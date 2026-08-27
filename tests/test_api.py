"""API tests. Skipped cleanly if FastAPI's test stack isn't installed."""

import pathlib
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402


@pytest.fixture()
def client():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_api_"))
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local")
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_render_returns_202_and_location(client):
    r = client.post("/v1/render", json={"prompt": "a red desert", "width": 768, "height": 768})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] in {"accepted", "running", "succeeded"}
    assert "Location" in r.headers
    # poll
    job = client.get(r.headers["Location"]).json()
    assert job["id"] == body["id"]


def test_known_problem_refusal_is_problem_json(client):
    """A REAL past failure makes the identical run be refused, as problem+json."""
    from comfyui_bridge.core.problems import OOM
    cont = client.app.state.container
    params = {"width": 1024, "height": 1024}
    cont.registry.record(cont.settings.host_id, "sd15-txt2img", params,
                         status="failed", problem=OOM, detail="CUDA out of memory")
    r = client.post("/v1/render", json={"prompt": "huge", "width": 1024, "height": 1024})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/reconciliation-refused")
    assert body["problem"] == OOM
    assert body["config"] == "1024x1024"   # only what the caller pinned


def test_validation_error_is_problem_json(client):
    r = client.post("/v1/render", json={"prompt": ""})  # min_length=1
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


def test_unknown_job_is_404_problem(client):
    r = client.get("/v1/jobs/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


def test_a_video_workflow_with_frames_and_fps_is_drivable_in_seconds():
    """No node holds a duration, but frames = duration x fps is exactly what the
    orchestrator does — so the form may offer seconds without lying."""
    from comfyui_bridge.core.orchestrator import derivable_params
    assert derivable_params("video", {"latent_batch": 1, "fps": 1}) == ["duration_s"]
    # Its own duration node: nothing to derive.
    assert derivable_params("video", {"latent_batch": 1, "fps": 1, "duration_s": 1}) == []
    # No frame rate: seconds cannot be converted, so they are not offered.
    assert derivable_params("video", {"latent_batch": 1}) == []
    # A still has no duration at all.
    assert derivable_params("image", {"latent_batch": 1, "fps": 1}) == []


def test_the_api_does_not_cap_what_the_workflow_allows():
    """A ceiling invented in the schema is a second source of truth: 17 frames
    were refused with 422 although the node declared a far larger maximum.
    Only nonsense (zero, negative) is refused here; ComfyUI validates the rest."""
    from comfyui_bridge.api.schemas import IntentIn
    ok = IntentIn(prompt="p", batch=64, width=8192, steps=400, fps=120, duration_s=180.0)
    assert (ok.batch, ok.width, ok.steps, ok.fps) == (64, 8192, 400, 120)
    import pytest as _pytest
    from pydantic import ValidationError
    for bad in ({"batch": 0}, {"width": 0}, {"steps": 0}, {"fps": 0}):
        with _pytest.raises(ValidationError):
            IntentIn(prompt="p", **bad)


def test_a_queued_job_is_told_it_is_waiting_behind_others():
    """A job pending behind another was shown "loading the model" — a different
    thing entirely. The queue position is read from ComfyUI's own queue."""
    from comfyui_bridge.api.main import _with_engine_state

    class _Container:
        pass

    import comfyui_bridge.api.main as main
    main._QUEUE_CACHE["at"], main._QUEUE_CACHE["value"] = 0.0, None
    snapshot = {"running": ["other"], "pending": ["mine"]}
    original = main._queue_snapshot
    main._queue_snapshot = lambda container: snapshot
    try:
        out = _with_engine_state(_Container(), {"status": "queued", "engine_ref": "mine"})
        assert out["engine_state"] == {"state": "pending", "ahead": 1}
        run = _with_engine_state(_Container(), {"status": "running", "engine_ref": "other"})
        assert run["engine_state"] == {"state": "running"}
    finally:
        main._queue_snapshot = original


def test_a_value_the_workflow_cannot_receive_is_named_not_swallowed():
    """A constraint on the frame count of a workflow that computes its frames
    from a duration changed nothing, and nothing said so."""
    from comfyui_bridge.core.intention import Constraint, ConstraintOp, RenderIntent
    from comfyui_bridge.core.orchestrator import Orchestrator
    from comfyui_bridge.core.workflow import WorkflowProfile

    class _Registry:
        def get_profile(self, name):
            return WorkflowProfile("wf", "video", accepts=("prompt", "fps", "duration_s",
                                                           "filename_prefix"))
        def names(self): return ["wf"]
        def default_name(self): return "wf"

    orch = Orchestrator(backend=None, reconciler=None, journal=None, store=None,
                        host_id="h", registry=_Registry())
    plan = orch.build_plan(RenderIntent(prompt="p", fps=24, duration_s=2.0,
                                        constraints=(Constraint("frames", ConstraintOp.LTE, 10),)))
    assert plan.params["latent_batch"] == 10       # the constraint did apply…
    assert "latent_batch" in plan.ignored          # …but this workflow takes no frame count


def test_a_run_stopped_on_request_is_not_recorded_against_the_workflow():
    """ComfyUI marks an interrupted run as an error. Read literally, Hermes
    blamed a graph that was running fine because a human pressed stop."""
    from comfyui_bridge.core.jobs import JobStatus, JobStore

    store = JobStore()
    job = store.create(kind="video")
    store.request_cancel(job.id)
    store.mark_failed(job.id, {"title": "ComfyUI workflow error"})
    assert store.get(job.id).status is JobStatus.CANCELLED

    other = store.create(kind="video")
    store.mark_failed(other.id, {"title": "ComfyUI workflow error"})
    assert store.get(other.id).status is JobStatus.FAILED


def test_starting_the_engine_never_raises_a_second_instance(client):
    """The console offers "start the engine" only when nothing answers, and the
    endpoint keeps the startup rule: attach to whatever is there, launch only a
    profile we manage. Here nothing answers and the test profile is 'cli', so
    the honest answer is a state, never a second server."""
    r = client.post("/v1/engine/start")
    assert r.status_code == 200
    state = r.json()
    assert state["started"] is False
    assert state["state"] in {"attached", "absent", "started"}


def test_the_announced_input_names_are_the_accepted_ones():
    """The catalogue announced `latent_batch`; the API read only `batch` and
    dropped the rest without a word — a trap for anything driving this service
    from outside the console."""
    from comfyui_bridge.core.intention import intent_fields

    fields = intent_fields(["latent_batch", "filename_prefix", "prompt", "width"])
    assert fields == ["label", "prompt", "width", "batch"] or set(fields) == {
        "batch", "label", "prompt", "width"}
    # Every announced name is a real field of the intent.
    from comfyui_bridge.core.intention import RenderIntent
    for f in fields:
        assert f in RenderIntent.__dataclass_fields__


def test_an_unknown_field_is_refused_not_swallowed(client):
    r = client.post("/v1/preview", json={"prompt": "x", "latent_batch": 4})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


def test_a_caller_can_name_its_own_output(client):
    """A larger flow must recognise ITS files: the job id dies with the process,
    the file name does not."""
    r = client.post("/v1/preview", json={"prompt": "x", "label": "plan-42"})
    assert r.json()["params"]["filename_prefix"] == "cortex/plan-42"
    # …and cannot be turned into a path of its own choosing.
    r = client.post("/v1/preview", json={"prompt": "x", "label": "../../etc/passwd"})
    assert r.json()["params"]["filename_prefix"] == "cortex/etcpasswd"

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
    # workflows_dir isolé : sans lui, un test qui importe un workflow l'écrit
    # dans le catalogue réel du poste et le laisse là.
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
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


def test_readiness_says_the_last_time_not_the_first(client):
    """Un souvenir se juge sur la DERNIÈRE fois qu'il s'est vérifié. La console
    n'affichait aucune date, et l'API annonçait la plus récente sous le nom
    « since » : on ne pouvait pas savoir si le problème datait d'une minute ou
    d'un mois, ni s'il avait été retenté depuis."""
    from comfyui_bridge.core.problems import OOM
    cont = client.app.state.container
    params = {"width": 1024, "height": 1024}
    for _ in range(3):
        cont.registry.record(cont.settings.host_id, "sd15-txt2img", params,
                             status="failed", problem=OOM, detail="CUDA out of memory")
    d = client.get("/v1/workflows/sd15-txt2img/readiness").json()
    assert d["runnable"] is False and d["problem"] == OOM
    assert d["occurrences"] == 3
    assert d["last_seen"] >= d["first_seen"]      # la dernière, pas la première
    assert "since" not in d                      # le nom qui mentait a disparu
    assert d["revisions"] == []                  # rien n'a encore été levé


def test_readiness_shows_when_a_memory_was_lifted_and_by_what(client):
    """Hermes n'efface plus : il révise et date. Taire ces moments dans l'API
    reviendrait à les effacer pour le lecteur."""
    from comfyui_bridge.core.problems import OOM
    cont = client.app.state.container
    params = {"width": 1024, "height": 1024}
    cont.registry.record(cont.settings.host_id, "sd15-txt2img", params,
                         status="failed", problem=OOM, detail="CUDA out of memory")
    cont.registry.record(cont.settings.host_id, "sd15-txt2img", params,
                         status="succeeded", duration_s=9.0)
    d = client.get("/v1/workflows/sd15-txt2img/readiness").json()
    assert d["runnable"] is True                       # le souvenir ne tient plus
    levee = d["revisions"][0]
    assert levee["problem"] == OOM and levee["revised_at"]
    assert "réussite" in levee["revised_by"]
    # …et la même mémoire, lue par son propre point d'entrée.
    p = client.get("/v1/hermes/problems?workflow=sd15-txt2img").json()
    assert p["problems"] == [] and p["revised"][0]["revised_by"] == levee["revised_by"]


def test_validation_error_is_problem_json(client):
    r = client.post("/v1/render", json={"prompt": "x", "width": 0})   # ge=1
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")


def test_a_workflow_without_text_needs_no_prompt(client):
    """Une mise à l'échelle ou une interpolation n'a pas de texte : exiger un
    prompt obligeait à inventer "(sans prompt)", qui repartait ensuite dans les
    paramètres non transmis."""
    r = client.post("/v1/preview", json={"workflow": "sd15-txt2img", "width": 512})
    assert r.status_code == 200
    # Rien n'est injecté à la place : le graphe garde le texte de son auteur.
    assert "prompt" not in r.json()["params"]


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
    assert "batch" in plan.ignored     # …under the name the caller actually sent


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


def test_every_way_in_weighs_the_run_not_just_the_http_api():
    """The load was computed at one entry point only: a run launched from the
    CLI was journalled with no load and taught the estimate nothing. The core
    now asks the backend for it just before recording, whatever the way in."""
    from comfyui_bridge.core.orchestrator import Orchestrator
    from comfyui_bridge.core.plan import ExecutionPlan

    class _Backend:
        def load_of(self, plan):
            return 42.0, 7

    orch = Orchestrator(backend=_Backend(), reconciler=None, journal=None, store=None,
                        host_id="h", registry=None)
    plan = ExecutionPlan(intent=None, params={"width": 512}, workflow="wf")
    orch._weigh(plan)
    assert (plan.work, plan.work_model) == (42.0, 7)

    # A load already known is not recomputed…
    known = ExecutionPlan(intent=None, params={}, workflow="wf", work=1.0, work_model=2)
    orch._weigh(known)
    assert (known.work, known.work_model) == (1.0, 2)

    # …and a backend that cannot weigh leaves it unknown rather than invented.
    class _Blind:
        pass
    blind = Orchestrator(backend=_Blind(), reconciler=None, journal=None, store=None,
                         host_id="h", registry=None)
    unknown = ExecutionPlan(intent=None, params={}, workflow="wf")
    blind._weigh(unknown)
    assert unknown.work is None


def test_a_re_analysis_says_what_it_re_measured():
    """A re-extraction answering "done" teaches nothing: what matters is which
    inputs appeared, which vanished, and which now drive another node."""
    from comfyui_bridge.adapter.mapping import Binding
    from comfyui_bridge.api.main import _rewiring

    class _Spec:
        kind = "audio"
        bindings = {"prompt": Binding("2", "text"),
                    "duration_s": Binding("10", "value"),   # a bougé de nœud
                    "latent_batch": Binding("4", "batch_size")}

    avant = {"kind": "audio",
             "bindings": {"prompt": ("2", "text"),
                          "duration_s": ("4", "seconds"),
                          "negative_prompt": ("3", "text"),   # a disparu
                          "steps": ("5", "steps")}}           # a disparu
    diff = _rewiring(avant, _Spec())
    assert diff["removed"] == ["negative_prompt", "steps"]
    assert diff["added"] == ["batch"]                          # nom API, pas interne
    assert diff["moved"] == [{"field": "duration_s", "from": "4.seconds", "to": "10.value"}]
    assert diff["kind_changed"] is False

    # Première analyse : tout est nouveau, et c'est dit comme tel.
    premiere = _rewiring(None, _Spec())
    assert premiere["first_analysis"] is True
    assert premiere["added"] == ["batch", "duration_s", "prompt"]


def test_freshness_is_read_once_and_says_why(tmp_path):
    """La même question — cet extrait est-il à jour ? — se pose au catalogue, au
    lancement et à la gestion : une seule lecture, sinon elles divergent."""
    from comfyui_bridge.adapter.freshness import forget, source_state

    class _Spec:
        name, source, source_hash, titles = "wf", "wf.json", "abc", {"1": "Duration"}

    class _Engine:
        def __init__(self, doc): self.doc = doc
        def get_saved_workflow(self, name): return self.doc

    forget()
    inchangé = source_state(_Engine({"nodes": []}), _Spec(), now=0.0)
    assert inchangé["fresh"] in (True, False)      # dépend du hash, pas d'exception
    forget()

    class _Muet:
        def get_saved_workflow(self, name): raise OSError("moteur muet")
    # Un moteur muet ne fait pas conclure à une péremption.
    assert source_state(_Muet(), _Spec(), now=0.0)["fresh"] is True


def test_the_updates_route_is_not_eaten_by_the_name_route(client):
    """Déclarée après "/v1/workflows/{name}", elle répondait 400 : "updates"
    était lu comme un nom de workflow."""
    r = client.get("/v1/workflows/updates")
    assert r.status_code == 200
    assert set(r.json()) == {"count", "updates"}


def test_the_re_analysis_also_reports_what_changed_on_the_way_OUT():
    """Un workflow passé de SaveAudio à SaveAudioMP3 changeait le format livré
    sans que rien ne l'annonce : le diff ne regardait que les entrées."""
    from comfyui_bridge.adapter.mapping import Binding
    from comfyui_bridge.api.main import _rewiring

    class _Spec:
        kind = "audio"
        bindings = {"prompt": Binding("2", "text")}

    avant = {"kind": "audio", "bindings": {"prompt": ("2", "text")},
             "outputs": [{"node": "7", "class_type": "SaveAudio"}]}
    apres = [{"node": "7", "class_type": "SaveAudioMP3", "display_name": "Save Audio (MP3)"}]

    diff = _rewiring(avant, _Spec(), apres)
    assert diff["added"] == [] and diff["removed"] == []      # les IN n'ont pas bougé…
    assert diff["delivery_changed"] == {"from": ["7.SaveAudio"], "to": ["7.SaveAudioMP3"]}
    assert diff["delivers"] == ["Save Audio (MP3)"]

    # Une sortie inchangée ne crie pas au changement.
    inchange = _rewiring(avant, _Spec(), [{"node": "7", "class_type": "SaveAudio"}])
    assert inchange["delivery_changed"] is False


def test_a_source_deleted_from_comfyui_is_not_passed_over_in_silence():
    """Supprimée dans ComfyUI, la source laissait le catalogue annoncer
    `source_changed: False` — un workflow dont plus rien ne répond."""
    import urllib.error

    from comfyui_bridge.adapter.freshness import forget, source_state

    class _Spec:
        name, source, source_hash, titles = "wf", "wf.json", "abc", {}

    class _Absent:
        def get_saved_workflow(self, name):
            raise urllib.error.HTTPError("u", 404, "Not Found", None, None)

    forget()
    state = source_state(_Absent(), _Spec(), now=0.0)
    assert state["fresh"] is False and state["missing"] is True
    forget()


GRAPHE_MINIMAL = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "modele.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512,
                                                       "batch_size": 1}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "cfg": 7.0,
                                               "model": ["1", 0], "positive": ["2", 0],
                                               "negative": ["2", 0], "latent_image": ["3", 0]}},
    "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["4", 0]}},
}


def test_importing_a_workflow_actually_works(client):
    """La route n'était couverte par aucun test : elle référençait deux noms
    inexistants et échouait donc à CHAQUE appel — après avoir enregistré. Un
    appelant recevait une erreur pour un import qui avait eu lieu."""
    r = client.post("/v1/workflows", json={"name": "importe", "workflow": GRAPHE_MINIMAL})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "importe"
    assert "prompt" in body["intent_fields"]
    # …et il est réellement appelable ensuite.
    assert "importe" in client.get("/v1/workflows").json()["workflows"]
    assert client.post("/v1/preview", json={"workflow": "importe", "prompt": "x"}).status_code == 200


def test_re_importing_reports_what_changed(client):
    """Une ré-ingestion doit dire ce qu'elle a re-câblé, comme l'extraction."""
    client.post("/v1/workflows", json={"name": "reimporte", "workflow": GRAPHE_MINIMAL})
    assert client.post("/v1/workflows",
                       json={"name": "reimporte", "workflow": GRAPHE_MINIMAL}
                       ).json()["changes"]["first_analysis"] is False

    sans_negatif = {k: dict(v, inputs=dict(v["inputs"])) for k, v in GRAPHE_MINIMAL.items()}
    del sans_negatif["3"]["inputs"]["batch_size"]
    diff = client.post("/v1/workflows",
                       json={"name": "reimporte", "workflow": sans_negatif}).json()["changes"]
    assert "batch" in diff["removed"]


def test_both_ingestion_doors_answer_the_same_shape(client):
    """Les deux routes d'ingestion ont divergé jusqu'à ce que l'une référence des
    variables propres à l'autre : leur réponse doit rester de même forme."""
    posted = client.post("/v1/workflows",
                         json={"name": "forme", "workflow": GRAPHE_MINIMAL}).json()
    for cle in ("name", "kind", "intent_fields", "bindings", "dependencies",
                "analysis", "delivers", "changes"):
        assert cle in posted, cle

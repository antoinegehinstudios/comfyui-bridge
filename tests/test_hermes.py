"""Hermes: remembers real problems, refuses to re-meet them. No invented rules."""

import pytest

from comfyui_bridge.core import problems as P
from comfyui_bridge.core.intention import RenderIntent
from comfyui_bridge.core.plan import ExecutionPlan
from comfyui_bridge.hermes.reconciler import HermesReconciler
from comfyui_bridge.hermes.registry import ProblemRegistry


def plan(w=768, h=768, b=1, workflow="wf"):
    params = {"prompt": "x", "width": w, "height": h, "latent_batch": b, "steps": 20}
    return ExecutionPlan(RenderIntent(prompt="x"), params, kind="image", workflow=workflow)


@pytest.fixture()
def reg(tmp_path):
    return ProblemRegistry(tmp_path / "h.sqlite3", scope="test")


def test_empty_registry_accepts(reg):
    """Nothing known = nothing to object. An empty registry never invents a limit."""
    v = HermesReconciler(reg, host="h").reconcile(plan())
    assert v.accepted and v.known_problems == []


def test_refuses_config_that_already_failed(reg):
    reg.record("h", "wf", plan(1024, 1024).params, status="failed",
               problem=P.OOM, detail="CUDA out of memory")
    hermes = HermesReconciler(reg, host="h")
    refused = hermes.reconcile(plan(1024, 1024))
    assert not refused.accepted and refused.problem == P.OOM
    assert "déjà rencontré" in refused.reason
    # a different configuration is untouched by that memory
    assert hermes.reconcile(plan(512, 512)).accepted


def test_later_success_clears_the_past_problem(reg):
    reg.record("h", "wf", plan().params, status="failed", problem=P.OOM, detail="oom")
    reg.record("h", "wf", plan().params, status="succeeded")
    assert HermesReconciler(reg, host="h").reconcile(plan()).accepted


def test_non_blocking_problem_is_context_not_refusal(reg):
    reg.record("h", "wf", plan().params, status="failed", problem=P.TIMEOUT, detail="timed out")
    v = HermesReconciler(reg, host="h").reconcile(plan())
    assert v.accepted  # a timeout may not recur — do not refuse on it


def test_remembers_comfyui_missing_model_verdict(reg):
    """Hermes does not re-check dependencies — it remembers ComfyUI's own verdict."""
    reg.record("h", "wf", plan().params, status="failed", problem=P.MISSING_MODEL,
               detail="ComfyUI rejected the graph (HTTP 400): value_not_in_list ckpt_name")
    v = HermesReconciler(reg, host="h").reconcile(plan())
    assert not v.accepted and v.problem == P.MISSING_MODEL


def test_scope_isolates_knowledge(tmp_path):
    """Same DB file, two pipelines: a problem learned in one must not leak."""
    db = tmp_path / "shared.sqlite3"
    a, b = ProblemRegistry(db, scope="A"), ProblemRegistry(db, scope="B")
    a.record("h", "wf", plan().params, status="failed", problem=P.OOM, detail="oom")
    assert a.problems_for("h", "wf") != []
    assert b.problems_for("h", "wf") == []


@pytest.mark.parametrize("text,expected", [
    ("torch.cuda.OutOfMemoryError: CUDA out of memory", P.OOM),
    ("Code Integrity determined ... did not meet the Enterprise signing level", P.BLOCKED_BY_OS),
    ("Value not in list: ckpt_name: 'x.safetensors' not in [...]", P.MISSING_MODEL),
    ("unreachable: connection refused", P.UNREACHABLE),
    ("ComfyUI timed out after 300s", P.TIMEOUT),
    ("something weird happened", P.UNKNOWN),
])
def test_classify_real_messages(text, expected):
    assert P.classify(text) == expected


def test_no_experience_means_no_estimate(reg):
    """Nothing measured = nothing announced. An invented duration would lie."""
    assert reg.estimate_duration("h", "wf", config="768x768") is None


def test_estimate_is_the_median_of_past_successes(reg):
    for d in (100.0, 120.0, 500.0):        # 500 = one slow outlier
        reg.record("h", "wf", plan().params, status="succeeded", duration_s=d)
    est = reg.estimate_duration("h", "wf", config=plan().config)
    assert est["seconds"] == 120           # median, not the average (240)
    assert est["samples"] == 3 and est["basis"] == "same-config"
    assert est["min"] == 100 and est["max"] == 500


def test_failures_and_other_configs_do_not_pollute_the_estimate(reg):
    reg.record("h", "wf", plan().params, status="failed", problem=P.OOM, detail="x", duration_s=9.0)
    reg.record("h", "wf", plan(1024, 1024).params, status="succeeded", duration_s=800.0)
    # nothing successful for THIS config -> falls back, and says so
    est = reg.estimate_duration("h", "wf", config=plan().config)
    assert est["basis"] == "same-workflow" and est["seconds"] == 800


def test_engine_measure_is_preferred_over_wall_clock():
    """A run that waited 30 min in the queue and computed for 2 min must be
    remembered as 2 min. Measuring from our side recorded 1999 s for a run the
    engine did in 121 s."""
    from comfyui_bridge.adapter.comfy_http import _execution_seconds
    entry = {"status": {"messages": [
        ["execution_start", {"timestamp": 1787775455012}],
        ["execution_success", {"timestamp": 1787775576224}],
    ]}}
    assert round(_execution_seconds(entry)) == 121


def test_no_engine_measure_means_no_duration_recorded():
    """Rather than a wall-clock number polluted by queue time, record nothing."""
    from comfyui_bridge.adapter.comfy_http import _execution_seconds
    assert _execution_seconds({"status": {"messages": []}}) is None
    assert _execution_seconds({}) is None


def test_estimate_scales_with_the_actual_work(reg):
    """Resolution and frame count must change the answer: 704x448 for 2 s is
    not the same job as 1280x720 for 5 s, even on the same workflow."""
    # one measured run: 100 units of work took 200 s -> 2 s per unit
    reg.record("h", "wf", plan().params, status="succeeded", duration_s=200.0, work=100.0)
    small = reg.estimate_duration("h", "wf", work=50.0)
    big = reg.estimate_duration("h", "wf", work=400.0)
    assert small["seconds"] == 100 and big["seconds"] == 800
    assert small["basis"] == "work-rate"


def test_work_rate_uses_the_median_of_observed_rates(reg):
    for dur, wk in ((100.0, 100.0), (300.0, 100.0), (1000.0, 100.0)):
        reg.record("h", "wf", plan().params, status="succeeded", duration_s=dur, work=wk)
    est = reg.estimate_duration("h", "wf", work=100.0)
    assert est["seconds"] == 300           # median rate, not dragged by the outlier
    assert est["min"] == 100 and est["max"] == 1000


def test_readiness_and_the_reconciler_read_the_memory_the_same_way(tmp_path):
    """The UI said "cannot run here" while a run would in fact be accepted:
    readiness looked at every configuration and ignored later successes, the
    reconciler did not. One reading now serves both."""
    from comfyui_bridge.core.cost import config_fingerprint
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    small = {"width": 512, "height": 512}
    big = {"width": 2048, "height": 2048}
    reg.record("h", "wf", big, status="failed", problem="oom", detail="out of memory")
    # The workflow as a whole is held against: that configuration still stands.
    assert [p["problem"] for p in reg.blocking_problems("h", "wf")] == ["oom"]
    # …but another configuration carries nothing.
    assert reg.blocking_problems("h", "wf", config_fingerprint(small)) == []
    # A later success on the same configuration clears the memory.
    reg.record("h", "wf", big, status="succeeded", duration_s=10.0)
    assert reg.blocking_problems("h", "wf") == []


def test_an_absent_model_blocks_every_configuration(tmp_path):
    """Changing the resolution does not install a missing model: retrying at
    another size only burns another rejection. An out-of-memory is different —
    it is precisely about how much was asked for."""
    from comfyui_bridge.core.cost import config_fingerprint
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    big = {"width": 2048, "height": 2048}
    small = {"width": 512, "height": 512}
    reg.record("h", "wf", big, status="failed", problem="missing-model", detail="not in list")
    assert [p["problem"] for p in reg.blocking_problems("h", "wf", config_fingerprint(small))] \
        == ["missing-model"]
    reg.record("h", "oomwf", big, status="failed", problem="oom", detail="out of memory")
    assert reg.blocking_problems("h", "oomwf", config_fingerprint(small)) == []


def test_the_estimate_accounts_for_the_fixed_cost_of_a_run(tmp_path):
    """Measured on this host: 0.95 units took 71 s and 15.1 units took 126 s.
    A proportional rule announced 8 s for the small one — the model has to be
    loaded whatever the size, so the fit is affine."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    for width, work, seconds in ((352, 0.95, 70.9), (704, 15.14, 126.2)):
        reg.record("h", "wf", {"width": width, "height": 224}, status="succeeded",
                   duration_s=seconds, work=work)
    small = reg.estimate_duration("h", "wf", work=0.95)
    assert small["basis"] == "work-fit"
    assert 60 <= small["seconds"] <= 80          # nowhere near a proportional 8 s
    assert small["setup_s"] > 0                  # the fixed cost is named
    big = reg.estimate_duration("h", "wf", work=30.0)
    assert big["seconds"] > small["seconds"]     # and size still moves the answer


def test_a_single_measured_size_cannot_pretend_to_separate_setup_from_work(tmp_path):
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 704, "height": 448}, status="succeeded",
               duration_s=126.2, work=15.14)
    est = reg.estimate_duration("h", "wf", work=15.14)
    assert est["basis"] == "work-rate"


def test_cancelling_a_run_is_not_recorded_as_a_workflow_problem():
    """Clearing the queue from the console makes the engine forget the run. Read
    as a failure of the workflow it would block a configuration the user never
    had trouble with."""
    from comfyui_bridge.core import problems as P
    kind = P.classify("le moteur ne connaît plus ce run (ComfyUI a redémarré ou l'a perdu) — relancer")
    assert kind == P.LOST
    assert kind not in P.BLOCKING

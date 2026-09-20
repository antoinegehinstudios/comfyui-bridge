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


def test_a_missing_dependency_warns_but_never_refuses(reg):
    """Le moteur refuse lui-même une dépendance absente, avant tout calcul : la
    retenter ne coûte rien. Refuser d'avance interdisait le seul run qui aurait
    prouvé la réparation — un workflow réparé restait bloqué pour une cause
    disparue."""
    reg.record("h", "wf", plan().params, status="failed", problem=P.MISSING_MODEL,
               detail="ComfyUI rejected the graph (HTTP 400): value_not_in_list ckpt_name")
    v = HermesReconciler(reg, host="h").reconcile(plan())
    assert v.accepted                                   # on laisse essayer…
    assert any(P.MISSING_MODEL in k for k in v.known_problems)   # …en le disant
    assert reg.blocking_problems("h", "wf") == []
    assert [w["problem"] for w in reg.known_warnings("h", "wf")] == [P.MISSING_MODEL]


def test_an_out_of_memory_still_refuses(reg):
    """Celui-là coûte des minutes et peut emporter le moteur."""
    reg.record("h", "wf", plan().params, status="failed", problem=P.OOM, detail="out of memory")
    v = HermesReconciler(reg, host="h").reconcile(plan())
    assert not v.accepted and v.problem == P.OOM


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
    # Two measured sizes: 100 units took 200 s, 200 units took 300 s.
    # -> 100 s of setup + 1 s per unit, both read from the measurements.
    reg.record("h", "wf", {"width": 100}, status="succeeded", duration_s=200.0,
               work=100.0, work_model=2)
    reg.record("h", "wf", {"width": 200}, status="succeeded", duration_s=300.0,
               work=200.0, work_model=2)
    small = reg.estimate_duration("h", "wf", work=50.0, work_model=2)
    big = reg.estimate_duration("h", "wf", work=400.0, work_model=2)
    assert small["basis"] == "work-fit" and big["basis"] == "work-fit"
    assert small["seconds"] == 150 and big["seconds"] == 500
    assert big["seconds"] > small["seconds"]


def test_one_measured_size_falls_back_to_a_median_no_outlier_can_drag(reg):
    """Same size, three very different durations: with a single size there is
    nothing to fit, and the answer must be the middle one — not the average,
    which the 1000 s outlier would carry away."""
    for dur in (100.0, 300.0, 1000.0):
        reg.record("h", "wf", plan().params, status="succeeded", duration_s=dur,
                   work=100.0, work_model=2)
    est = reg.estimate_duration("h", "wf", work=100.0, work_model=2,
                                config=plan().config)
    assert est["basis"] == "same-config"
    assert est["seconds"] == 300
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


def test_a_refusal_applies_to_the_configuration_that_met_it(tmp_path):
    """Un dépassement mémoire parle de la taille demandée : une autre taille
    reste tentable. Un blocage du système, lui, vaut à toute résolution."""
    from comfyui_bridge.core.cost import config_fingerprint
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    grand = {"width": 2048, "height": 2048}
    petit = {"width": 512, "height": 512}

    reg.record("h", "oomwf", grand, status="failed", problem="oom", detail="out of memory")
    assert reg.blocking_problems("h", "oomwf", config_fingerprint(grand)) != []
    assert reg.blocking_problems("h", "oomwf", config_fingerprint(petit)) == []

    reg.record("h", "oswf", grand, status="failed", problem="blocked-by-os",
               detail="code integrity")
    assert [p["problem"]
            for p in reg.blocking_problems("h", "oswf", config_fingerprint(petit))]         == ["blocked-by-os"]


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
    """Applied proportionally, one measurement announced 29 min for a run that
    takes 2: the fixed cost of a run is invisible from a single size."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 352, "height": 224}, status="succeeded",
               duration_s=110.0, work=10.4, work_model=2)
    est = reg.estimate_duration("h", "wf", work=166.5, work_model=2)
    assert est is None or est["basis"] not in {"work-fit", "work-rate"}


def test_cancelling_a_run_is_not_recorded_as_a_workflow_problem():
    """Clearing the queue from the console makes the engine forget the run. Read
    as a failure of the workflow it would block a configuration the user never
    had trouble with."""
    from comfyui_bridge.core import problems as P
    kind = P.classify("le moteur ne connaît plus ce run (ComfyUI a redémarré ou l'a perdu) — relancer")
    assert kind == P.LOST
    assert kind not in P.BLOCKING


def test_two_ways_of_counting_work_are_never_fitted_together(tmp_path):
    """Counting the LTX passes changed the scale of `work` elevenfold. Fitting
    old and new measurements on one line would have described neither."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 512}, status="succeeded", duration_s=70.0,
               work=1.0, work_model=1)
    reg.record("h", "wf", {"width": 704}, status="succeeded", duration_s=126.0,
               work=15.0, work_model=1)
    # The new barème has nothing measured yet: no work-based answer at all.
    fresh = reg.estimate_duration("h", "wf", work=11.0, work_model=2)
    assert fresh is None or fresh["basis"] not in {"work-fit", "work-rate"}
    # The old one still fits its own points.
    old = reg.estimate_duration("h", "wf", work=8.0, work_model=1)
    assert old["basis"] == "work-fit"


def test_loading_the_model_is_measured_apart_so_the_sizes_line_up(tmp_path):
    """Measured on this host: a cold run of load 10 took 110 s, a warm run of
    load 166 took 73 s. Fitted on raw durations, a bigger job looked cheaper and
    no line could be drawn. Taking the measured loading time out first, the
    compute times line up and the size drives the answer again."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 352}, status="succeeded",
               duration_s=110.0, setup_s=100.0, work=10.0, work_model=2)   # froid
    reg.record("h", "wf", {"width": 704}, status="succeeded",
               duration_s=73.0, setup_s=6.0, work=166.0, work_model=2)     # à chaud
    est = reg.estimate_duration("h", "wf", work=166.0, work_model=2)
    assert est["basis"] == "work-fit" and est["setup_measured"] is True
    petit = reg.estimate_duration("h", "wf", work=10.0, work_model=2)
    assert est["seconds"] > petit["seconds"]        # la taille compte de nouveau
    assert est["min"] <= est["seconds"] <= est["max"]  # l'écart de mise en route est dit
    assert est["min"] < est["max"]                     # …et il n'est pas gommé


def test_the_compute_floor_is_kept_not_only_the_slope(tmp_path):
    """A graph does more than its sampling passes: 46 s of compute for a load of
    10 and 50 s for a load of 166. Keeping only the slope announced 21 s for a
    run measured at 67."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 352}, status="succeeded",
               duration_s=67.3, setup_s=21.0, work=10.4, work_model=2)
    reg.record("h", "wf", {"width": 704}, status="succeeded",
               duration_s=69.7, setup_s=20.1, work=166.5, work_model=2)
    for work, mesuré in ((10.4, 67.3), (166.5, 69.7)):
        est = reg.estimate_duration("h", "wf", work=work, work_model=2)
        assert abs(est["seconds"] - mesuré) < 3, (work, est["seconds"], mesuré)


def test_les_durees_mesurees_se_relisent_avec_leur_etiquette(reg):
    """Ce qu'il faut pour estimer une chaîne par ses propres livraisons : les
    durées réussies, les plus récentes d'abord, chacune avec l'étiquette de sa
    configuration — un échec, ou un autre workflow, n'y sont pas."""
    reg.record("h", "chaine", {"width": 720, "height": 1280, "duration_s": 5.0},
               status="succeeded", duration_s=720.0)
    reg.record("h", "chaine", {"width": 720, "height": 1280, "duration_s": 8.0},
               status="succeeded", duration_s=1234.0)
    reg.record("h", "chaine", {"width": 720, "height": 1280, "duration_s": 8.0},
               status="failed", problem=P.OOM, detail="x", duration_s=9.0)
    reg.record("h", "autre", {"duration_s": 8.0}, status="succeeded", duration_s=1.0)
    lues = reg.durees("h", "chaine")
    assert [(d["config"], d["duration_s"]) for d in lues] == [("720x1280-8s", 1234.0),
                                                              ("720x1280-5s", 720.0)]
    assert all(d["ts"] for d in lues)
    assert reg.durees("h", "jamais-vu") == []


def test_un_ajustement_public_sur_des_tailles_quelconques():
    """La droite du registre, publiée : une chaîne se mesure en runs, pas en
    travail de graphe — deux tailles au moins, une pente à l'endroit."""
    from comfyui_bridge.hermes.registry import ajuster
    points = [(2.0, 720.0), (2.0, 700.0), (4.0, 1230.0), (4.0, 1240.0)]
    a = ajuster(points, 12.0)
    assert a["samples"] == 4 and a["per_unit_s"] > 200 and a["setup_s"] >= 0
    # 12 runs : la droite passe par ≈ 710 à 2 et ≈ 1235 à 4 → ≈ 3300 s.
    assert 3100 <= a["seconds"] <= 3500 and a["min"] <= a["seconds"] <= a["max"]
    assert ajuster([(2.0, 720.0), (2.0, 700.0)], 12.0) is None       # une seule taille
    assert ajuster([(2.0, 1000.0), (4.0, 500.0)], 6.0) is None         # pente à l'envers


def test_une_empreinte_se_relit():
    """L'étiquette d'une livraison de chaîne est tout ce que la mémoire en
    garde : pour comparer une demande neuve à d'autres durées mesurées, il
    faut la relire — et ce qui ne se relit pas rend vide, jamais inventé."""
    from comfyui_bridge.core.cost import config_fingerprint, config_lue
    for params in ({"width": 720, "height": 1280, "duration_s": 5.0},
                   {"duration_s": 6.0}, {"width": 704, "latent_batch": 16},
                   {"duration_s": 6.0, "latent_batch": 1}, {"height": 512}):
        lu = config_lue(config_fingerprint(params))
        assert lu == params, (params, lu)
    assert config_lue("workflow-default") == {}
    assert config_lue("n'importe quoi") == {}


def test_a_pinned_duration_is_part_of_the_configuration():
    """Two audio runs of 6 s and 30 s were both labelled "workflow-default":
    the memory of one then answered for the other."""
    from comfyui_bridge.core.cost import config_fingerprint
    assert config_fingerprint({"duration_s": 6.0}) == "6s"
    assert config_fingerprint({"duration_s": 30.0}) == "30s"
    # A frame count already says it: no need to say it twice.
    assert config_fingerprint({"duration_s": 2.0, "latent_batch": 48}) == "x48"
    # A batch of 1 carries no quantity: the duration must still show.
    assert config_fingerprint({"duration_s": 6.0, "latent_batch": 1}) == "x1-6s"
    assert config_fingerprint({}) == "workflow-default"


def test_a_standing_refusal_can_always_be_overridden():
    """Un souvenir qui refuse interdit aussi le run qui prouverait la
    réparation. Ce qui bloque encore coûte cher, donc le refus reste — mais une
    porte explicite doit exister, sinon la mémoire ne peut jamais être démentie."""
    from comfyui_bridge.core.errors import HardwareReconciliationError
    from comfyui_bridge.core.intention import RenderIntent
    from comfyui_bridge.core.orchestrator import Orchestrator
    from comfyui_bridge.core.plan import Reconciliation
    from comfyui_bridge.core.workflow import WorkflowProfile

    class _Registre:
        def get_profile(self, name): return WorkflowProfile("wf", "image", accepts=("prompt",))
        def names(self): return ["wf"]
        def default_name(self): return "wf"

    class _Refuse:
        def reconcile(self, plan):
            return Reconciliation(False, plan, "oom déjà rencontré", plan.config, [], problem="oom")

    orch = Orchestrator(backend=None, reconciler=_Refuse(), journal=None, store=None,
                        host_id="h", registry=_Registre())
    intention = RenderIntent(prompt="p")

    import pytest
    with pytest.raises(HardwareReconciliationError):
        orch.plan_and_reconcile(intention)
    assert orch.plan_and_reconcile(intention, force=True).workflow == "wf"


def test_forgetting_problems_needs_the_definition_to_have_changed(tmp_path):
    """Réenregistrer un workflow À L'IDENTIQUE n'apprend rien de neuf : effacer
    la mémoire à chaque ingestion la viderait sans raison."""
    from comfyui_bridge.adapter.comfyui_client import source_hash
    graphe = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    identique = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    modifie = {"1": {"class_type": "KSampler", "inputs": {"steps": 8}}}
    assert source_hash(graphe) == source_hash(identique)
    assert source_hash(graphe) != source_hash(modifie)


def test_re_registering_revises_the_memory_and_keeps_the_moment(tmp_path):
    """Les mesures de durée servent l'estimation : les effacer avec les
    problèmes ferait tout réapprendre à chaque ingestion.

    Et le problème lui-même n'est plus effacé : effacer emportait avec lui le
    moment où il a cessé d'être vrai, et ce qui l'a changé. La ligne reste,
    marquée et datée."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    reg.record("h", "wf", {"width": 512}, status="succeeded", duration_s=30.0,
               work=5.0, work_model=2)
    reg.record("h", "wf", {"width": 512}, status="failed", problem="oom", detail="oom")
    levee = reg.revise("h", "wf", "workflow réenregistré avec un graphe différent")
    assert levee["revised"] == 1 and levee["at"]
    assert reg.problems_for("h", "wf") == []           # ne parle plus au présent
    lignes = reg.recent("h")
    assert [l["status"] for l in lignes] == ["failed", "succeeded"]
    assert lignes[0]["revised_ts"] == levee["at"]      # le moment est gardé
    assert lignes[0]["revised_by"] == "workflow réenregistré avec un graphe différent"
    assert lignes[1]["duration_s"] == 30.0             # la mesure survit


def test_a_success_revises_what_it_denies_at_the_moment_it_happens(tmp_path):
    """L'ajustement n'est pas laissé à la bonne volonté d'un appelant.

    Il se fait dans l'écriture même de l'issue : un souvenir qui survit à ce qui
    l'a démenti est un mensonge, et personne ne pense à le lever après coup."""
    from comfyui_bridge.core import problems as P
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    grand = {"width": 2048, "height": 2048}
    reg.record("h", "wf", grand, status="failed", problem=P.OOM, detail="out of memory")
    assert [p["problem"] for p in reg.problems_for("h", "wf")] == [P.OOM]

    reg.record("h", "wf", grand, status="succeeded", duration_s=12.0)
    assert reg.problems_for("h", "wf") == []
    levees = reg.revisions("h", "wf")
    assert len(levees) == 1
    assert levees[0]["problem"] == P.OOM
    assert levees[0]["revised_ts"] >= levees[0]["ts"]        # le moment, daté
    assert "réussite" in levees[0]["revised_by"]
    # Ce qui est arrivé reste écrit : on révise un souvenir, on ne réécrit pas
    # le passé.
    assert [l["status"] for l in reg.recent("h")] == ["succeeded", "failed"]


def test_a_new_delivery_mechanism_revises_what_only_it_could_have_caused(tmp_path):
    """MESURÉ : un maillage écrit par le moteur mais non ramassé par la
    passerelle (la clé `3d` n'était pas lue) a été retenu contre un workflow qui
    marchait. Le mécanisme de livraison a changé — le verdict d'un processus qui
    n'existe plus ne se transmet pas au suivant.

    Ce qui ne doit RIEN à ce mécanisme n'est pas touché : une mémoire saturée
    reste une mémoire saturée."""
    from comfyui_bridge.core import problems as P
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    params = {"width": 512, "height": 512}
    # Un souvenir d'avant que le mécanisme soit noté (il porte NULL)…
    reg.record("h", "maillage", params, status="failed", problem=P.UNKNOWN,
               detail="ComfyUI finished but produced no media")
    # …et un souvenir écrit par le mécanisme précédent.
    reg.record("h", "maillage2", params, status="failed", problem=P.UNKNOWN,
               detail="ComfyUI finished but produced no media", mechanism=1)
    reg.record("h", "gros", params, status="failed", problem=P.OOM,
               detail="out of memory", mechanism=1)

    # Le souvenir d'avant a déjà été revu au moment où un run est passé par le
    # mécanisme 1 : c'était déjà un autre processus que celui qui l'avait écrit.
    assert reg.problems_for("h", "maillage") == []
    revu = reg.revisions("h", "maillage")[0]
    assert "mécanisme" in revu["revised_by"] and "v1" in revu["revised_by"]

    # Un run livré par le mécanisme suivant, sur un tout autre workflow : c'est
    # LA LIVRAISON qui a changé, pas ce workflow-là.
    reg.record("h", "autre", params, status="succeeded", duration_s=3.0, mechanism=2)

    assert reg.problems_for("h", "maillage2") == []
    levee = reg.revisions("h", "maillage2")[0]
    assert "mécanisme" in levee["revised_by"] and "v2" in levee["revised_by"]
    assert levee["revised_ts"] >= levee["ts"]
    # Le premier souvenir garde SA date de levée : on ne révise qu'une fois, au
    # moment où c'est arrivé.
    assert reg.revisions("h", "maillage")[0]["revised_ts"] == revu["revised_ts"]
    # Le dépassement mémoire ne doit rien au mécanisme de livraison : intact.
    assert [p["problem"] for p in reg.problems_for("h", "gros")] == [P.OOM]


def test_an_estimate_is_a_median_so_it_carries_no_date(tmp_path):
    """La dernière date compte pour un ÉTAT — « ce problème s'est vérifié le… ».
    Une durée estimée n'est pas un moment : c'est une médiane de runs. Lui
    coller une date ferait passer un calcul pour un fait daté."""
    from comfyui_bridge.hermes.registry import ProblemRegistry
    reg = ProblemRegistry(tmp_path / "h.sqlite3")
    for duree in (100.0, 200.0):
        reg.record("h", "wf", {"width": 512}, status="succeeded", duration_s=duree)
    estimation = reg.estimate_duration("h", "wf")
    assert estimation["samples"] == 2 and estimation["basis"] == "same-config"
    assert not [k for k in estimation
                if k in ("ts", "at", "date", "last_seen", "revised_at", "since")]

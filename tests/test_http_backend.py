import json
import urllib.error

import comfyui_bridge.adapter.comfy_http as H
from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.adapter.comfy_http import ComfyUIHttpBackend
from comfyui_bridge.config import Settings
from comfyui_bridge.core.intention import RenderIntent
from comfyui_bridge.core.plan import ExecutionPlan

PNG = b"\x89PNG\r\n\x1a\nFAKEDATA"


class _Resp:
    def __init__(self, data): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _plan():
    params = {"prompt": "x", "negative_prompt": "", "width": 768, "height": 768,
              "steps": 20, "cfg": 7.0, "seed": 0, "latent_batch": 1, "filename_prefix": "t"}
    return ExecutionPlan(RenderIntent(prompt="x"), params, kind="image", workflow="sd15-txt2img")


def _backend(tmp_path):
    # A closed port on purpose: a unit test must never reach a real service
    # (a half-alive server on the default port made the suite hang).
    s = Settings(comfy_backend="http", comfy_output_dir=tmp_path, comfyui_poll_interval_s=0.0,
                 comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                 neutral_media=False)
    return ComfyUIHttpBackend(s, load_catalog(s.catalog_file))


def test_http_submit_downloads_media(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            return _Resp(json.dumps({"p1": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
            }}).encode())
        if "/view" in url:
            return _Resp(PNG)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    res = _backend(tmp_path).submit(_plan())
    assert len(res.artifacts) == 1
    a = res.artifacts[0]
    assert a.kind == "image" and a.bytes == len(PNG)
    assert a.url == "/artifacts/out.png"
    assert (tmp_path / "out.png").read_bytes() == PNG


def test_http_reports_workflow_error(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            return _Resp(json.dumps({"p1": {"status": {
                "status_str": "error",
                "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]],
            }}}).encode())
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    from comfyui_bridge.core.errors import BackendExecutionError
    try:
        _backend(tmp_path).submit(_plan())
    except BackendExecutionError as e:
        assert "out of memory" in e.detail.lower()
        # the raw text travels so the domain can classify the real cause
        from comfyui_bridge.core.problems import OOM, classify
        assert classify(e.detail) == OOM
    else:
        raise AssertionError("expected BackendExecutionError")


def test_http_probe_unreachable(tmp_path, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(H.urllib.request, "urlopen", boom)
    p = _backend(tmp_path).probe()
    assert p["available"] is False and "unreachable" in p["reason"]


def test_saturated_engine_is_not_declared_dead(tmp_path, monkeypatch):
    """A ComfyUI busy enough to stop answering HTTP must NOT be reported as a
    failure: it finishes and delivers. Reporting 'failed' there fed Hermes a
    false problem on a run ComfyUI had actually completed."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            calls["n"] += 1
            if calls["n"] <= 40:                      # long silence while computing
                raise urllib.error.URLError("timed out")
            return _Resp(json.dumps({"p1": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
            }}).encode())
        if "/view" in url:
            return _Resp(PNG)
        if "/ws" in url:
            raise urllib.error.URLError("no ws")
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    res = _backend(tmp_path).submit(_plan())
    assert len(res.artifacts) == 1                    # delivered, not failed


def test_execution_time_is_reread_when_the_stamp_lands_late(tmp_path, monkeypatch):
    """Outputs can appear just before ComfyUI stamps execution_success. Reading
    only once lost the engine's measure and left the run un-timed."""
    calls = {"hist": 0}
    done = {"status": {"status_str": "success", "messages": [
                ["execution_start", {"timestamp": 1000}]]},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}
    complete = json.loads(json.dumps(done))
    complete["status"]["messages"].append(["execution_success", {"timestamp": 43000}])

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            calls["hist"] += 1
            return _Resp(json.dumps({"p1": done if calls["hist"] == 1 else complete}).encode())
        if "/view" in url:
            return _Resp(PNG)
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    res = _backend(tmp_path).submit(_plan())
    assert res.execution_s == 42.0        # 43000 - 1000 ms, from the second read


def test_a_run_left_in_flight_is_collected_after_a_restart(tmp_path):
    """Measured: the engine finished a render while the service restarted, and
    the media was never fetched — the run "delivered nothing". What is written
    down before the restart lets ComfyUI's own history close the loop."""
    from comfyui_bridge.adapter.inflight import InflightLog, recover
    from comfyui_bridge.core.plan import Artifact

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p-1", workflow="wf", config="512x512", work=2.0,
            params={"width": 512, "height": 512}, at="2026-08-26T00:00:00+00:00")
    log.add("p-2", workflow="wf", config="512x512", work=2.0, params={}, at="2026-08-26T00:00:00+00:00")

    produced = Artifact(kind="video", path=str(tmp_path / "v.mp4"), url="/artifacts/v.mp4", bytes=10)

    class _Backend:
        # Ce que CE backend sait ramasser porte une version : la reprise doit
        # noter celle du backend qui a repris, pas une constante recopiée.
        delivery_mechanism = 7

        def collect(self, prompt_id, plan=None):
            return ([produced], 42.0) if prompt_id == "p-1" else None   # p-2 still running

    class _Registry:
        def __init__(self): self.rows = []
        def record(self, host, workflow, params, status, problem=None, detail=None,
                   duration_s=None, work=None, work_model=None, mechanism=None):
            self.rows.append((workflow, status, duration_s, work, mechanism))

    registry = _Registry()
    out = recover(_Backend(), log, registry, host="h")
    assert [o["state"] for o in out] == ["recovered"]
    # Le mécanisme de livraison est noté même à la reprise : un run repris par
    # une passerelle d'une autre version ne doit pas être lu comme le sien.
    assert registry.rows == [("wf", "succeeded", 42.0, 2.0, 7)]
    # The collected one is forgotten; the one still running stays written down.
    assert list(log.entries()) == ["p-2"]


def test_a_run_the_engine_ended_in_error_stops_being_in_flight(tmp_path, monkeypatch):
    """MESURÉ : une exception dans un nœud laisse `completed: false` dans
    l'historique de ComfyUI — la même trace qu'un run qui continue. La reprise
    le redemandait à chaque démarrage sans jamais conclure, et l'échec réel
    n'était consigné nulle part."""
    from comfyui_bridge.adapter.inflight import InflightLog, recover

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/history/" in url:
            return _Resp(json.dumps({"p-err": {
                "status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_start", {"prompt_id": "p-err"}],
                    ["execution_error", {"node_id": "1", "node_type": "Load3D",
                                         "exception_message": "string indices must be integers"}],
                ]},
                "outputs": {},
            }}).encode())
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    backend = _backend(tmp_path)
    assert backend.collect("p-err") is None          # rien à collecter…
    echec = backend.failure("p-err")                      # …mais c'est fini
    # Le verdict du moteur voyage avec son message : sans lui, la cause se
    # classait « inconnue », c'est-à-dire imputable à la passerelle.
    assert echec.startswith("execution_error") and "string indices" in echec

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p-err", workflow="wf", config="workflow-default", work=None,
            params={"width": 512}, at="2026-08-29T06:14:54+00:00", kind="image")

    class _Registry:
        def __init__(self): self.rows = []
        def record(self, host, workflow, params, status, problem=None, detail=None,
                   duration_s=None, work=None, work_model=None, mechanism=None):
            self.rows.append((workflow, status, problem, mechanism))

    registry = _Registry()
    out = recover(backend, log, registry, host="h")
    assert [o["state"] for o in out] == ["failed"]
    assert list(log.entries()) == []                  # il ne traîne plus
    # Ce qu'un run vivant aurait retenu est retenu : la cause classée depuis le
    # message réel du moteur, et le mécanisme qui l'a livré.
    from comfyui_bridge.adapter.media import DELIVERY_MECHANISM
    assert registry.rows == [("wf", "failed", "workflow-error", DELIVERY_MECHANISM)]


def test_a_run_served_from_the_engine_cache_teaches_nothing_about_cost():
    """Measured: an identical intent came back in 0.3 s, 48 of 51 nodes reused.
    A real file — and a duration that must never be fitted as a cost."""
    from comfyui_bridge.adapter.comfy_http import _served_from_cache

    reused = {"status": {"messages": [["execution_cached", {"nodes": ["1", "2", "75"]}]]},
              "outputs": {"75": {"images": [{"filename": "v.mp4"}]}}}
    computed = {"status": {"messages": [["execution_cached", {"nodes": ["1", "2"]}]]},
                "outputs": {"75": {"images": [{"filename": "v.mp4"}]}}}
    assert _served_from_cache(reused) is True
    assert _served_from_cache(computed) is False


def test_un_run_dont_les_sorties_arrivent_avant_le_tampon_reste_en_vol(tmp_path, monkeypatch):
    """ComfyUI publie parfois les sorties AVANT d'estampiller la fin — le code
    de `submit` compose déjà avec ce décalage. Dans cet instant, l'historique
    porte des `outputs` et `completed: false` : `collect()` rend None faute de
    tampon, alors que le média, lui, est bien là et n'a jamais été rapatrié.

    La question du journal des runs en vol est « nous doit-on encore quelque
    chose ? ». Un run qui a des sorties nous doit son média : sortir sa ligne
    ici la perdrait, et la marquerait « échouée » par-dessus le marché.
    """
    from comfyui_bridge.adapter.inflight import InflightLog, recover

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/history/" in url:
            return _Resp(json.dumps({"p-tot": {
                "status": {"status_str": "success", "completed": False},
                "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "",
                                              "type": "output"}]}},
            }}).encode())
        if url.endswith("/queue"):
            return _Resp(json.dumps({"queue_running": [], "queue_pending": []}).encode())
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    backend = _backend(tmp_path)
    assert backend.collect("p-tot") is None       # pas de tampon : rien à conclure
    assert backend.failure("p-tot") is None       # et aucune erreur à consigner
    # `settled()` dit vrai (il y a des sorties) : c'est la bonne réponse pour la
    # veille par socket, et la MAUVAISE pour le journal des runs en vol.
    assert backend.settled("p-tot") is True
    assert backend.vanished("p-tot") is False

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p-tot", workflow="wf", config="c", work=None, params={}, at="t", kind="image")
    out = recover(backend, log, None, host="h")
    assert out == []                              # rien de conclu…
    assert list(log.entries()) == ["p-tot"]       # …et la ligne attend son média

# -- le neutre ne décide pas du montage ----------------------------------------
#
# Mesuré le 2026-09-18 (série « références ») : l'élément neutre posé sur
# image_2 et image_3 AVANT le montage faisait choisir l'amorce à trois
# références pour une demande à une image — l'encodeur recevait « <Picture 2>
# is the exact subject to show, same identity and details » sur une image
# blanche — et la même amorce à trois blanches pour une demande sans image.

def _catalogue_a_variantes(tmp_path):
    from comfyui_bridge.adapter.catalog import load_catalog
    wf = tmp_path / "wf"
    wf.mkdir(exist_ok=True)
    montage = {"assemblage": 1, "exemple": {"n": 1}, "montage": [
        {"fragment": "commun", "contenu": {"1": {"class_type": "Charger", "inputs": {}}}},
        {"si": {"parametre": "image_2", "op": "ne", "valeur": None},
         "alors": [{"fragment": "amorce", "contenu": {
             "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
             "2": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
             "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}}}}],
         "sinon": [{"si": {"parametre": "image", "op": "ne", "valeur": None},
                    "alors": [{"fragment": "amorce", "contenu": {
                        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                        "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}}}}],
                    "sinon": [{"fragment": "amorce", "contenu": {
                        "9": {"class_type": "SaveImage", "inputs": {"images": ["$commun.1", 0], "filename_prefix": "x"}}}}]}],
        },
    ]}
    (wf / "m.json").write_text(json.dumps(montage), encoding="utf-8")
    plat = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}}}
    (wf / "plat.json").write_text(json.dumps(plat), encoding="utf-8")
    rec = tmp_path / "reconciliation.json"
    rec.write_text(json.dumps({"default": "m", "workflows": {
        "m": {"kind": "video", "workflow": str(wf / "m.json"),
              "bindings": {"image": {"node": "$amorce.1", "input": "image"},
                           "image_2": {"node": "$amorce.2", "input": "image"}}},
        "plat": {"kind": "image", "workflow": str(wf / "plat.json"),
                 "bindings": {"image": {"node": "1", "input": "image"},
                              "image_2": {"node": "2", "input": "image"}}}}}), encoding="utf-8")
    return load_catalog(rec, workflows_dir=wf)


def _graphe_envoye(tmp_path, monkeypatch, workflow, params):
    s = Settings(comfy_backend="http", comfy_output_dir=tmp_path, comfyui_poll_interval_s=0.0,
                 comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                 neutral_media=True)
    backend = ComfyUIHttpBackend(s, _catalogue_a_variantes(tmp_path))
    monkeypatch.setattr("comfyui_bridge.adapter.neutral.ensure_neutral",
                        lambda base, param, timeout=30.0: "neutre-blanc.png")
    monkeypatch.setattr(backend, "_evincer_les_etrangers", lambda on_note=None: None)
    vus = {}
    monkeypatch.setattr(backend, "_courir", lambda graph, *a, **k: vus.setdefault("graphe", graph))
    backend.submit(ExecutionPlan(RenderIntent(prompt="x"), dict(params, n=1), kind="video", workflow=workflow))
    return vus["graphe"]


def _images_chargees(g):
    return sorted(v["inputs"]["image"] for v in g.values() if v["class_type"] == "LoadImage")


def test_sans_image_le_montage_ne_pose_aucune_reference(tmp_path, monkeypatch):
    g = _graphe_envoye(tmp_path, monkeypatch, "m", {})
    assert _images_chargees(g) == []


def test_avec_une_image_le_montage_n_en_pose_qu_une_et_jamais_le_neutre(tmp_path, monkeypatch):
    g = _graphe_envoye(tmp_path, monkeypatch, "m", {"image": "photo.png"})
    assert _images_chargees(g) == ["photo.png"]


def test_avec_deux_images_le_montage_pose_les_deux(tmp_path, monkeypatch):
    g = _graphe_envoye(tmp_path, monkeypatch, "m", {"image": "photo.png", "image_2": "autre.png"})
    assert _images_chargees(g) == ["autre.png", "photo.png"]


def test_un_graphe_ordinaire_garde_son_neutre_pour_l_entree_laissee_vide(tmp_path, monkeypatch):
    # Hors montage, rien ne change : une entrée image laissée vide reçoit le
    # neutre plutôt que le contenu embarqué du workflow.
    g = _graphe_envoye(tmp_path, monkeypatch, "plat", {"image": "photo.png"})
    assert _images_chargees(g) == ["neutre-blanc.png", "photo.png"]


def test_un_200_avec_node_errors_est_un_refus_qui_nomme_le_noeud(tmp_path, monkeypatch):
    """Mesuré le 2026-09-19 : le moteur a répondu 200 avec `node_errors`
    (« GetImageRangeFromBatch 62 : Value -22 smaller than min of -1 »), a
    couru onze minutes en IGNORANT la sortie fautive, et l'échec n'est venu
    qu'au tour suivant, sans le relais attendu. Un tel 200 est un REFUS : le
    prompt est retiré de la file du moteur, et le refus nomme chaque nœud —
    identifiant, classe, message, détail, sorties ignorées. Un 200 sans
    `node_errors` reste accepté."""
    from comfyui_bridge.core.errors import BackendExecutionError
    retires = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p9", "number": 3, "node_errors": {
                "62": {"class_type": "GetImageRangeFromBatch",
                       "errors": [{"type": "value_smaller_than_min",
                                   "message": "Value -22 smaller than min of -1",
                                   "details": "start_index", "extra_info": {}}],
                       "dependent_outputs": ["70", "71"]}}}).encode())
        if url.endswith("/queue"):
            retires.append(json.loads(req.data.decode("utf-8")))
            return _Resp(b"")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    try:
        _backend(tmp_path).submit(_plan())
    except BackendExecutionError as e:
        assert "node_errors" in e.detail
        assert "nœud 62 (GetImageRangeFromBatch) : Value -22 smaller than min of -1 (start_index)" in e.detail
        assert "sorties ignorées : 70, 71" in e.detail
        assert "p9 retiré de la file du moteur" in e.detail
        assert e.extensions["node_errors"]["62"]["class_type"] == "GetImageRangeFromBatch"
    else:
        raise AssertionError("expected BackendExecutionError")
    assert retires == [{"delete": ["p9"]}]            # l'opération officielle du moteur

    # …et un retrait qui échoue est DIT, jamais tu : le refus reste un refus.
    def urlopen_sans_retrait(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p10", "node_errors": {
                "5": {"class_type": "X", "errors": []}}}).encode())
        raise urllib.error.URLError("moteur muet")

    monkeypatch.setattr(H.urllib.request, "urlopen", urlopen_sans_retrait)
    try:
        _backend(tmp_path).submit(_plan())
    except BackendExecutionError as e:
        assert "nœud 5 (X) : erreur sans message" in e.detail
        assert "p10 NON retiré de la file du moteur" in e.detail
    else:
        raise AssertionError("expected BackendExecutionError")
    # Un 200 sans `node_errors` (ou avec un objet vide) : accepté, comme avant.
    assert H.dire_les_noeuds_fautifs({}) == ""

    def urlopen_sain(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p11", "node_errors": {}}).encode())
        if "/history/" in url:
            return _Resp(json.dumps({"p11": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "sain.png", "subfolder": "",
                                              "type": "output"}]}}}}).encode())
        if "/view" in url:
            return _Resp(PNG)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", urlopen_sain)
    assert len(_backend(tmp_path).submit(_plan()).artifacts) == 1

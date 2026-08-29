"""The websocket watcher relays ComfyUI's own progress — and only that."""

import json

from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.adapter.comfy_http import ComfyUIHttpBackend
from comfyui_bridge.config import Settings


class _WSTimeout(Exception):
    """Stands in for websocket's WebSocketTimeoutException (matched by name)."""


class WebSocketTimeoutException(_WSTimeout):
    pass


class FakeWS:
    """Replays a scripted stream; a `None` entry means 'read timed out'."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def settimeout(self, _t):
        pass

    def recv(self):
        if not self._frames:
            raise WebSocketTimeoutException()
        f = self._frames.pop(0)
        if f is None:
            raise WebSocketTimeoutException()
        return f


def _backend(tmp_path):
    s = Settings(comfy_backend="http", comfy_output_dir=tmp_path, comfyui_total_timeout_s=5)
    return ComfyUIHttpBackend(s, load_catalog(s.catalog_file))


def _msg(kind, data):
    return json.dumps({"type": kind, "data": data})


def test_silence_is_not_the_end_of_the_run(tmp_path):
    """Loading a big model emits nothing for minutes: a read timeout must not
    be mistaken for completion (the bug this test pins)."""
    seen = []
    frames = [
        None, None,                                            # long silence
        _msg("progress", {"value": 3, "max": 8, "node": "3", "prompt_id": "p1"}),
        None,                                                  # more silence
        _msg("progress", {"value": 8, "max": 8, "node": "3", "prompt_id": "p1"}),
        _msg("executing", {"node": None, "prompt_id": "p1"}),  # ComfyUI: done
    ]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1",
                                 lambda v, m, n: seen.append((v, m, n)))
    assert seen == [(3, 8, "3"), (8, 8, "3")]


def test_other_prompt_progress_is_ignored(tmp_path):
    seen = []
    frames = [
        _msg("progress", {"value": 1, "max": 4, "node": "9", "prompt_id": "other"}),
        _msg("executing", {"node": None, "prompt_id": "p1"}),
    ]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1", lambda v, m, n: seen.append((v, m, n)))
    assert seen == []


def test_binary_preview_frames_are_skipped(tmp_path):
    seen = []
    frames = [b"\x00\x01binary-preview",
              _msg("progress", {"value": 2, "max": 2, "node": "1", "prompt_id": "p1"}),
              _msg("executing", {"node": None, "prompt_id": "p1"})]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1", lambda v, m, n: seen.append((v, m, n)))
    assert seen == [(2, 2, "1")]


def test_une_socket_remplacee_ne_fait_pas_attendre_un_run_deja_fini(tmp_path):
    """ComfyUI ne garde qu'UNE socket par clientId (« Reusing existing session,
    remove old »). Deux runs lancés coup sur coup avec le même identifiant
    faisaient remplacer la socket du premier ; devenue muette, elle était
    indiscernable d'un moteur qui charge un modèle, et le run — terminé en une
    seconde d'après l'historique — restait « en cours » jusqu'à l'expiration du
    budget. L'historique tranche."""
    b = _backend(tmp_path)
    b._client.queue = lambda: {"running": [], "pending": []}
    b._get_json = lambda path, timeout: {"p1": {"status": {"completed": True},
                                                "outputs": {"9": {"images": []}}}}
    seen = []
    b._watch_ws(FakeWS([]), "p1", lambda v, m, n: seen.append((v, m, n)))
    assert seen == []          # rien à relayer : il ne reste qu'à ne pas attendre


def test_un_run_encore_en_file_continue_d_etre_attendu(tmp_path):
    """Le silence d'un moteur qui charge un modèle de 20 Go n'est pas une fin."""
    b = _backend(tmp_path)
    b._client.queue = lambda: {"running": ["p1"], "pending": []}
    assert b.settled("p1") is False


def test_chaque_run_a_son_propre_identifiant_de_client(tmp_path):
    """La cause racine : un identifiant par INSTANCE de backend, partagé par
    tous les runs. Il en faut un par run, sinon le moteur ne parle qu'au
    dernier arrivé."""
    from comfyui_bridge.core.intention import RenderIntent
    from comfyui_bridge.core.plan import ExecutionPlan

    b = _backend(tmp_path)
    vus = []

    class _Muet:
        def settimeout(self, _t):
            pass

        def close(self):
            pass

    b._open_ws = lambda client_id: (vus.append(("ws", client_id)) or (None, "attached"))
    b._enqueue = lambda graph, req_t, client_id: (vus.append(("prompt", client_id)) or "p")
    b._await_outputs = lambda *a, **k: {"outputs": {}}
    b._download = lambda *a, **k: [object()]
    plan = ExecutionPlan(RenderIntent(prompt="x", workflow="sd15-txt2img"),
                         {"prompt": "x"}, kind="image", workflow="sd15-txt2img")
    b.submit(plan)
    b.submit(plan)
    ws1, pr1, ws2, pr2 = [v for _, v in vus]
    assert ws1 == pr1 and ws2 == pr2      # une socket et son run partagent l'id
    assert ws1 != ws2                     # deux runs ne le partagent jamais


def test_un_run_termine_par_une_erreur_ne_fait_plus_attendre(tmp_path):
    """Mesuré sur un vrai échec de nœud : status_str « error », completed faux,
    outputs vide, plus rien en file. Ne lire que completed faisait passer
    l'échec pour une attente."""
    b = _backend(tmp_path)
    b._client.queue = lambda: {"running": [], "pending": []}
    b._get_json = lambda path, timeout: {"p1": {"status": {"status_str": "error",
                                                           "completed": False},
                                                "outputs": {}}}
    assert b.settled("p1") is True
    b._watch_ws(FakeWS([]), "p1", lambda v, m, n: None)      # rend la main

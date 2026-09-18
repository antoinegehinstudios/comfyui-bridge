"""Les essais passent par la porte et cèdent la place ; l'étranger est retiré.

Antoine, le 2026-09-18, après qu'une enquête a envoyé ses expériences
directement au moteur et fait attendre son rendu vingt minutes : « ne corrige
pas ce cas unique, ajuste l'outillage pour que ce type de problème
n'apparaisse plus, by design ». By design : (1) un essai entre par
``POST /v1/essais`` dans la file, sur sa voie — il ne tourne que quand aucune
demande n'attend et cède la place à une demande qui arrive ; (2) ce qui atteint
le moteur sans passer par la passerelle est ÉTRANGER : marqué, et retiré de la
file du moteur dès qu'une demande attend derrière lui.
"""

import pathlib
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from comfyui_bridge.core.errors import BackendExecutionError
from comfyui_bridge.core.file_des_demandes import FileDesDemandes

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter.comfy_http import ComfyUIHttpBackend  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import BackendQuiLivre  # noqa: E402
from test_file_des_demandes import _attendre  # noqa: E402

GRAPHE = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "essai", "images": ["2", 0]}},
          "2": {"class_type": "EmptyImage", "inputs": {"width": 8, "height": 8, "batch_size": 1, "color": 0}}}


# -- le mécanisme seul ---------------------------------------------------------


def test_un_essai_ne_tourne_que_quand_aucune_demande_n_attend():
    file = FileDesDemandes()
    ordre = []
    barriere = threading.Event()
    file.deposer("d1", lambda: (ordre.append("d1"), barriere.wait(timeout=5)))
    file.deposer("e1", lambda: ordre.append("e1"), essai=True)
    file.deposer("d2", lambda: ordre.append("d2"))
    time.sleep(0.1)
    assert file.place_de("e1") == {"rang": 2, "devant": ["d1", "d2"], "genre": "essai"}
    assert file.etat() == {"en_cours": "d1", "genre_en_cours": "demande",
                           "en_attente": ["d2"], "essais_en_attente": ["e1"]}
    barriere.set()
    time.sleep(0.3)
    assert ordre == ["d1", "d2", "e1"]


def test_une_demande_qui_arrive_fait_ceder_l_essai_en_cours_qui_repart_apres():
    cedes, cessions, dits = [], [], []
    file = FileDesDemandes(dire=lambda j, m: dits.append((j, m)),
                           ceder=lambda j: cedes.append(j) or interrompre.set(),
                           sur_cession=lambda j, n: cessions.append((j, n)))
    interrompre = threading.Event()
    ordre = []

    def essai():
        ordre.append("essai")
        if interrompre.wait(timeout=5):
            interrompre.clear()
            raise BackendExecutionError("interrompu par le moteur")
    file.deposer("e", essai, essai=True)
    time.sleep(0.15)
    assert file.etat()["en_cours"] == "e"
    file.deposer("d", lambda: ordre.append("d"))
    time.sleep(0.4)
    assert cedes == ["e"] and cessions == [("e", 1)]
    assert ordre == ["essai", "d", "essai"]                  # cédé, puis reparti de zéro
    assert any("cède la place" in m for j, m in dits if j == "e")
    assert any("remis en tête" in m for j, m in dits if j == "e") or cessions


def test_un_essai_qui_casse_sans_ceder_est_une_erreur():
    erreurs = []
    file = FileDesDemandes(sur_erreur=lambda j, e: erreurs.append(j))

    def casse():
        raise RuntimeError("boum")
    file.deposer("e", casse, essai=True)
    time.sleep(0.2)
    assert erreurs == ["e"] and file.etat()["essais_en_attente"] == []


# -- par l'API -----------------------------------------------------------------


class BackendDEssais(BackendQuiLivre):
    """Un backend qui sait faire tourner un graphe tel quel, et qui peut être
    interrompu — comme le moteur l'est par la passerelle quand un essai cède."""

    def __init__(self, sortie):
        super().__init__(sortie)
        self.graphes = []
        self.interrompu = threading.Event()
        self.libre = threading.Event()

    def soumettre_graphe(self, graph, label="essai", on_enqueued=None, on_progress=None,
                         on_note=None, on_started=None):
        self.graphes.append((label, graph))
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.graphes)), "attached")
        if on_started:
            on_started()
        while not self.libre.wait(timeout=0.05):
            if self.interrompu.is_set():
                self.interrompu.clear()
                raise BackendExecutionError("Execution interrupted")
        fichier = self.sortie / "cortex" / "essais" / label / "essai_00001_.png"
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
        return BackendResult(artifacts=[Artifact(kind="image", path=str(fichier), url=None, bytes=16)],
                             raw_stdout="essai", execution_s=0.1)


@pytest.fixture()
def atelier():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_essais_"))
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    app = create_app(settings)
    faux = BackendDEssais(settings.comfy_output_dir)
    c = app.state.container
    c.orchestrator._backend = faux
    c.backend = faux
    # Le moteur d'essai : sa file connaît l'essai en cours ; l'interrompre
    # débloque le backend.
    en_file = {"running": [], "pending": []}
    c.comfyui.queue = lambda: dict(en_file)
    c.comfyui.interrupt = lambda: (faux.interrompu.set(), {"ok": True})[1]
    c.comfyui.cancel = lambda ids: {"ok": True, "cancelled": ids}
    with TestClient(app) as client:
        client.faux = faux
        client.en_file = en_file
        yield client


def test_un_essai_passe_par_la_porte_et_livre_sous_son_nom(atelier):
    r = atelier.post("/v1/essais", json={"graphe": GRAPHE, "label": "continuite B"})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["kind"] == "essai" and job["workflow"] == "essai:continuiteB"
    atelier.faux.libre.set()
    fini = _attendre(atelier, job["id"])
    assert fini["status"] == "succeeded", fini.get("problem")
    assert atelier.faux.graphes[0][0] == "continuiteB"
    assert "essais" in fini["artifacts"][0]["path"] and "continuiteB" in fini["artifacts"][0]["path"]


def test_un_essai_cede_la_place_a_une_demande_puis_repart(atelier):
    essai = atelier.post("/v1/essais", json={"graphe": GRAPHE, "label": "cede"}).json()
    time.sleep(0.3)
    fiche = atelier.get(f"/v1/jobs/{essai['id']}").json()
    assert fiche["status"] == "running" and fiche["file"] == {"rang": 0, "devant": [], "genre": "essai"}
    atelier.en_file["running"].append(fiche["engine_ref"])
    demande = atelier.post("/v1/render", json={"prompt": "priorité", "width": 64, "height": 64}).json()
    fini = _attendre(atelier, demande["id"])
    assert fini["status"] == "succeeded", fini.get("problem")
    # L'essai a cédé (interrompu, remis en tête de sa voie), puis repart et livre.
    journal = "\n".join(atelier.get(f"/v1/jobs/{essai['id']}").json()["logs"])
    assert "cède la place" in journal and "remis en tête" in journal
    atelier.faux.libre.set()
    refait = _attendre(atelier, essai["id"])
    assert refait["status"] == "succeeded", refait.get("problem")
    assert len(atelier.faux.graphes) == 2                       # tourné deux fois : cédé, puis de zéro


def test_un_essai_qui_attend_se_retire_et_un_graphe_vide_est_refuse(atelier):
    atelier.post("/v1/render", json={"prompt": "un", "width": 64, "height": 64})
    essai = atelier.post("/v1/essais", json={"graphe": GRAPHE, "label": "attend"}).json()
    r = atelier.post(f"/v1/jobs/{essai['id']}/cancel")
    assert r.json() == {"cancelled": True, "how": "file-d-attente"}
    assert atelier.get("/v1/file").json()["essais_en_attente"] == []
    assert atelier.post("/v1/essais", json={"graphe": {}, "label": "vide"}).status_code == 422


# -- l'étranger ----------------------------------------------------------------


def test_le_travail_etranger_est_retire_de_la_file_du_moteur_avant_une_demande(tmp_path, monkeypatch):
    """Ce qui atteint le moteur sans la passerelle est étranger : retiré s'il
    attend, interrompu s'il tourne — et dit sur le job qui passe."""
    from comfyui_bridge.adapter import comfy_http
    from comfyui_bridge.adapter.inflight import InflightLog
    inflight = InflightLog(tmp_path / "inflight.json")
    inflight.add("connu-1", workflow="w", config="c", work=None, params={}, at="t")
    backend = ComfyUIHttpBackend.__new__(ComfyUIHttpBackend)
    backend._inflight = inflight
    backend._base = "http://127.0.0.1:9"
    backend._settings = SimpleNamespace(comfyui_request_timeout_s=1.0)
    appels = []

    class ClientFactice:
        def __init__(self, base, request_timeout_s=0): pass
        def queue(self): return {"running": ["etranger-r"], "pending": ["connu-1", "etranger-p"]}
        def cancel(self, ids): appels.append(("cancel", list(ids)))
        def interrupt(self): appels.append(("interrupt",))
    monkeypatch.setattr(comfy_http, "ComfyUIClient", ClientFactice)
    notes = []
    backend._evincer_les_etrangers(notes.append)
    assert appels == [("cancel", ["etranger-p"]), ("interrupt",)]
    assert notes and "ÉTRANGER" in notes[0] and "1 interrompu, 1 retiré" in notes[0] and "/v1/essais" in notes[0]
    # Rien d'étranger : rien à faire, rien dit.
    ClientFactice.queue = lambda self: {"running": ["connu-1"], "pending": []}
    appels.clear(); notes.clear()
    backend._evincer_les_etrangers(notes.append)
    assert appels == [] and notes == []


def test_la_file_du_moteur_marque_l_etranger(atelier):
    atelier.en_file["pending"].append("etranger-x")
    atelier.app.state.container.comfyui.probe = lambda: {"available": True}
    atelier.app.state.container.comfyui.queue = lambda: {
        "running": [], "pending": ["etranger-x"],
        "items": [{"prompt_id": "etranger-x", "number": 1, "state": "pending"}]}
    q = atelier.get("/v1/engine/queue").json()
    assert q["etrangers"] == ["etranger-x"] and q["items"][0]["etranger"] is True

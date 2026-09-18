"""Une seule demande de l'utilisateur à la fois : les autres attendent leur tour.

Antoine, le 2026-09-18 : « ne permets pas que deux requêtes formulées par
l'utilisateur se fassent en même temps ; une seule à la fois, avec une file
d'attente ». Mesuré avant : deux chaînes acceptées à 350 ms d'intervalle se
disputaient le moteur run après run.
"""

import pathlib
import tempfile
import threading
import time

import pytest

from comfyui_bridge.core.file_des_demandes import FileDesDemandes

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from test_chaines_api import BackendQuiLivre  # noqa: E402


# -- le mécanisme seul ---------------------------------------------------------


def test_les_demandes_tournent_une_a_la_fois_dans_l_ordre():
    dits = []
    file = FileDesDemandes(dire=lambda job_id, m: dits.append((job_id, m)))
    ordre, en_meme_temps, verrou = [], [0], threading.Lock()
    actifs = [0]
    barriere = threading.Event()

    def tache(nom):
        with verrou:
            actifs[0] += 1
            en_meme_temps[0] = max(en_meme_temps[0], actifs[0])
        ordre.append(nom)
        barriere.wait(timeout=5)
        with verrou:
            actifs[0] -= 1

    places = [file.deposer(nom, tache, nom) for nom in ("a", "b", "c")]
    time.sleep(0.2)
    assert file.place_de("a") == {"rang": 0, "devant": [], "genre": "demande"}
    assert file.place_de("b") == {"rang": 1, "devant": ["a"], "genre": "demande"}
    assert file.place_de("c") == {"rang": 2, "devant": ["a", "b"], "genre": "demande"}
    assert places[1]["rang"] == 1 and places[2] == {"rang": 2, "devant": ["a", "b"], "genre": "demande"}
    assert file.etat() == {"en_cours": "a", "genre_en_cours": "demande", "en_attente": ["b", "c"], "essais_en_attente": []}
    barriere.set()
    for _ in range(50):
        if file.etat()["en_cours"] is None and not file.etat()["en_attente"]:
            break
        time.sleep(0.05)
    assert ordre == ["a", "b", "c"] and en_meme_temps[0] == 1
    assert file.place_de("a") is None
    # Ce qui attend l'a su, en le disant ; la première n'attendait rien.
    assert [j for j, _ in dits] == ["b", "c"] and "1 entrée(s) avant" in dits[0][1]


def test_une_demande_retiree_avant_son_tour_ne_tourne_jamais():
    file = FileDesDemandes()
    tournees = []
    barriere = threading.Event()
    file.deposer("a", lambda: (tournees.append("a"), barriere.wait(timeout=5)))
    file.deposer("b", lambda: tournees.append("b"))
    time.sleep(0.1)
    assert file.retirer("b") is True
    assert file.retirer("b") is False and file.retirer("a") is False      # en cours : pas d'ici
    barriere.set()
    time.sleep(0.2)
    assert tournees == ["a"]


def test_une_demande_qui_casse_ne_tue_pas_la_file():
    erreurs = []
    file = FileDesDemandes(sur_erreur=lambda job_id, exc: erreurs.append((job_id, str(exc))))
    tournees = []

    def casse():
        raise RuntimeError("boum")
    file.deposer("a", casse)
    file.deposer("b", lambda: tournees.append("b"))
    time.sleep(0.3)
    assert erreurs == [("a", "boum")] and tournees == ["b"]


# -- par l'API -----------------------------------------------------------------


class BackendQuiAttend(BackendQuiLivre):
    """Un backend dont chaque run attend qu'on le libère : deux demandes ne
    peuvent pas se chevaucher sans qu'on le voie."""

    def __init__(self, sortie):
        super().__init__(sortie)
        self.barriere = threading.Event()
        self.en_cours = 0
        self.max_en_cours = 0
        self._verrou = threading.Lock()

    def submit(self, plan, **kw):
        with self._verrou:
            self.en_cours += 1
            self.max_en_cours = max(self.max_en_cours, self.en_cours)
        try:
            self.barriere.wait(timeout=10)
            return super().submit(plan, **kw)
        finally:
            with self._verrou:
                self.en_cours -= 1


@pytest.fixture()
def atelier():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_file_"))
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    app = create_app(settings)
    faux = BackendQuiAttend(settings.comfy_output_dir)
    app.state.container.orchestrator._backend = faux
    with TestClient(app) as c:
        c.faux = faux
        yield c


def _attendre(client, job_id, fini=("succeeded", "failed", "cancelled"), tours=100):
    for _ in range(tours):
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in fini:
            return job
        time.sleep(0.05)
    return client.get(f"/v1/jobs/{job_id}").json()


def test_deux_demandes_ne_tournent_jamais_en_meme_temps_et_la_seconde_dit_sa_place(atelier):
    un = atelier.post("/v1/render", json={"prompt": "un", "width": 64, "height": 64}).json()
    deux = atelier.post("/v1/render", json={"prompt": "deux", "width": 64, "height": 64}).json()
    time.sleep(0.2)
    file = atelier.get("/v1/file").json()
    assert file["une_a_la_fois"] is True
    assert file["en_cours"] == un["id"] and file["en_attente"] == [deux["id"]]
    fiche = atelier.get(f"/v1/jobs/{deux['id']}").json()
    assert fiche["file"] == {"rang": 1, "devant": [un["id"]], "genre": "demande"}
    assert any("en file d'attente : 1 entrée(s) avant" in l for l in fiche["logs"])
    assert atelier.get(f"/v1/jobs/{un['id']}").json()["file"] == {"rang": 0, "devant": [], "genre": "demande"}
    atelier.faux.barriere.set()
    assert _attendre(atelier, deux["id"])["status"] == "succeeded"
    assert atelier.faux.max_en_cours == 1
    assert "file" not in atelier.get(f"/v1/jobs/{deux['id']}").json()


def test_annuler_une_demande_qui_attend_la_retire_sans_la_lancer(atelier):
    un = atelier.post("/v1/render", json={"prompt": "un", "width": 64, "height": 64}).json()
    deux = atelier.post("/v1/render", json={"prompt": "deux", "width": 64, "height": 64}).json()
    time.sleep(0.2)
    r = atelier.post(f"/v1/jobs/{deux['id']}/cancel")
    assert r.status_code == 200 and r.json() == {"cancelled": True, "how": "file-d-attente"}
    fiche = atelier.get(f"/v1/jobs/{deux['id']}").json()
    assert fiche["status"] == "cancelled" and fiche["problem"]["problem_kind"] == "cancelled"
    assert atelier.get("/v1/file").json()["en_attente"] == []
    atelier.faux.barriere.set()
    assert _attendre(atelier, un["id"])["status"] == "succeeded"
    assert len(atelier.faux.runs) == 1

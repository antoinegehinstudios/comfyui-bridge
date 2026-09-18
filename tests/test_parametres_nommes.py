"""Les réglages NOMMÉS d'un rendu : une valeur qu'un flux expose sous un nom,
injectée par la liaison du workflow — le rôle d'une image de référence
(« le personnage principal »), sans nommer aucun nœud.

Comme une pièce jointe, mais une valeur : une chaîne l'écrit dans l'étape
« rendre » sous « parametres », un appelant direct sous le même nom, et le
workflow dit où elle atterrit par sa liaison. Un nom que rien ne lie est dit
« non appliqué », jamais avalé ; une valeur vide n'écrit rien.
"""

import contextlib
import json
import pathlib
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter.injector import inject  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from test_chaines_api import BackendQuiLivre, _job  # noqa: E402

GRAPHE = {
    "5": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": ""}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "cortex/essai", "images": ["5", 0]}},
}

CHAINE = {
    "version": 1, "chaine": "chaine-a-role",
    "resume": "un rendu qui reçoit le rôle d'une image",
    "expose": {"role": {"type": "STRING", "defaut": "", "libelle": "Ce que l'image est"},
               # Une image FACULTATIVE, sans défaut : laissée au repos, elle vaut
               # « non fourni », et l'étape qui l'écrit dans ses médias reçoit rien.
               "image": {"media": "image", "requis": False, "libelle": "Une image (facultatif)"}},
    "etapes": [{"id": "rendu", "rendre": {"workflow": "image-a-role",
                                          "media": {"image": "$image"},
                                          "parametres": {"role_image": "$role"}}}],
    "livrable": "$rendu.livrable",
}


class BackendQuiNote(BackendQuiLivre):
    def __init__(self, sortie, catalog):
        super().__init__(sortie)
        self.catalog = catalog
        self.plans = []
        self.graphes = []

    def submit(self, plan, **kw):
        self.plans.append(plan)
        spec = self.catalog.get_spec(plan.workflow)
        gabarit, liaisons = self.catalog.monter(spec, dict(plan.params))
        self.graphes.append(inject(gabarit, liaisons, dict(plan.params)))
        return super().submit(plan, **kw)


@pytest.fixture()
def atelier():
    pile = contextlib.ExitStack()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_roles_"))
    (tmp / "image-a-role.json").write_text(json.dumps(GRAPHE), encoding="utf-8")
    (tmp / "chaine-a-role.json").write_text(json.dumps(CHAINE), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "image-a-role": {"kind": "image", "workflow": str(tmp / "image-a-role.json"),
                             "bindings": {"role_image": {"node": "5", "input": "value"},
                                          "filename_prefix": {"node": "9", "input": "filename_prefix"}}},
            "chaine-a-role": {"kind": "image", "chaine": str(tmp / "chaine-a-role.json"),
                              "titre": "Chaîne à rôle", "categorie": "essais", "ordre": 1},
        }}, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    app = create_app(settings)
    faux = BackendQuiNote(settings.comfy_output_dir, app.state.container.catalog)
    app.state.container.orchestrator._backend = faux
    client = pile.enter_context(TestClient(app))
    client.faux = faux
    yield client
    pile.close()


def test_une_chaine_transmet_un_reglage_nomme_qui_atterrit_par_la_liaison(atelier):
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-role",
                                                         "role": "le personnage principal"}))
    assert job["status"] == "succeeded", job.get("problem")
    plan = atelier.faux.plans[-1]
    assert plan.params["role_image"] == "le personnage principal"
    assert atelier.faux.graphes[-1]["5"]["inputs"]["value"] == "le personnage principal"
    sous = atelier.get(f"/v1/jobs/{plan.intent.label and job['etapes'][0]['job_id']}").json()
    assert not any("non appliqué" in l and "role_image" in l for l in sous["logs"])


def test_une_image_facultative_laissee_au_repos_ne_designe_rien(atelier):
    # Sans image : l'étape tourne, sans média — « $image » vaut « non fourni ».
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-role",
                                                         "role": "un rôle"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert "image" not in atelier.faux.plans[-1].params
    # Avec elle : le nom que le moteur lui donne arrive au run.
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-role",
                                                         "role": "un rôle", "image": "photo.png"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert atelier.faux.plans[-1].params.get("image") == "photo.png"


def test_un_reglage_vide_n_ecrit_rien(atelier):
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-role", "role": ""}))
    assert job["status"] == "succeeded", job.get("problem")
    assert "role_image" not in atelier.faux.plans[-1].params
    assert atelier.faux.graphes[-1]["5"]["inputs"]["value"] == ""


def test_un_appelant_direct_envoie_le_meme_nom(atelier):
    job = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "image-a-role", "parametres": {"role_image": "l'objet exact"}}))
    assert job["status"] == "succeeded", job.get("problem")
    assert atelier.faux.graphes[-1]["5"]["inputs"]["value"] == "l'objet exact"


def test_un_nom_que_rien_ne_lie_est_dit_non_applique(atelier):
    job = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "image-a-role", "parametres": {"humeur": "gaie"}}))
    assert job["status"] == "succeeded", job.get("problem")
    assert any("non appliqué" in l and "humeur" in l for l in job["logs"])

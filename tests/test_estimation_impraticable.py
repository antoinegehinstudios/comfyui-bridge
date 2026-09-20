"""Une demande qu'un montage refuse est dite IMPRATICABLE par l'estimation.

Un rôle d'image donné sans son image (2026-09-20 : « Retirer » une image en
laissant son rôle) fait refuser le montage — `WorkflowMappingError`, que
l'estimation avalait (d'où une médiane sans rapport) et que le run levait
ensuite, sous un message qui nommait un nœud. L'estimation monte chaque étape
à blanc (l'amorce seule quand le montage va par tours) et le dit, réglage
nommé, sans annoncer de durée ; l'image jointe, tout tourne.
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


class BackendQuiMonte(BackendQuiLivre):
    """Comme le vrai backend : le graphe est monté et injecté avant de partir —
    c'est là qu'un montage refuse une demande."""

    def __init__(self, sortie, catalog):
        super().__init__(sortie)
        self.catalog = catalog

    def submit(self, plan, **kw):
        spec = self.catalog.get_spec(plan.workflow)
        gabarit, liaisons = self.catalog.monter(spec, dict(plan.params))
        inject(gabarit, liaisons, dict(plan.params))
        return super().submit(plan, **kw)

MONTAGE = {"assemblage": 1, "exemple": {"n": 1}, "montage": [
    {"fragment": "commun", "contenu": {
        "1": {"class_type": "Charger", "inputs": {}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "cortex/essai", "images": ["1", 0]}}}},
    {"si": {"parametre": "image", "op": "ne", "valeur": None},
     "alors": [{"fragment": "references", "contenu": {
         "1": {"class_type": "LoadImage", "inputs": {"image": "exemple.png"}},
         "2": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "le sujet"}}}}]},
]}

CHAINE = {
    "version": 1, "chaine": "chaine-a-variante",
    "resume": "un montage dont la variante aux références ne se pose qu'avec l'image jointe",
    "expose": {"image": {"media": "image", "requis": False, "libelle": "Une image (facultatif)"},
               "role": {"type": "STRING", "defaut": "", "libelle": "Ce que l'image est"}},
    "etapes": [{"id": "rendu", "rendre": {"workflow": "montage-a-variante",
                                          "media": {"image": "$image"},
                                          "parametres": {"role_image": "$role"}}}],
    "livrable": "$rendu.livrable",
}


@pytest.fixture()
def atelier():
    pile = contextlib.ExitStack()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_impraticable_"))
    (tmp / "montage-a-variante.json").write_text(json.dumps(MONTAGE), encoding="utf-8")
    (tmp / "chaine-a-variante.json").write_text(json.dumps(CHAINE, ensure_ascii=False),
                                                encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "montage-a-variante": {"kind": "image", "workflow": str(tmp / "montage-a-variante.json"),
                                   "bindings": {"image": {"node": "$references.1", "input": "image"},
                                                "role_image": {"node": "$references.2", "input": "value"},
                                                "filename_prefix": {"node": "$commun.9",
                                                                    "input": "filename_prefix"}}},
            "chaine-a-variante": {"kind": "image", "chaine": str(tmp / "chaine-a-variante.json"),
                                  "titre": "Chaîne à variante", "categorie": "essais", "ordre": 1},
        }}, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    app = create_app(settings)
    app.state.container.orchestrator._backend = BackendQuiMonte(settings.comfy_output_dir,
                                                                 app.state.container.catalog)
    client = pile.enter_context(TestClient(app))
    yield client
    pile.close()


def test_un_role_sans_son_image_est_dit_impraticable_avant_de_lancer(atelier):
    d = atelier.post("/v1/estimate", json={"workflow": "chaine-a-variante",
                                           "role": "le personnage principal"}).json()
    assert d["estimate"] is None and "manque" not in d
    assert d["impraticable"].startswith("étape rendu : « role_image » n'a pas de place dans ce "
                                        "dépliage du montage ($references.2)")
    assert "le retirer, ou joindre ce qu'il accompagne" in d["impraticable"]
    # L'image jointe : plus rien à reprocher — rien de mesuré encore, et c'est dit.
    d = atelier.post("/v1/estimate", json={"workflow": "chaine-a-variante", "image": "photo.png",
                                           "role": "le personnage principal"}).json()
    assert "impraticable" not in d and "jamais été mesurée" in d["manque"]
    # Sans rôle ni image : la variante n'est pas posée, rien à écrire, rien à reprocher.
    d = atelier.post("/v1/estimate", json={"workflow": "chaine-a-variante"}).json()
    assert "impraticable" not in d


def test_le_run_refuse_ce_que_l_estimation_disait_impraticable(atelier):
    """La même demande lancée : refusée au run, sous le même message — le
    réglage nommé, pas un numéro de nœud."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-a-variante",
                                                         "role": "le personnage principal"}))
    assert job["status"] == "failed", job.get("problem")
    assert "« role_image » n'a pas de place dans ce dépliage du montage" in job["problem"]["detail"]

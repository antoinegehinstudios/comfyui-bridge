"""Deux phases par bloc : un run d'encodage ne livre que ses relais, le run de
rendu les relit et livre la vidéo — et le mécanisme ne compte que les vidéos.

Mesuré le 2026-09-18 au premier rendu à deux phases : la passerelle refusait
le run d'encodage (« n'a pas livré de vidéo ») alors qu'il avait écrit son
conditionnement et son latent, exactement ce qu'on lui demandait.
"""

import contextlib
import json
import pathlib
import subprocess
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import montage_video  # noqa: E402
from comfyui_bridge.adapter.measure import measure  # noqa: E402
from comfyui_bridge.adapter.media import artifact_url, media_kind  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import SANS_FFMPEG, _job  # noqa: E402
from test_run_par_tour import (CADENCE, CHAINE_PAR_TOURS, HAUTEUR, LARGEUR,  # noqa: E402
                               MONTAGE_PAR_TOURS, BackendQuiTourne, _etape)

RELAIS_IMAGE = MONTAGE_PAR_TOURS["montage"][1]["pour"]["relais"]["derniere_image"]

MONTAGE_EN_PHASES = {
    **json.loads(json.dumps(MONTAGE_PAR_TOURS)),
    "montage": [
        {"fragment": "commun", "sorties": {"modele": "1"}, "contenu": {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}}}},
        {"fragment": "texte", "sorties": {"clip": "1"}, "contenu": {
            "1": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen.safetensors"}}}},
        {"pour": {"jusqu_a": "duration_s", "chaque": "secondes_par_bloc", "un_run_par_tour": True,
                  "phases": 2,
                  "relais": {
                      "derniere_image": RELAIS_IMAGE,
                      "conditionnement": {
                          "ecrire": {"class_type": "SauverConditionnement",
                                     "inputs": {"conditioning": "$relais.port",
                                                "filename_prefix": "$relais.prefixe"}},
                          "lire": {"class_type": "ChargerConditionnement",
                                   "inputs": {"fichier": "$relais.fichier"}}}}},
         "faire": [
             {"si": {"parametre": "phase", "op": "eq", "valeur": 0},
              "alors": [{"fragment": "encodage", "sorties": {"conditionnement": "1"}, "contenu": {
                  "1": {"class_type": "Encoder", "inputs": {"clip": ["$texte.clip", 0]}}}}],
              "sinon": [{"fragment": "rendu", "sorties": {"derniere_image": "1"}, "contenu": {
                            "1": {"class_type": "ImageVersVideo",
                                  "inputs": {"model": ["$commun.modele", 0],
                                             "conditioning": ["$precedent.conditionnement", 0]}}}},
                        {"fragment": "livrer", "sorties": {"derniere_image": "1"}, "contenu": {
                            "1": {"class_type": "VHS_VideoCombine",
                                  "inputs": {"images": ["$precedent.derniere_image", 0],
                                             "filename_prefix": {"$texte": "$const.prefixe_morceaux",
                                                                 "$rang": "bloc_rang"}}}}}]},
         ]},
    ],
}


class BackendEnPhases(BackendQuiTourne):
    """Un run d'encodage (phase 0) n'écrit que son relais ; un run de rendu écrit la vidéo
    et la dernière image."""

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.runs)), "attached")
        if on_started:
            on_started()
        self.tours_recus.append((plan.params.get("tour"), plan.params.get("relais_conditionnement"),
                                 plan.params.get("relais_derniere_image")))
        graphe, _ = self.catalog.monter(self.catalog.get_spec(plan.workflow), dict(plan.params))
        self.graphes.append(graphe)
        dossier = self.sortie / "cortex"
        dossier.mkdir(parents=True, exist_ok=True)
        nom = str(plan.params.get("filename_prefix", "cortex/essai")).rsplit("/", 1)[-1]
        types = {v["class_type"] for v in graphe.values()}
        artefacts = []
        if "SauverConditionnement" in types:
            fichier = dossier / f"{nom}_relais_conditionnement_00001_.pt"
            fichier.write_bytes(b"\x80\x02")
            artefacts.append(Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                                      url=artifact_url(self.sortie, fichier), bytes=fichier.stat().st_size))
        if "VHS_VideoCombine" in types:
            fichier = dossier / f"{nom}_recolle.mp4"
            subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                            "-i", f"testsrc=size={LARGEUR}x{HAUTEUR}:rate={CADENCE}:duration=1",
                            "-pix_fmt", "yuv420p", str(fichier)], check=True)
            artefacts.append(Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                                      url=artifact_url(self.sortie, fichier), bytes=fichier.stat().st_size,
                                      measured=measure(fichier) or None))
        if "SaveImage" in types:
            image = dossier / f"{nom}_relais_derniere_image_00001_.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
            artefacts.append(Artifact(kind=media_kind(image), path=str(image.resolve()),
                                      url=artifact_url(self.sortie, image), bytes=image.stat().st_size))
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


@pytest.fixture()
def banc(monkeypatch):
    pile = contextlib.ExitStack()
    monkeypatch.setattr("comfyui_bridge.adapter.neutral.upload_image",
                        lambda base, nom, contenu, *reste, **autres: nom)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_phases_"))
    (tmp / "video-en-phases.json").write_text(json.dumps(MONTAGE_EN_PHASES), encoding="utf-8")
    chaine = json.loads(json.dumps(CHAINE_PAR_TOURS))
    chaine["chaine"] = "chaine-en-phases"
    chaine["etapes"][0]["rendre"]["workflow"] = "video-en-phases"
    (tmp / "chaine-en-phases.json").write_text(json.dumps(chaine), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "video-en-phases": {"kind": "video", "workflow": str(tmp / "video-en-phases.json"),
                                "bindings": {},
                                "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
            "chaine-en-phases": {"kind": "video", "chaine": str(tmp / "chaine-en-phases.json"),
                                 "titre": "Chaîne en phases", "categorie": "essais", "ordre": 1},
        }}, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows")
    app = create_app(settings)
    faux = BackendEnPhases(settings.comfy_output_dir, app.state.container.catalog)
    app.state.container.orchestrator._backend = faux
    client = pile.enter_context(TestClient(app))
    client.faux = faux
    yield client
    pile.close()


@SANS_FFMPEG
def test_un_run_d_encodage_ne_livre_que_son_relais_et_le_rendu_le_relit(banc):
    job = _job(banc, banc.post("/v1/render", json={"workflow": "chaine-en-phases",
                                                   "secondes": 2, "label": "phases"}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert rendu["tours"] == 4 and len(rendu["job_ids"]) == 4
    assert [t for t, _, _ in banc.faux.tours_recus] == [0, 1, 2, 3]
    # Le rendu (tour 1) relit le conditionnement écrit par l'encodage (tour 0) ;
    # l'encodage du bloc suivant (tour 2) relit la dernière image du rendu (tour 1).
    assert banc.faux.tours_recus[1][1].endswith("_relais_conditionnement_00001_.pt")
    assert banc.faux.tours_recus[2][2].endswith("_relais_derniere_image_00001_.png")
    encodage, rendu_0 = banc.faux.graphes[:2]
    assert sorted(v["class_type"] for v in encodage.values()) == ["CLIPLoader", "Encoder", "SauverConditionnement"]
    types_rendu = [v["class_type"] for v in rendu_0.values()]
    assert "CLIPLoader" not in types_rendu and "UNETLoader" in types_rendu
    # Deux vidéos (une par bloc) recollées ; les runs d'encodage n'ajoutent aucun morceau.
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert 1.7 <= montage_video.mesurer(livrable)["duration_s"] <= 2.3
    assert "4 tours recollés" in "\n".join(job["logs"])
    # Un relais n'est ni un média ni des nombres.
    assert media_kind("x_relais_conditionnement_00001_.pt") == "relais"


@SANS_FFMPEG
def test_entre_deux_runs_de_phases_differentes_le_moteur_libere_ses_modeles(banc):
    # Le moteur garde ses modèles entre les prompts ; avec des phases, on lui
    # demande de les libérer avant chaque run après le premier — par son
    # opération officielle. Un moteur qui ne répond pas ne bloque rien.
    appels = []
    banc.app.state.container.comfyui.free = lambda **kw: appels.append(kw) or {}
    job = _job(banc, banc.post("/v1/render", json={"workflow": "chaine-en-phases",
                                                   "secondes": 2, "label": "libere"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert appels == [{"unload_models": True, "free_memory": True}] * 3       # avant les tours 2, 3, 4
    assert sum("libérés chez le moteur" in l for l in job["logs"]) == 3

    def refuse(**kw):
        raise OSError("moteur muet")
    banc.app.state.container.comfyui.free = refuse
    job = _job(banc, banc.post("/v1/render", json={"workflow": "chaine-en-phases",
                                                   "secondes": 2, "label": "muet"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert any("n'a pas libéré ses modèles" in l for l in job["logs"])

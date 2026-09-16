"""La fin d'une chaîne : la conclusion sans sa queue, et l'étape qui n'a pas lieu.

Antoine, 2026-09-16 : « à la toute fin, tu éprouveras avec une vidéo 4K/60 fps
de 20 s ». L'appel final chargeait la conclusion ENTIÈRE pour y écrire son texte
et la rendait entière : en 4K, c'est une vidéo de plus en mémoire pour quelques
secondes d'encre. Il ne rend désormais QUE ses images, et dit combien il en a
reprises — le montage joint donc la conclusion SANS celles-là, puis l'appel.
Sans appel, l'étape est sautée et son « sinon » rend de quoi monter quand même.

Éprouvé avec un vrai ffmpeg : le rognage de queue se mesure en secondes de
livrable, pas en intention.
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
from comfyui_bridge.adapter.media import artifact_url, media_kind  # noqa: E402
from comfyui_bridge.adapter.measure import measure  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core import chaine as noyau  # noqa: E402
from comfyui_bridge.core.errors import MediaAssemblyError, WorkflowMappingError  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import SANS_FFMPEG, BackendQuiLivre, _job  # noqa: E402

CADENCE = 25
# L'appel d'essai reprend une demi-seconde de la conclusion : de quoi mesurer le
# rognage sans dépendre d'une image près.
IMAGES_REPRISES = 13


def _clip(fichier: pathlib.Path, secondes: float) -> pathlib.Path:
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc=size=160x120:rate={CADENCE}:duration={secondes}",
                    "-pix_fmt", "yuv420p", str(fichier)], check=True)
    return fichier


# -- le rognage de queue, sans app ---------------------------------------------


@SANS_FFMPEG
def test_une_part_entre_dans_le_montage_sans_sa_queue(tmp_path):
    """Ce que l'aval a déjà repris ne doit pas passer deux fois. Mesuré en
    secondes de livrable : deux secondes moins une demi, plus une."""
    un = _clip(tmp_path / "un.mp4", 2.0)
    deux = _clip(tmp_path / "deux.mp4", 1.0)
    entier = montage_video.recoller([un, deux], tmp_path / "entier.mp4",
                                    fps=CADENCE, largeur=160, hauteur=120)
    assert 2.8 <= entier["mesure"]["duration_s"] <= 3.2

    rogne = montage_video.recoller(
        [{"fichier": str(un), "sauf_les_dernieres": IMAGES_REPRISES}, deux],
        tmp_path / "rogne.mp4", fps=CADENCE, largeur=160, hauteur=120)
    assert rogne["parts"] == 2
    attendu = 3.0 - IMAGES_REPRISES / CADENCE
    assert abs(rogne["mesure"]["duration_s"] - attendu) <= 0.2, rogne["mesure"]


@SANS_FFMPEG
def test_une_part_vide_est_ignoree_et_dite(tmp_path):
    """C'est ainsi qu'une étape sautée rend « rien » sans casser le montage qui
    la nomme. Il en faut au moins une qui reste."""
    un = _clip(tmp_path / "un.mp4", 1.0)
    dits: list[str] = []
    fait = montage_video.recoller([un, None, {"fichier": None}], tmp_path / "seul.mp4",
                                  fps=CADENCE, largeur=160, hauteur=120,
                                  signaler=dits.append)
    assert fait["parts"] == 1
    assert len(dits) == 2 and all("ignorée" in d for d in dits)
    with pytest.raises(MediaAssemblyError, match="toutes sont vides"):
        montage_video.recoller([None, ""], tmp_path / "rien.mp4")


@SANS_FFMPEG
def test_une_part_plus_courte_que_son_rognage_est_refusee(tmp_path):
    """Il n'en resterait rien : le dire vaut mieux que livrer un montage amputé
    en silence."""
    court = _clip(tmp_path / "court.mp4", 0.4)          # ≈ 10 images
    autre = _clip(tmp_path / "autre.mp4", 1.0)
    with pytest.raises(MediaAssemblyError, match="il n'en resterait rien"):
        montage_video.recoller([{"fichier": str(court), "sauf_les_dernieres": 50}, autre],
                               tmp_path / "non.mp4", fps=CADENCE, largeur=160, hauteur=120)


@SANS_FFMPEG
def test_le_raccord_se_mesure_sur_la_derniere_image_gardee(tmp_path):
    """« Réel » veut dire : la frontière que le spectateur verra. Mesurer la
    dernière image du FICHIER aurait jugé une frontière que personne ne voit."""
    un = _clip(tmp_path / "un.mp4", 2.0)
    deux = _clip(tmp_path / "deux.mp4", 1.0)
    mesure = montage_video.mesurer_raccords(
        [{"fichier": str(un), "sauf_les_dernieres": IMAGES_REPRISES}, deux],
        tmp_path / "travail")
    assert mesure["nombre"] == 1 and 0.0 <= mesure["pire"] <= 1.0


# -- « sinon », sans app -------------------------------------------------------


def test_sinon_est_un_objet_ou_rien():
    """Ce qu'une lecture peut prouver faux, elle le refuse."""
    base = {"version": 1, "chaine": "c", "etapes": [
        {"id": "une", "rendre": {"workflow": "w"}}], "livrable": "$une.livrable"}
    assert noyau.lire(base).etapes[0].sinon == {}
    avec = json.loads(json.dumps(base))
    avec["etapes"][0]["sinon"] = {"livrable": None}
    assert noyau.lire(avec).etapes[0].sinon == {"livrable": None}
    faux = json.loads(json.dumps(base))
    faux["etapes"][0]["sinon"] = "rien"
    with pytest.raises(WorkflowMappingError, match="« sinon »"):
        noyau.lire(faux)


# -- de bout en bout -----------------------------------------------------------

# Une chaîne d'essai à la forme de la vraie fin : une « conclusion » de deux
# secondes, un « appel » facultatif qui en reprend la queue, un montage à deux
# ou trois parts, et un contrôle qui lit ce qui a été monté.
CHAINE_DE_FIN = {
    "version": 1, "chaine": "chaine-de-fin",
    "resume": "une conclusion, un appel facultatif, un montage",
    "expose": {"cta": {"type": "STRING", "defaut": "", "libelle": "Appel final"}},
    "etapes": [
        {"id": "conclusion", "rendre": {"workflow": "video-essai", "prompt": "fin",
                                        "duration_s": 2.0}},
        {"id": "appel", "quand": "$cta",
         "sinon": {"livrable": None, "recit": {"images_reprises": 0}},
         "rendre": {"workflow": "video-appel-essai", "prompt": "$cta", "duration_s": 1.0,
                    "media": {"video": "$conclusion.livrable"}}},
        {"id": "montage", "recoller": {
            "parts": [{"fichier": "$conclusion.livrable",
                       "sauf_les_dernieres": "$appel.recit.images_reprises"},
                      "$appel.livrable"],
            "fps": CADENCE, "largeur": 160, "hauteur": 120}},
        {"id": "controle", "verifier": [
            {"id": "le_montage_a_ses_parts", "valeur": "$montage.parts",
             "op": "between", "attendu": [1, 2]},
            {"id": "les_images_reprises_sont_dites",
             "valeur": "$appel.recit.images_reprises", "op": "exists"}]},
    ],
    "livrable": "$montage.livrable",
}


class BackendDeFin(BackendQuiLivre):
    """Un backend d'essai dont l'APPEL dit combien d'images il a reprises."""

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.runs)), "attached")
        if on_started:
            on_started()
        if on_progress:
            on_progress(1, 1, "1")
        dossier = self.sortie / "cortex"
        dossier.mkdir(parents=True, exist_ok=True)
        nom = str(plan.params.get("filename_prefix", "cortex/essai")).rsplit("/", 1)[-1]
        fichier = _clip(dossier / f"{nom}_{len(self.runs)}.mp4",
                        float(plan.params.get("duration_s") or 1))
        artefacts = [Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                              url=artifact_url(self.sortie, fichier),
                              bytes=fichier.stat().st_size, measured=measure(fichier) or None)]
        if plan.workflow == "video-appel-essai":
            # Le récit de l'appel : ce que le nœud dit avoir repris à la
            # conclusion, et que le montage retranche.
            recit = fichier.with_suffix(".json")
            recit.write_text(json.dumps({"images_reprises": IMAGES_REPRISES,
                                         "images_ecrites": 25}), encoding="utf-8")
            artefacts.append(Artifact(kind=media_kind(recit), path=str(recit.resolve()),
                                      url=artifact_url(self.sortie, recit),
                                      bytes=recit.stat().st_size))
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


# La même, dont le contrôle final ne peut pas tenir : de quoi éprouver qu'en
# ÉCHEC les fichiers déjà écrits restent livrés.
CHAINE_JUGEE = json.loads(json.dumps(CHAINE_DE_FIN))
CHAINE_JUGEE["chaine"] = "chaine-de-fin-jugee"
CHAINE_JUGEE["etapes"][-1]["verifier"] = [
    {"id": "impossible", "valeur": "$montage.parts", "op": "gte", "attendu": 99}]


@pytest.fixture()
def atelier():
    pile = contextlib.ExitStack()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_fin_"))
    (tmp / "chaine-de-fin.json").write_text(json.dumps(CHAINE_DE_FIN, ensure_ascii=False),
                                            encoding="utf-8")
    (tmp / "chaine-de-fin-jugee.json").write_text(json.dumps(CHAINE_JUGEE, ensure_ascii=False),
                                                  encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "chaine-de-fin": {"kind": "video", "chaine": str(tmp / "chaine-de-fin.json"),
                              "titre": "Chaîne de fin", "categorie": "essais", "ordre": 1},
            "chaine-de-fin-jugee": {"kind": "video",
                                    "chaine": str(tmp / "chaine-de-fin-jugee.json"),
                                    "titre": "Chaîne jugée", "categorie": "essais", "ordre": 2},
            "video-essai": {"kind": "video", "workflow": "workflow_template.json",
                            "bindings": {"filename_prefix": {"node": "9",
                                                             "input": "filename_prefix"}}},
            "video-appel-essai": {"kind": "video", "workflow": "workflow_template.json",
                                  "bindings": {"filename_prefix": {"node": "9",
                                                                   "input": "filename_prefix"}}},
        },
    }, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=64 * 2 ** 30)
    app = create_app(settings)
    app.state.container.orchestrator._backend = BackendDeFin(settings.comfy_output_dir)
    client = pile.enter_context(TestClient(app))
    client.faux = app.state.container.orchestrator._backend
    yield client
    pile.close()


@SANS_FFMPEG
def test_sans_appel_le_montage_tient_sur_la_conclusion_seule(atelier):
    """L'étape sautée rend ce que son « sinon » déclare : pas de livrable (la
    part est ignorée) et zéro image reprise (rien n'est rogné). Le montage
    retombe sur une part, sans rien savoir de tout cela."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-de-fin"}))
    assert job["status"] == "succeeded", job.get("problem")
    appel = [e for e in job["etapes"] if e["id"] == "appel"][0]
    assert appel["statut"] == "skipped"
    assert atelier.faux.runs == ["video-essai"]            # l'appel n'a pas tourné
    montage = [e for e in job["etapes"] if e["id"] == "montage"][0]
    assert montage["resultat"]["parts"] == 1
    assert "sans fichier : ignorée" in "\n".join(job["logs"])
    # Le contrôle aval a LU le « sinon » : sans lui, « $appel.recit.images_reprises »
    # restait un renvoi non résolu et le montage échouait.
    controle = [e for e in job["etapes"] if e["id"] == "controle"][0]
    assert [c["mesure"] for c in controle["resultat"]["controles"]] == [1, 0]
    assert 1.8 <= montage_video.mesurer(job["artifacts"][0]["path"])["duration_s"] <= 2.2


@SANS_FFMPEG
def test_avec_appel_la_conclusion_entre_sans_ce_que_l_appel_a_repris(atelier, monkeypatch):
    """Deux parts, et la conclusion amputée de ce que l'appel a déjà montré :
    sinon on verrait ces images-là deux fois."""
    # L'appel reçoit la conclusion en pièce jointe : un livrable local se DÉPOSE
    # chez le moteur, qui n'est pas là dans un essai. C'est le dépôt qu'on
    # simule, pas le montage.
    from comfyui_bridge.adapter import neutral

    class _Reponse:
        def read(self):
            return json.dumps({"name": "conclusion-deposee.mp4"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(neutral.urllib.request, "urlopen",
                        lambda req, timeout=None: _Reponse())
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-de-fin",
                                                         "cta": "La suite, bientôt"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert atelier.faux.runs == ["video-essai", "video-appel-essai"]
    montage = [e for e in job["etapes"] if e["id"] == "montage"][0]
    assert montage["resultat"]["parts"] == 2
    controle = [e for e in job["etapes"] if e["id"] == "controle"][0]
    assert [c["mesure"] for c in controle["resultat"]["controles"]] == [2, IMAGES_REPRISES]
    # 2 s de conclusion − 13 images + 1 s d'appel.
    attendu = 3.0 - IMAGES_REPRISES / CADENCE
    mesure = montage_video.mesurer(job["artifacts"][0]["path"])["duration_s"]
    assert abs(mesure - attendu) <= 0.2, mesure


# -- ce qu'une chaîne LIVRE --------------------------------------------------


@SANS_FFMPEG
def test_une_chaine_reussie_ne_livre_que_son_montage(atelier):
    """Antoine, 2026-09-16 : « maestro ne doit pas […] afficher les produits
    d'itérations, mais seulement la production finale montée ; il doit toujours
    livrer l'état terminé ». La conclusion, l'appel, les clips et les récits
    restent lisibles — dans les ÉTAPES et leurs sous-jobs —, mais ce ne sont pas
    des livraisons : les montrer à côté du montage faisait choisir entre
    plusieurs fichiers dont un seul était la vidéo commandée."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-de-fin"}))
    assert job["status"] == "succeeded", job.get("problem")
    montage = [e for e in job["etapes"] if e["id"] == "montage"][0]
    assert len(job["artifacts"]) == 1
    assert job["artifacts"][0]["path"] == montage["resultat"]["livrable"]
    assert "les 2 fichiers d'étapes restent lisibles" in "\n".join(job["logs"])
    # Ce que les étapes ont produit reste lisible là où ça a un sens.
    conclusion = [e for e in job["etapes"] if e["id"] == "conclusion"][0]
    assert pathlib.Path(conclusion["resultat"]["livrable"]).is_file()
    assert atelier.get(f"/v1/jobs/{conclusion['job_id']}").json()["artifacts"]


@SANS_FFMPEG
def test_en_echec_les_fichiers_deja_ecrits_restent_livres(atelier):
    """Rien ne change là : c'est en les regardant qu'on comprend pourquoi le
    contrôle a dit non."""
    job = _job(atelier, atelier.post("/v1/render",
                                     json={"workflow": "chaine-de-fin-jugee"}))
    assert job["status"] == "failed"
    assert job["problem"]["problem_kind"] == "controle-echoue"
    assert len(job["artifacts"]) >= 2
    assert all(pathlib.Path(a["path"]).is_file() for a in job["artifacts"])

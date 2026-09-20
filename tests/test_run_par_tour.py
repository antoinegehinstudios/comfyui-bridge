"""Un run par tour : un montage à blocs de boucle rendu tour après tour.

Envoyé entier, un montage tient tous ses blocs dans un seul prompt du moteur :
les modèles y restent en mémoire jusqu'au dernier nœud et la passerelle n'a
aucun point de contrôle entre deux blocs — mesuré le 2026-09-18, le poste a
tué le moteur au troisième bloc. Une boucle qui déclare « un_run_par_tour »
est rendue un tour par run : la garde de place joue avant chacun, le relais
(la dernière image) est écrit par un run, déposé chez le moteur, relu par le
suivant, et les tours sont recollés par copie de flux. Éprouvé ici de bout en
bout, avec un vrai ffmpeg et un backend qui MONTE le graphe de chaque run.
"""

import contextlib
import re
import json
import pathlib
import subprocess
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import montage_video  # noqa: E402
from comfyui_bridge.adapter.catalog import load_catalog  # noqa: E402
from comfyui_bridge.adapter.measure import measure  # noqa: E402
from comfyui_bridge.adapter.media import artifact_url, media_kind  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.errors import WorkflowMappingError  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import SANS_FFMPEG, BackendQuiLivre, _job  # noqa: E402

LARGEUR, HAUTEUR, CADENCE = 160, 120, 25

# Un montage dont chaque tour est un run : au premier tour l'amorce (texte →
# vidéo), ensuite le segment qui repart de la DERNIÈRE IMAGE du tour d'avant —
# le relais. Les types de nœuds sont ceux que le moteur connaît ; ici personne
# ne les exécute, c'est le CÂBLAGE de chaque run qui est éprouvé.
MONTAGE_PAR_TOURS = {
    "assemblage": 1,
    "constantes": {"prefixe_morceaux": "cortex/morceaux/bloc", "secondes_par_bloc": 1.0},
    "exemple": {"duration_s": 2},
    "livrable": {"morceaux": "$const.prefixe_morceaux"},
    "blocs": ["livrer"],
    "montage": [
        {"fragment": "commun", "sorties": {"modele": "1"}, "contenu": {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "h3.safetensors"}}}},
        {"pour": {"jusqu_a": "duration_s", "chaque": "secondes_par_bloc", "un_run_par_tour": True,
                  "relais": {"derniere_image": {
                      "ecrire": {"class_type": "SaveImage",
                                 "inputs": {"images": "$relais.port",
                                            "filename_prefix": "$relais.prefixe"}},
                      "lire": {"class_type": "LoadImage",
                               "inputs": {"image": "$relais.fichier"}}}}},
         "faire": [
             {"si": {"parametre": "tour", "op": "eq", "valeur": 0},
              "alors": [{"fragment": "amorce", "sorties": {"derniere_image": "1"}, "contenu": {
                  "1": {"class_type": "TexteVersVideo",
                        "inputs": {"model": ["$commun.modele", 0]}}}}],
              "sinon": [{"fragment": "segment", "sorties": {"derniere_image": "1"}, "contenu": {
                  "1": {"class_type": "ImageVersVideo",
                        "inputs": {"model": ["$commun.modele", 0],
                                   "first_frame": ["$precedent.derniere_image", 0]}}}}]},
             {"fragment": "livrer", "sorties": {"derniere_image": "1"}, "contenu": {
                 "1": {"class_type": "VHS_VideoCombine",
                       "inputs": {"images": ["$precedent.derniere_image", 0],
                                  "filename_prefix": {"$texte": "$const.prefixe_morceaux",
                                                      "$rang": "bloc_rang"}}}}},
         ]},
    ],
}

CHAINE_PAR_TOURS = {
    "version": 1, "chaine": "chaine-par-tours",
    "resume": "un montage rendu un tour par run",
    "expose": {"secondes": {"type": "FLOAT", "defaut": 3, "min": 1, "max": 6,
                            "libelle": "Durée", "unite": "s"}},
    "etapes": [{"id": "rendu", "rendre": {"workflow": "video-par-tours",
                                          "duration_s": "$secondes", "width": LARGEUR,
                                          "height": HAUTEUR, "fps": CADENCE}}],
    "livrable": "$rendu.livrable",
}


class BackendQuiTourne(BackendQuiLivre):
    """Un backend d'essai qui HONORE les tours.

    Il MONTE le graphe de chaque run comme le vrai backend le ferait — c'est
    là que la fenêtre du tour et les relais se voient —, écrit la part de vidéo
    de ce tour (même encodage pour toutes) et, sauf au dernier tour, l'image
    que le nœud « ecrire » du relais aurait écrite.
    """

    def __init__(self, sortie: pathlib.Path, catalog) -> None:
        super().__init__(sortie)
        self.catalog = catalog
        self.tours_recus: list[tuple] = []
        self.graphes: list[dict] = []

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.runs)), "attached")
        if on_started:
            on_started()
        if on_progress:
            on_progress(1, 1, "1")
        tour = plan.params.get("tour")
        self.tours_recus.append((tour, plan.params.get("relais_derniere_image")))
        graphe, _ = self.catalog.monter(self.catalog.get_spec(plan.workflow), dict(plan.params))
        self.graphes.append(graphe)
        if self.avant is not None:
            self.avant(plan)
        dossier = self.sortie / "cortex"
        dossier.mkdir(parents=True, exist_ok=True)
        nom = str(plan.params.get("filename_prefix", "cortex/essai")).rsplit("/", 1)[-1]
        fichier = dossier / f"{nom}_recolle.mp4"
        subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"testsrc=size={LARGEUR}x{HAUTEUR}:rate={CADENCE}:duration=1",
                        "-pix_fmt", "yuv420p", str(fichier)], check=True)
        artefacts = [Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                              url=artifact_url(self.sortie, fichier), bytes=fichier.stat().st_size,
                              measured=measure(fichier) or None)]
        ecrit = next((v for v in graphe.values() if v["class_type"] == "SaveImage"), None)
        if ecrit is not None:
            prefixe = str(ecrit["inputs"]["filename_prefix"]).rsplit("/", 1)[-1]
            image = dossier / f"{prefixe}_00001_.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
            artefacts.append(Artifact(kind=media_kind(image), path=str(image.resolve()),
                                      url=artifact_url(self.sortie, image),
                                      bytes=image.stat().st_size))
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


class BackendQuiRejoue(BackendQuiTourne):
    """Le même banc, mais chaque tour ≥ 2 REJOUE les 10 dernières images du
    tour précédent avant de continuer — ce qu'un bloc continué par référence
    fait en vrai (mesuré le 2026-09-20). Le motif testsrc compte les images :
    le tour t livre les images [25·t − 10, 25·t + 25) pour t ≥ 1, [0, 25) pour
    le premier."""

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        resultat = super().submit(plan, on_enqueued, on_progress, on_note, on_started)
        tour = int(plan.params.get("tour") or 0)
        fichier = pathlib.Path(resultat.artifacts[0].path)
        depuis, nombre = (0, 25) if tour == 0 else (25 * tour - 10, 35)
        subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"testsrc=size={LARGEUR}x{HAUTEUR}:rate={CADENCE}:duration=10",
                        "-vf", f"select='between(n,{depuis},{depuis + nombre - 1})',setpts=N/{CADENCE}/TB",
                        "-r", str(CADENCE), "-pix_fmt", "yuv420p", str(fichier)], check=True)
        return resultat


@pytest.fixture()
def banc(monkeypatch):
    pile = contextlib.ExitStack()
    # Le dépôt d'un relais chez le moteur : ici, le nom suffit.
    monkeypatch.setattr("comfyui_bridge.adapter.neutral.upload_image",
                        lambda base, nom, contenu, *reste, **autres: nom)

    def batir(backend=None, **plus) -> TestClient:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_tours_"))
        (tmp / "video-par-tours.json").write_text(json.dumps(MONTAGE_PAR_TOURS), encoding="utf-8")
        (tmp / "chaine-par-tours.json").write_text(json.dumps(CHAINE_PAR_TOURS), encoding="utf-8")
        (tmp / "reconciliation.local.json").write_text(json.dumps({
            "categories": {"essais": {"titre": "Essais", "ordre": 1}},
            "workflows": {
                "video-par-tours": {"kind": "video", "workflow": str(tmp / "video-par-tours.json"),
                                    "bindings": {},
                                    "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
                "chaine-par-tours": {"kind": "video", "chaine": str(tmp / "chaine-par-tours.json"),
                                     "titre": "Chaîne par tours", "categorie": "essais", "ordre": 1},
            },
        }, ensure_ascii=False), encoding="utf-8")
        settings = Settings(comfy_backend="cli", dry_run=True,
                            comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                            hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                            hermes_mode="local", workflows_dir=tmp / "workflows", **plus)
        app = create_app(settings)
        faux = (backend or BackendQuiTourne)(settings.comfy_output_dir, app.state.container.catalog)
        app.state.container.orchestrator._backend = faux
        client = pile.enter_context(TestClient(app))
        client.faux = faux
        client.tmp = tmp
        return client

    yield batir
    pile.close()


def _etape(job: dict, ident: str) -> dict:
    return [e for e in job["etapes"] if e["id"] == ident][0]


def _types(g):
    return sorted(v["class_type"] for v in g.values())


# -- de bout en bout -----------------------------------------------------------


@SANS_FFMPEG
def test_chaque_tour_est_un_run_et_le_relais_passe_de_l_un_a_l_autre(banc):
    atelier = banc()
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 3, "label": "tours"}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert rendu["tours"] == 3 and len(rendu["job_ids"]) == 3
    assert rendu["note"] == "3 tours" and rendu["resultat"]["tours"] == 3
    # Chaque run a reçu SON tour ; les deux derniers, le relais écrit par le
    # run d'avant, déposé chez le moteur sous le nom du fichier.
    tours = [t for t, _ in atelier.faux.tours_recus]
    relais = [r for _, r in atelier.faux.tours_recus]
    assert tours == [0, 1, 2]
    assert relais[0] is None
    assert relais[1] and relais[1].endswith("_relais_derniere_image_00001_.png")
    assert relais[2] and relais[2] != relais[1]
    journal = "\n".join(job["logs"])
    assert "rendu en 3 runs, un par tour de boucle" in journal
    assert "relais « derniere_image » du tour 1" in journal
    # Un montage à un run par tour ne se monte pas d'un seul tenant pour être
    # tranché : la question ne se pose pas, et le journal n'en parle pas.
    assert "tranches" not in journal
    # Trois runs ordinaires, visibles, qui disent de qui ils sont.
    for sous_id in rendu["job_ids"]:
        sous = atelier.get(f"/v1/jobs/{sous_id}").json()
        assert sous["parent"] == job["id"] and sous["status"] == "succeeded"


@SANS_FFMPEG
def test_le_graphe_de_chaque_run_est_la_fenetre_de_son_tour(banc):
    atelier = banc()
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 3, "label": "fenetres"}))
    assert job["status"] == "succeeded", job.get("problem")
    premier, milieu, dernier = atelier.faux.graphes
    # Le premier run : l'amorce, le commun, la livraison, et le relais écrit.
    assert _types(premier) == ["SaveImage", "TexteVersVideo", "UNETLoader", "VHS_VideoCombine"]
    # Au milieu : le segment repart du relais RELU, et réécrit le sien.
    assert _types(milieu) == ["ImageVersVideo", "LoadImage", "SaveImage", "UNETLoader",
                              "VHS_VideoCombine"]
    lu = next(k for k, v in milieu.items() if v["class_type"] == "LoadImage")
    segment = next(v for v in milieu.values() if v["class_type"] == "ImageVersVideo")
    assert segment["inputs"]["first_frame"] == [lu, 0]
    assert milieu[lu]["inputs"]["image"] == atelier.faux.tours_recus[1][1]
    # Le dernier n'écrit aucun relais.
    assert _types(dernier) == ["ImageVersVideo", "LoadImage", "UNETLoader", "VHS_VideoCombine"]
    # Et chaque run nomme ses morceaux à son rang dans le montage ENTIER.
    rangs = [next(v for v in g.values() if v["class_type"] == "VHS_VideoCombine")
             ["inputs"]["filename_prefix"].rsplit("_", 1)[-1] for g in atelier.faux.graphes]
    assert rangs == ["000", "001", "002"]


@SANS_FFMPEG
def test_les_tours_sont_recolles_sans_reencodage_et_les_jonctions_mesurees(banc):
    atelier = banc()
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 3, "label": "joint"}))
    assert job["status"] == "succeeded", job.get("problem")
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file() and livrable.suffix == ".mp4"
    assert 2.7 <= montage_video.mesurer(livrable)["duration_s"] <= 3.3
    journal = "\n".join(job["logs"])
    assert "3 tours recollés sans ré-encodage" in journal
    assert "jonctions mesurées" in journal
    # UN TOUR N'EST JAMAIS UNE LIVRAISON : le job ne porte que le recollé.
    assert len(job["artifacts"]) == 1
    assert _etape(job, "rendu")["resultat"]["jonctions"]["nombre"] == 2


@SANS_FFMPEG
def test_un_tour_qui_rejoue_la_fin_du_precedent_est_coupe_au_recollage(banc):
    """Antoine, 2026-09-20 : « une coupure se fait mal, l'image semble revenir
    en arrière et puis continuer ». Mesuré (job 03e675b0) : un bloc continué
    par référence rejoue la fin du bloc précédent. Le recollage cherche l'image
    de raccord dans chaque tour et jette ce qui la précède ; les tours sont
    alors ré-encodés une fois, la coupe est dite, et la jonction se mesure sur
    le montage réel."""
    atelier = banc(BackendQuiRejoue)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 3, "label": "rejoue"}))
    assert job["status"] == "succeeded", job.get("problem")
    journal = "\n".join(job["logs"])
    # le motif testsrc bouge peu d'une image à l'autre : le raccord tombe sur
    # l'image rejouée à une image près (mesuré : 10 puis 9)
    assert re.search(r"le tour 2 rejoue la fin du tour 1 — ses (9|10|11) premières images sont jetées", journal)
    assert re.search(r"le tour 3 rejoue la fin du tour 2 — ses (9|10|11) premières images sont jetées", journal)
    assert "3 tours recollés en ré-encodant" in journal and "images jetées aux raccords : " in journal
    jonctions = _etape(job, "rendu")["resultat"]["jonctions"]
    assert all(9 <= c <= 11 for c in jonctions["coupes"]) and len(jonctions["coupes"]) == 2
    assert jonctions["nombre"] == 2 and jonctions["pire"] > 0.9
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    # 25 + ~25 + ~25 images gardées à 25 i/s : ≈ 3 s, pas 3,8
    assert 2.8 <= montage_video.mesurer(livrable)["duration_s"] <= 3.3


@SANS_FFMPEG
def test_un_seul_tour_reste_un_run_ordinaire(banc):
    atelier = banc()
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 1, "label": "seul"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert atelier.faux.tours_recus == [(None, None)]
    assert "tours" not in _etape(job, "rendu")


@SANS_FFMPEG
def test_un_arret_demande_est_honore_entre_deux_tours(banc):
    atelier = banc()
    job_id = {}

    def arreter(_plan):
        atelier.post(f"/v1/jobs/{job_id['id']}/cancel")

    atelier.faux.avant = arreter
    reponse = atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                               "secondes": 3, "label": "arret"})
    job_id["id"] = reponse.json()["id"]
    job = _job(atelier, reponse)
    assert job["status"] in ("cancelled", "failed")
    assert len(atelier.faux.tours_recus) == 1        # le deuxième tour n'a pas été lancé


@SANS_FFMPEG
def test_un_tour_qui_n_ecrit_pas_son_relais_se_lit(banc):
    atelier = banc()

    def sans_relais(plan):
        # Le nœud « ecrire » disparaît du graphe monté : rien n'est écrit.
        atelier.faux.graphes[-1] = {k: v for k, v in atelier.faux.graphes[-1].items()
                                    if v["class_type"] != "SaveImage"}
        # …et le backend ne fabrique donc pas l'image (il lit son propre graphe).
    atelier.faux.avant = sans_relais
    original = BackendQuiTourne.submit

    def submit_sans_image(self, plan, **kw):
        resultat = original(self, plan, **kw)
        resultat.artifacts[:] = [a for a in resultat.artifacts if a.kind != "image"]
        return resultat
    atelier.faux.submit = submit_sans_image.__get__(atelier.faux, BackendQuiTourne)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-par-tours",
                                                         "secondes": 3, "label": "muet"}))
    assert job["status"] == "failed"
    assert "n'a écrit aucun relais (derniere_image)" in job["problem"]["detail"]


# -- le catalogue --------------------------------------------------------------


def test_une_liaison_vers_une_variante_absente_ne_gene_que_si_son_parametre_est_fourni(tmp_path):
    """Une variante sous « si » (les références d'image, quand une image est
    jointe) porte des liaisons déclarées : sans l'image, la variante n'est pas
    posée et la liaison n'a rien à écrire ; avec elle, un fragment absent est
    une vraie faute, dite."""
    wf = tmp_path / "wf"
    wf.mkdir()
    montage = {"assemblage": 1, "exemple": {"n": 1}, "montage": [
        {"fragment": "commun", "contenu": {"1": {"class_type": "Charger", "inputs": {}}}},
        {"si": {"parametre": "image", "op": "ne", "valeur": None},
         "alors": [{"fragment": "references", "contenu": {
             "1": {"class_type": "LoadImage", "inputs": {"image": "exemple.png"}}}}]},
    ]}
    (wf / "m.json").write_text(json.dumps(montage), encoding="utf-8")
    rec = tmp_path / "reconciliation.json"
    rec.write_text(json.dumps({"default": "m", "workflows": {"m": {
        "kind": "video", "workflow": str(wf / "m.json"),
        "bindings": {"image": {"node": "$references.1", "input": "image"}}}}}), encoding="utf-8")
    cat = load_catalog(rec, workflows_dir=wf)
    spec = cat.get_spec("m")
    g, liaisons = cat.monter(spec, {"n": 1})
    assert "image" not in liaisons and "LoadImage" not in _types(g)
    g, liaisons = cat.monter(spec, {"n": 1, "image": "photo.png"})
    assert "LoadImage" in _types(g) and liaisons["image"].node in g
    sans = json.loads(json.dumps(montage))
    sans["montage"][1]["alors"][0]["fragment"] = "autre"
    (wf / "m.json").write_text(json.dumps(sans), encoding="utf-8")
    cat = load_catalog(rec, workflows_dir=wf)
    with pytest.raises(WorkflowMappingError, match="references") as e:
        cat.monter(cat.get_spec("m"), {"n": 1, "image": "photo.png"})
    # Le refus nomme le RÉGLAGE et ce qui lui manque, pas seulement le nœud :
    # « le nœud '22' n'existe pas dans 'amorce' » ne disait rien à qui avait
    # retiré une image en laissant son rôle (2026-09-20).
    assert "« image » n'a pas de place dans ce dépliage" in e.value.detail
    assert "ne s'applique pas avec les pièces jointes fournies" in e.value.detail
    assert e.value.extensions["field"] == "image"
    assert e.value.extensions["cible"] == "$references.1"


def test_le_catalogue_compte_les_tours_et_monte_la_fenetre_d_un_tour(tmp_path):
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / "m.json").write_text(json.dumps(MONTAGE_PAR_TOURS), encoding="utf-8")
    rec = tmp_path / "reconciliation.json"
    rec.write_text(json.dumps({"default": "m", "workflows": {
        "m": {"kind": "video", "workflow": str(wf / "m.json"), "bindings": {}}}}), encoding="utf-8")
    cat = load_catalog(rec, workflows_dir=wf)
    spec = cat.get_spec("m")
    # « tour » et le relais PILOTENT le montage : ce sont des champs reçus.
    assert {"tour", "relais_derniere_image", "duration_s"} <= set(spec.profile.accepts)
    assert cat.tours_separes(spec, {"duration_s": 3}) == 3
    assert cat.tours_separes(spec, {"duration_s": 1}) is None
    assert sorted(cat.relais_de(spec)) == ["derniere_image"]
    g, _ = cat.monter(spec, {"duration_s": 3, "tour": 1, "relais_derniere_image": "a.png",
                             "filename_prefix": "cortex/essai"})
    assert _types(g) == ["ImageVersVideo", "LoadImage", "SaveImage", "UNETLoader", "VHS_VideoCombine"]
    ecrit = next(v for v in g.values() if v["class_type"] == "SaveImage")
    assert ecrit["inputs"]["filename_prefix"] == "cortex/essai_relais_derniere_image"
    # Sans « tour », le montage entier : ce que /io décrit.
    entier, _ = cat.monter(spec, {"duration_s": 3})
    assert _types(entier).count("VHS_VideoCombine") == 3 and "LoadImage" not in _types(entier)
    # « tour » sur un montage qui ne le demande pas se lit.
    sans = dict(MONTAGE_PAR_TOURS)
    sans["montage"] = json.loads(json.dumps(MONTAGE_PAR_TOURS["montage"]))
    del sans["montage"][1]["pour"]["un_run_par_tour"]
    (wf / "s.json").write_text(json.dumps(sans), encoding="utf-8")
    rec.write_text(json.dumps({"default": "s", "workflows": {
        "s": {"kind": "video", "workflow": str(wf / "s.json"), "bindings": {}}}}), encoding="utf-8")
    cat = load_catalog(rec, workflows_dir=wf)
    with pytest.raises(WorkflowMappingError, match="un_run_par_tour"):
        cat.monter(cat.get_spec("s"), {"duration_s": 3, "tour": 1})

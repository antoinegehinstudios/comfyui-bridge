"""Composer une IMAGE FIXE : la taille exacte, un texte dans une police du poste, un logo tel quel.

Le pendant, pour une image, de ce que `recoller` fait à une vidéo — éprouvé
sur de vraies images par le vrai encodeur : la taille livrée est celle qu'on a
demandée (couvrir puis rogner au centre, jamais de bandes), le texte est posé
(les pixels changent là où il est, et nulle part ailleurs), un logo se
retrouve à sa place, un texte vide est compté sans faute, une image absente
refuse. Et une chaîne qui porte une étape « composer » livre un PNG à la
taille demandée avec, au récit, ce qui a été posé.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest
from PIL import Image

from comfyui_bridge.adapter import composition_image, montage_video
from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core.errors import MediaAssemblyError, WorkflowMappingError

SANS_FFMPEG = pytest.mark.skipif(not montage_video.disponible(), reason="ffmpeg absent")
POLICE = pathlib.Path("C:/Windows/Fonts/arial.ttf")
SANS_POLICE = pytest.mark.skipif(not POLICE.is_file(), reason="aucune police du poste")


def _degrade(chemin: pathlib.Path, taille=(272, 340), couleur=(40, 90, 160)) -> pathlib.Path:
    """Une image d'épreuve : un aplat, pour que tout pixel changé se voie."""
    Image.new("RGB", taille, couleur).save(chemin)
    return chemin


def _logo(chemin: pathlib.Path, taille=(60, 20)) -> pathlib.Path:
    im = Image.new("RGBA", taille, (0, 0, 0, 0))
    for x in range(4, taille[0] - 4):
        for y in range(3, taille[1] - 3):
            im.putpixel((x, y), (240, 120, 10, 255))
    im.save(chemin)
    return chemin


def _part_changee(fichier: pathlib.Path, couleur, bande: tuple[float, float] | None = None) -> float:
    """La part des pixels qui ne sont plus l'aplat, sur toute l'image ou une bande de hauteur."""
    with Image.open(fichier) as im:
        im = im.convert("RGB")
        y0, y1 = (0, im.height) if bande is None else (int(im.height * bande[0]), int(im.height * bande[1]))
        pixels = [im.getpixel((x, y)) for y in range(y0, y1, 2) for x in range(0, im.width, 2)]
    loin = sum(1 for p in pixels if max(abs(p[i] - couleur[i]) for i in range(3)) > 24)
    return loin / max(1, len(pixels))


def test_le_cadrage_couvre_sans_deformer_et_compte_ce_qu_il_rogne():
    # Les multiples de seize d'un modèle, ramenés à la taille demandée : rien à rogner.
    assert composition_image.cadrage((1088, 1360), (1080, 1350)) == {
        "facteur": 0.9926, "rogne_px": {"largeur": 0, "hauteur": 0}}
    # Deux proportions différentes : couvrir, puis rogner ce qui dépasse — jamais de bandes.
    c = composition_image.cadrage((1024, 1024), (1080, 1350))
    assert c["facteur"] == 1.3184 and c["rogne_px"] == {"largeur": 270, "hauteur": 0}
    assert composition_image.cadrage((720, 1280), (720, 1280)) == {
        "facteur": 1.0, "rogne_px": {"largeur": 0, "hauteur": 0}}


@SANS_FFMPEG
def test_l_image_est_livree_a_la_taille_demandee_sans_bande(tmp_path):
    source = _degrade(tmp_path / "source.png", (272, 340))
    fait = composition_image.composer(source, tmp_path / "livree.png", 270, 337)
    assert fait["mesure"]["width"] == 270 and fait["mesure"]["height"] == 337
    assert fait["mesure"]["bytes"] > 0 and pathlib.Path(fait["livrable"]).is_file()
    assert fait["source"] == {"width": 272, "height": 340, "fichier": str(source.resolve())}
    assert fait["cadrage"]["rogne_px"]["largeur"] <= 2 and fait["cadrage"]["rogne_px"]["hauteur"] <= 2
    # Rien posé : l'aplat est intact (aucune bande, aucun pixel étranger).
    assert _part_changee(pathlib.Path(fait["livrable"]), (40, 90, 160)) == 0.0
    assert fait["textes_demandes"] == 0 and fait["textes_poses"] == 0 and fait["images_posees"] == []
    # Sans taille demandée, la sienne.
    meme = composition_image.composer(source, tmp_path / "meme.png")
    assert (meme["mesure"]["width"], meme["mesure"]["height"]) == (272, 340)


@SANS_FFMPEG
@SANS_POLICE
def test_un_texte_est_pose_la_ou_on_le_demande_et_dit(tmp_path):
    source = _degrade(tmp_path / "source.png", (400, 500))
    dits = []
    fait = composition_image.composer(
        source, tmp_path / "livree.png", 400, 500,
        textes=[{"texte": "Bonjour", "police": str(POLICE), "position": "haut", "taille": 0.08,
                 "couleur": "white", "debut_s": 0.5, "fin_s": 4.5, "fondu_s": 0.4},
                {"texte": "", "police": str(POLICE), "position": "bas"}],
        signaler=dits.append)
    livree = pathlib.Path(fait["livrable"])
    # Le haut porte le texte, le bas reste l'aplat : un texte se pose où on le dit.
    assert _part_changee(livree, (40, 90, 160), (0.0, 0.3)) > 0.01
    assert _part_changee(livree, (40, 90, 160), (0.5, 1.0)) == 0.0
    assert fait["textes_demandes"] == 1 and fait["textes_poses"] == 1 and fait["textes_vides"] == 1
    dit = fait["textes_dits"]
    assert dit[0]["pose"] is True and dit[0]["lignes"] >= 1 and dit[0]["taille_px"] == 40
    assert dit[0]["position"] == "haut" and dit[1]["pose"] is False
    assert any("1 texte(s) posé(s)" in d for d in dits)


@SANS_FFMPEG
def test_un_logo_est_pose_tel_quel_a_sa_place(tmp_path):
    source = _degrade(tmp_path / "source.png", (400, 400))
    logo = _logo(tmp_path / "logo.png")
    fait = composition_image.composer(
        source, tmp_path / "livree.png", 400, 400,
        images=[{"fichier": str(logo), "ancrage": "bas-droite", "largeur": 0.3, "marge": 0.05}])
    assert len(fait["images_posees"]) == 1
    pose = fait["images_posees"][0]
    assert pose["largeur"] == 120 and pose["ancrage"] == "bas-droite"
    with Image.open(fait["livrable"]) as im:
        centre = im.convert("RGB").getpixel((pose["x"] + pose["largeur"] // 2, pose["y"] + pose["hauteur"] // 2))
        coin = im.convert("RGB").getpixel((10, 10))
    assert max(abs(centre[i] - (240, 120, 10)[i]) for i in range(3)) <= 6
    assert coin == (40, 90, 160)


def test_une_image_absente_ou_une_liste_fausse_refuse(tmp_path):
    with pytest.raises(MediaAssemblyError, match="introuvable"):
        composition_image.composer(tmp_path / "rien.png", tmp_path / "x.png")
    if montage_video.disponible():
        source = _degrade(tmp_path / "source.png")
        with pytest.raises(MediaAssemblyError, match="textes"):
            composition_image.composer(source, tmp_path / "x.png", textes="pas une liste")


def test_l_apercu_d_une_image_est_une_vignette_webp(tmp_path):
    source = _degrade(tmp_path / "source.png", (800, 1000))
    fait = composition_image.apercu_fixe(source, tmp_path / "apercus" / "mode")
    assert fait["format"] == "webp" and fait["images"] == 1
    with Image.open(fait["fichier"]) as im:
        assert im.size == (240, 300)
    # Le répartiteur : une image fait un aperçu fixe, autre chose refuse.
    assert composition_image.apercu_de_livrable(source, tmp_path / "apercus" / "mode2")["format"] == "webp"
    with pytest.raises(MediaAssemblyError):
        composition_image.apercu_de_livrable(tmp_path / "recit.json", tmp_path / "apercus" / "mode3")


def test_la_chaine_admet_une_etape_composer_et_refuse_ce_qu_elle_ne_connait_pas():
    brut = {"version": 1, "chaine": "c", "resume": "r",
            "expose": {"largeur": {"type": "INT", "defaut": 64, "libelle": "L"}},
            "etapes": [{"id": "rendu", "rendre": {"workflow": "g", "width": "$largeur"}},
                       {"id": "livraison", "composer": {"image": "$rendu.livrable", "largeur": "$largeur",
                                                        "hauteur": "$largeur", "textes": []}}],
            "livrable": "$livraison.livrable"}
    chaine = noyau.lire(brut, "c")
    assert [(e.id, e.genre) for e in chaine.etapes] == [("rendu", "rendre"), ("livraison", "composer")]
    faux = json.loads(json.dumps(brut))
    faux["etapes"][1]["composer"]["fps"] = 25
    with pytest.raises(WorkflowMappingError, match="inconnu"):
        noyau.lire(faux, "c")
    del faux["etapes"][1]["composer"]["fps"]
    del faux["etapes"][1]["composer"]["image"]
    with pytest.raises(WorkflowMappingError, match="requis"):
        noyau.lire(faux, "c")


@SANS_FFMPEG
def test_une_chaine_qui_compose_livre_un_png_a_la_taille_demandee():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from comfyui_bridge.api.main import create_app
    from comfyui_bridge.config import Settings
    from test_chaines_api import BackendQuiLivre, _job

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_composer_"))
    chaine = {
        "version": 1, "chaine": "chaine-composee", "resume": "un rendu, puis l'image composée",
        "expose": {"largeur": {"type": "INT", "defaut": 96, "libelle": "Largeur", "unite": "px"},
                   "hauteur": {"type": "INT", "defaut": 120, "libelle": "Hauteur", "unite": "px"},
                   "message": {"type": "STRING", "defaut": "", "libelle": "Message"}},
        "etapes": [
            {"id": "rendu", "rendre": {"workflow": "sd15-txt2img", "prompt": "essai",
                                       "width": 100, "height": 124}},
            {"id": "livraison", "composer": {"image": "$rendu.livrable", "largeur": "$largeur",
                                             "hauteur": "$hauteur",
                                             "textes": [{"texte": "$message", "police": str(POLICE),
                                                         "position": "bas", "taille": 0.1}]}},
            {"id": "constat", "constater": [
                {"id": "la_largeur_est_tenue", "valeur": "$livraison.mesure.width", "op": "eq",
                 "attendu": "$largeur", "aide": "la taille livrée est celle demandée"},
                {"id": "le_texte_est_pose", "valeur": "$livraison.textes_poses", "op": "eq",
                 "attendu": "$livraison.textes_demandes", "aide": "chaque texte demandé est posé"}]},
        ],
        "livrable": "$livraison.livrable",
    }
    (tmp / "chaine-composee.json").write_text(json.dumps(chaine, ensure_ascii=False), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {"chaine-composee": {"kind": "image", "chaine": str(tmp / "chaine-composee.json"),
                                          "titre": "Chaîne composée", "categorie": "essais", "ordre": 1}},
    }, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "hermes.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local", workflows_dir=tmp / "workflows")
    app = create_app(settings)
    app.state.container.orchestrator._backend = BackendQuiLivre(settings.comfy_output_dir)
    with TestClient(app) as client:
        corps = {"workflow": "chaine-composee", "label": "essai", "largeur": 96, "hauteur": 120}
        if POLICE.is_file():
            corps["message"] = "Bonjour"
        r = client.post("/v1/render", json=corps)
        assert r.status_code == 202, r.text
        job = _job(client, r)
        assert job["status"] == "succeeded", job.get("problem")
        livrable = pathlib.Path(job["artifacts"][0]["path"])
        assert livrable.suffix == ".png" and livrable.is_file()
        with Image.open(livrable) as im:
            assert im.size == (96, 120)
        etapes = {e["id"]: e for e in job["etapes"]}
        livraison = etapes["livraison"]["resultat"]
        assert livraison["mesure"]["width"] == 96 and livraison["cadrage"]["facteur"] == 0.9677
        assert livraison["textes_poses"] == livraison["textes_demandes"] == (1 if POLICE.is_file() else 0)
        assert etapes["constat"]["resultat"]["non_tenus"] == []
        # Le livrable image fait l'aperçu du mode, et le dit.
        r = client.put("/v1/workflows/chaine-composee/apercu", json={"job_id": job["id"]})
        assert r.status_code == 201 and r.json()["format"] == "webp"
        assert client.get("/v1/workflows/chaine-composee/apercu").status_code == 200


@SANS_FFMPEG
@SANS_POLICE
def test_un_texte_se_pose_sous_le_precedent_a_la_hauteur_reelle_de_son_bloc(tmp_path):
    """Mesuré le 2026-09-24 sur un visuel sous charte : un message replié sur
    trois lignes recouvrait le sous-titre posé à un décalage écrit d'avance. Le
    sous-titre se pose SOUS le bloc réel du message ; un groupe qui déborderait
    du bas remonte d'autant, et chaque texte dit son décalage."""
    source = _degrade(tmp_path / "source.png", (600, 750))
    long = "Tremplin deux mille vingt-sept : inscrivez votre groupe avant la fin du mois"
    fait = composition_image.composer(
        source, tmp_path / "livree.png", 600, 750,
        textes=[{"texte": long, "police": str(POLICE), "position": "bas", "taille": 0.09, "couleur": "white"},
                {"texte": "12 et 13 juin", "police": str(POLICE), "position": "bas", "taille": 0.045,
                 "couleur": "white", "sous_le_precedent": True}])
    titre, sous = fait["textes_dits"]
    assert titre["pose"] and sous["pose"] and titre["lignes"] >= 2
    # le sous-titre est plus bas que le bas du bloc du titre, et le groupe tient dans l'image
    H = 750
    bas_du_titre = (0.80 + titre["decalage"]) * H + titre["lignes"] * round(titre["taille_px"] * 1.25) / 2
    haut_du_sous = (0.80 + sous["decalage"]) * H - round(sous["taille_px"] * 1.25) / 2
    assert haut_du_sous >= bas_du_titre - 1
    assert (0.80 + sous["decalage"]) * H + round(sous["taille_px"] * 1.25) / 2 <= H
    assert titre["decalage"] < 0                      # le groupe a remonté : le titre est au-dessus de son ancre
    # sans « sous_le_precedent », deux textes à la même position partagent l'ancre (comme au recollage)
    plat = composition_image.empiler([{"texte": "a", "police": str(POLICE), "position": "bas"},
                                      {"texte": "b", "police": str(POLICE), "position": "bas"}], 600, 750)
    assert "decalage" not in plat[0] and "decalage" not in plat[1]
    # un texte vide ou une autre position ne s'empile pas
    autre = composition_image.empiler([{"texte": "a", "police": str(POLICE), "position": "bas"},
                                       {"texte": "", "police": str(POLICE), "position": "bas", "sous_le_precedent": True},
                                       {"texte": "c", "police": str(POLICE), "position": "haut", "sous_le_precedent": True}], 600, 750)
    assert "decalage" not in autre[2] and "sous_le_precedent" not in autre[2]


@SANS_FFMPEG
def test_une_image_sans_fichier_dit_pourquoi_elle_n_est_pas_posee(tmp_path):
    """Le logo décidé absent (image d'ambiance) n'est pas posé, et sa raison — celle de l'étape qui a décidé —
    va au journal et au récit ; un logo posé dit aussi la sienne."""
    source = _degrade(tmp_path / "source.png", (400, 400))
    dits: list[str] = []
    fait = composition_image.composer(
        source, tmp_path / "livree.png", 400, 400, signaler=dits.append,
        images=[{"fichier": None, "ancrage": "haut-centre", "raison": "une image d'ambiance : pas de logo"}])
    assert fait["images_posees"] == [] and fait["images_non_posees"] == [{"rang": 0, "raison": "une image d'ambiance : pas de logo"}]
    assert any("image 1 non posée : une image d'ambiance" in d for d in dits)
    with Image.open(fait["livrable"]) as im:
        assert im.convert("RGB").getpixel((200, 20)) == (40, 90, 160)          # rien n'est posé en haut
    logo = _logo(tmp_path / "logo.png")
    dits.clear()
    fait = composition_image.composer(
        source, tmp_path / "livree-2.png", 400, 400, signaler=dits.append,
        images=[{"fichier": str(logo), "ancrage": "bas-droite", "largeur": 0.3, "marge": 0.05, "raison": "un message est posé"}])
    assert len(fait["images_posees"]) == 1 and fait["images_non_posees"] == []
    assert any("image 1 posée : un message est posé" in d for d in dits)
    # sans raison, le mot d'avant
    fait = composition_image.composer(source, tmp_path / "livree-3.png", 400, 400, images=[{"fichier": ""}])
    assert fait["images_non_posees"][0]["raison"] == "aucun fichier à poser (rien d'imposé)"


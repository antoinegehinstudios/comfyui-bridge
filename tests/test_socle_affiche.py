"""« RÉVÉLER UNE AFFICHE » — le témoin de ce type d'essai (2026-09-20).

Antoine : « je veux un nouveau type de workflow dans maestro. J'envoie une
affiche ou un logo et le pipeline me le révèle en quelques secondes avec un
effet cinématique en prenant en compte les tonalités de couleur présentes. Un
nouveau type, pour test. Le contenu doit toujours rester inchangé. » Puis, le
même jour : « il serait bien de pouvoir déclarer un fond, ou autre élément
supplémentaire avec un commentaire, pour qu'il pèse. »

Ce qui est épinglé ici : quatre étapes (lecture → rendre → constater →
verifier), les quinze champs et leurs défauts, la lecture gardée par clé, les
éléments PRÉSENTS PAR LEUR NOM (une entrée de nœud par élément), l'intégrité
CONSTATÉE (jamais refusée — seul le fichier vide refuse), la copie versionnée
identique aux données du poste, et — sur ce poste — les graphes qui existent,
le nœud de révélation qui honore les tranches et les liaisons qui visent des
nœuds qui existent. Aucun nom de flux dans le code de la passerelle : tout ce
mode est des DONNÉES.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core import reconciliant

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
DONNEES = RACINE / "_data"

PLAN = [("lecture", "rendre"), ("revelation", "rendre"), ("integrite", "constater"),
        ("controle", "verifier")]
ELEMENTS = ("image_2", "image_3", "image_4")
DEFAUTS = {"duration_s": 5, "tenue_s": 1.5, "effet": "emergence", "fond": "tons",
           "width": 720, "height": 1280, "fps": 30, "seed": 71,
           "commentaire_2": "", "commentaire_3": "", "commentaire_4": ""}
CONSTATS = ["l_affiche_est_intacte", "la_tenue_est_tenue", "la_duree_est_exacte",
            "la_lecture_a_eu_lieu", "chaque_element_pese", "l_affiche_n_est_pas_couverte"]
ENTREES_DU_NOEUD = {"7.effet": "$effet", "7.tenue_s": "$tenue_s", "7.fond": "$fond",
                    "7.image_2_nom": "$image_2", "7.image_3_nom": "$image_3", "7.image_4_nom": "$image_4",
                    "7.reglages_json": "$lecture.recit.reglages_json"}
CLE_DE_LA_LECTURE = ["$image", "$image_2", "$image_3", "$image_4", "$commentaire_2", "$commentaire_3",
                     "$commentaire_4", "$effet", "$fond", "$duration_s", "$tenue_s", "$width", "$height",
                     "$seed"]


@pytest.fixture(scope="module")
def affiche():
    return reconciliant.deplier(json.loads((EXEMPLES / "video-affiche.json").read_text(encoding="utf-8")))


def test_quatre_etapes_et_un_seul_livrable(affiche):
    chaine = noyau.lire(affiche, "video-affiche")
    assert [(e.id, e.genre) for e in chaine.etapes] == PLAN
    assert affiche["livrable"] == "$revelation.livrable"
    rendre = affiche["etapes"][1]["rendre"]
    assert rendre["workflow"] == "video-affiche-cinematique"
    assert rendre["media"] == {"image": "$image", **{nom: "$" + nom for nom in ELEMENTS}}
    assert {k: rendre[k] for k in ("duration_s", "fps", "width", "height", "seed")} == {
        "duration_s": "$duration_s", "fps": "$fps", "width": "$width", "height": "$height",
        "seed": "$seed"}
    assert rendre["inputs"] == ENTREES_DU_NOEUD


def test_la_lecture_est_gardee_par_cle_et_lit_tout_ce_qui_pese(affiche):
    """Le réalisateur ne se rappelle pas pour la même demande ; et il reçoit
    chaque élément PAR SON NOM, chaque commentaire, et le contexte."""
    lecture = affiche["etapes"][0]["rendre"]
    assert lecture["workflow"] == "affiche-lecture"
    assert lecture["media"] == {"image": "$image", **{nom: "$" + nom for nom in ELEMENTS}}
    assert lecture["memoire"] == {"cle": CLE_DE_LA_LECTURE}
    entrees = lecture["inputs"]
    for nom in ELEMENTS:
        assert entrees["5.%s_nom" % nom] == "$" + nom
        assert entrees["5.commentaire_%s" % nom.split("_")[1]] == "$commentaire_%s" % nom.split("_")[1]
    assert {entrees[k] for k in ("5.effet", "5.fond", "5.duree_s", "5.tenue_s")} == {
        "$effet", "$fond", "$duration_s", "$tenue_s"}


def test_les_quinze_champs_et_leurs_defauts(affiche):
    expose = affiche["expose"]
    assert expose["image"]["media"] == "image" and expose["image"]["requis"] is True
    for nom in ELEMENTS:
        assert expose[nom]["media"] == "image" and expose[nom]["requis"] is False
        assert expose[nom]["categorie"] == "elements" and "defaut" not in expose[nom]
    defauts = {k: v["defaut"] for k, v in expose.items() if "defaut" in v}
    assert defauts == DEFAUTS
    assert len(expose) == 15
    assert (expose["duration_s"]["min"], expose["duration_s"]["max"]) == (3, 15)
    assert expose["effet"]["options"] == ["emergence", "balayage", "iris"]
    assert expose["fond"]["options"] == ["tons", "sombre", "clair"]
    for nom, champ in expose.items():
        assert champ.get("categorie") and str(champ.get("aide", "")).strip(), nom


def test_l_integrite_se_constate_et_seul_le_fichier_vide_refuse(affiche):
    """« Mentionner une erreur ne doit pas suicider la livraison » : la tenue
    ramenée, l'écart de la dernière image, un réalisateur muet, un élément
    par-dessus l'affiche se CONSTATENT ; le fichier sans images REFUSE."""
    integrite = affiche["etapes"][2]["constater"]
    assert [c["id"] for c in integrite] == CONSTATS
    assert all(str(c.get("aide", "")).strip() for c in integrite)
    intacte = integrite[0]
    assert (intacte["valeur"], intacte["op"], intacte["attendu"]) == (
        "$revelation.recit.ecart_derniere_image_max", "eq", 0)
    assert integrite[2]["attendu"] == "$duration_s"
    assert (integrite[3]["valeur"], integrite[3]["attendu"]) == ("$lecture.recit.repli", False)
    assert integrite[4]["attendu"] == "$lecture.recit.elements_fournis"
    controle = affiche["etapes"][3]["verifier"]
    assert [c["id"] for c in controle] == ["livrable_pese"]


def test_la_copie_versionnee_est_celle_du_poste():
    donnee = DONNEES / "chaines" / "video-affiche.json"
    if not donnee.is_file():
        pytest.skip("pas de _data sur ce poste")
    assert donnee.read_text(encoding="utf-8") == (EXEMPLES / "video-affiche.json").read_text(encoding="utf-8")


def test_sur_ce_poste_le_mode_est_publie_et_ses_graphes_tiennent():
    reconciliation = DONNEES / "reconciliation.local.json"
    if not reconciliation.is_file():
        pytest.skip("pas de _data sur ce poste")
    r = json.loads(reconciliation.read_text(encoding="utf-8"))
    assert r["categories"]["reveler-une-affiche"]["ordre"] == 2
    assert any(c["valeur"] == "elements" for c in r["categories_de_champs"])
    mode = r["workflows"]["video-affiche"]
    assert mode["categorie"] == "reveler-une-affiche" and pathlib.Path(mode["chaine"]).is_file()
    from comfyui_bridge.adapter.chaines import noeud_de_tranches
    for nom, noeud_id, classe, tranches in (("video-affiche-cinematique", "7", "AfficheCinematique", True),
                                             ("affiche-lecture", "5", "AfficheLecture", False)):
        entree = r["workflows"][nom]
        chemin = pathlib.Path(entree["workflow"])
        assert chemin.is_file(), nom
        graphe = json.loads(chemin.read_text(encoding="utf-8"))
        assert (noeud_de_tranches(graphe) == "7") is tranches, nom
        assert graphe[noeud_id]["class_type"] == classe
        for element in ELEMENTS:
            assert element + "_nom" in graphe[noeud_id]["inputs"], (nom, element)
            assert entree["bindings"][element]["input"] == "image"
            assert graphe[entree["bindings"][element]["node"]]["class_type"] == "LoadImage"
        for champ, liaison in entree["bindings"].items():
            assert liaison["input"] in graphe[liaison["node"]]["inputs"], (nom, champ)
    assert "reglages_json" in json.loads(pathlib.Path(r["workflows"]["video-affiche-cinematique"]["workflow"])
                                         .read_text(encoding="utf-8"))["7"]["inputs"]

"""« RÉVÉLER UNE AFFICHE » — le témoin de ce type d'essai (2026-09-20).

Antoine : « je veux un nouveau type de workflow dans maestro. J'envoie une
affiche ou un logo et le pipeline me le révèle en quelques secondes avec un
effet cinématique en prenant en compte les tonalités de couleur présentes. Un
nouveau type, pour test. Le contenu doit toujours rester inchangé. »

Ce qui est épinglé ici : trois étapes (rendre → constater → verifier), les
neuf champs et leurs défauts, l'intégrité CONSTATÉE (jamais refusée — seul le
fichier vide refuse), la copie versionnée identique aux données du poste, et
— sur ce poste — le graphe qui honore les tranches et les liaisons qui visent
des nœuds qui existent. Aucun nom de flux dans le code de la passerelle : tout
ce mode est des DONNÉES.
"""

import json
import pathlib

import pytest

from comfyui_bridge.core import chaine as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1]
EXEMPLES = RACINE / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
DONNEES = RACINE / "_data"

PLAN = [("revelation", "rendre"), ("integrite", "constater"), ("controle", "verifier")]
DEFAUTS = {"duration_s": 5, "tenue_s": 1.5, "effet": "emergence", "fond": "tons",
           "width": 720, "height": 1280, "fps": 30, "seed": 71}
CONSTATS = ["l_affiche_est_intacte", "la_tenue_est_tenue", "la_duree_est_exacte"]
ENTREES_DU_NOEUD = {"7.effet": "$effet", "7.tenue_s": "$tenue_s", "7.fond": "$fond"}


@pytest.fixture(scope="module")
def affiche():
    return json.loads((EXEMPLES / "video-affiche.json").read_text(encoding="utf-8"))


def test_trois_etapes_et_un_seul_livrable(affiche):
    chaine = noyau.lire(affiche, "video-affiche")
    assert [(e.id, e.genre) for e in chaine.etapes] == PLAN
    assert affiche["livrable"] == "$revelation.livrable"
    rendre = affiche["etapes"][0]["rendre"]
    assert rendre["workflow"] == "video-affiche-cinematique"
    assert rendre["media"] == {"image": "$image"}
    assert {k: rendre[k] for k in ("duration_s", "fps", "width", "height", "seed")} == {
        "duration_s": "$duration_s", "fps": "$fps", "width": "$width", "height": "$height",
        "seed": "$seed"}
    assert rendre["inputs"] == ENTREES_DU_NOEUD


def test_les_neuf_champs_et_leurs_defauts(affiche):
    expose = affiche["expose"]
    assert expose["image"]["media"] == "image" and expose["image"]["requis"] is True
    assert {k: v["defaut"] for k, v in expose.items() if k != "image"} == DEFAUTS
    assert (expose["duration_s"]["min"], expose["duration_s"]["max"]) == (3, 15)
    assert expose["effet"]["options"] == ["emergence", "balayage", "iris"]
    assert expose["fond"]["options"] == ["tons", "sombre", "clair"]
    # chaque champ dit sa rubrique et s'explique
    for nom, champ in expose.items():
        assert champ.get("categorie") and str(champ.get("aide", "")).strip(), nom


def test_l_integrite_se_constate_et_seul_le_fichier_vide_refuse(affiche):
    """« Mentionner une erreur ne doit pas suicider la livraison » : la tenue
    ramenée, l'écart de la dernière image, se CONSTATENT ; le fichier sans
    images REFUSE."""
    integrite = affiche["etapes"][1]["constater"]
    assert [c["id"] for c in integrite] == CONSTATS
    assert all(str(c.get("aide", "")).strip() for c in integrite)
    intacte = integrite[0]
    assert (intacte["valeur"], intacte["op"], intacte["attendu"]) == (
        "$revelation.recit.ecart_derniere_image_max", "eq", 0)
    assert integrite[2]["attendu"] == "$duration_s"
    controle = affiche["etapes"][2]["verifier"]
    assert [c["id"] for c in controle] == ["livrable_pese"]


def test_la_copie_versionnee_est_celle_du_poste():
    donnee = DONNEES / "chaines" / "video-affiche.json"
    if not donnee.is_file():
        pytest.skip("pas de _data sur ce poste")
    assert donnee.read_text(encoding="utf-8") == (EXEMPLES / "video-affiche.json").read_text(encoding="utf-8")


def test_sur_ce_poste_le_mode_est_publie_et_son_graphe_honore_les_tranches():
    reconciliation = DONNEES / "reconciliation.local.json"
    if not reconciliation.is_file():
        pytest.skip("pas de _data sur ce poste")
    r = json.loads(reconciliation.read_text(encoding="utf-8"))
    assert r["categories"]["reveler-une-affiche"]["ordre"] == 2
    mode = r["workflows"]["video-affiche"]
    assert mode["categorie"] == "reveler-une-affiche" and pathlib.Path(mode["chaine"]).is_file()
    graphe_entree = r["workflows"]["video-affiche-cinematique"]
    chemin = pathlib.Path(graphe_entree["workflow"])
    assert chemin.is_file()
    graphe = json.loads(chemin.read_text(encoding="utf-8"))
    from comfyui_bridge.adapter.chaines import noeud_de_tranches
    assert noeud_de_tranches(graphe) == "7"
    noeud = graphe["7"]
    assert noeud["class_type"] == "AfficheCinematique"
    for entree, renvoi in ENTREES_DU_NOEUD.items():
        assert entree.split(".", 1)[1] in noeud["inputs"], entree
    # les liaisons visent des nœuds et des entrées qui existent
    for champ, liaison in graphe_entree["bindings"].items():
        assert liaison["input"] in graphe[liaison["node"]]["inputs"], champ
    assert graphe_entree["defaults"] == {"duration_s": 5.0, "fps": 30, "width": 720, "height": 1280}

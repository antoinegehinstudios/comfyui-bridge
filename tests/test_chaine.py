"""Le cœur d'une chaîne : ce qu'il refuse, et ce qu'il résout.

Aucun moteur, aucun ffmpeg, aucun HTTP : c'est tout l'intérêt de ce module.
"""

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core.errors import (InputValueRefusedError, UnknownWorkflowInputError,
                                        WorkflowMappingError)


def _minimale(**remplace):
    base = {
        "version": 1,
        "chaine": "essai",
        "expose": {"duration_s": {"type": "FLOAT", "defaut": 5, "min": 2, "max": 10}},
        "etapes": [{"id": "un", "rendre": {"workflow": "wf", "duration_s": "$duration_s"}}],
        "livrable": "$un.livrable",
    }
    base.update(remplace)
    return base


def test_une_reference_vers_l_aval_est_refusee_a_la_lecture():
    """Découverte à l'exécution, elle faisait échouer la chaîne APRÈS avoir
    dépensé les étapes d'avant."""
    brut = _minimale(etapes=[
        {"id": "un", "recoller": {"parts": ["$deux.livrable"]}},
        {"id": "deux", "rendre": {"workflow": "wf"}},
    ], livrable="$deux.livrable")
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "deux" in refus.value.detail and "APRÈS" in refus.value.detail


def test_une_reference_inconnue_dit_ce_qui_existe():
    brut = _minimale(etapes=[{"id": "un", "rendre": {"workflow": "wf",
                                                     "duration_s": "$duree"}}])
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "$duree" in refus.value.detail
    assert "duration_s" in refus.value.detail        # ce qui existe est nommé


def test_un_parametre_inconnu_d_une_etape_est_refuse():
    brut = _minimale(etapes=[{"id": "un", "extraire_queue": {"video": "x.mp4",
                                                             "images": 3, "trop": 1}}],
                     livrable="$un.fichier")
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(brut)
    assert "trop" in refus.value.detail


def test_une_etape_sans_genre_ou_a_deux_genres_est_refusee():
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "un"}]))
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "un", "rendre": {"workflow": "wf"},
                                      "verifier": [{"valeur": "$duration_s", "op": "gte",
                                                    "attendu": 1}]}]))


def test_une_etape_ne_peut_pas_porter_le_nom_d_un_champ():
    """Sinon « $duration_s » ne saurait plus de quoi il parle."""
    with pytest.raises(WorkflowMappingError):
        noyau.lire(_minimale(etapes=[{"id": "duration_s", "rendre": {"workflow": "wf"}}],
                             livrable="$duration_s.livrable"))


def test_les_defauts_comblent_et_les_bornes_refusent():
    chaine = noyau.lire(_minimale())
    assert noyau.valeurs(chaine, {}) == {"duration_s": 5.0}
    assert noyau.valeurs(chaine, {"duration_s": 7}) == {"duration_s": 7.0}
    with pytest.raises(InputValueRefusedError) as bas:
        noyau.valeurs(chaine, {"duration_s": 1})
    assert "minimum 2" in bas.value.detail
    with pytest.raises(InputValueRefusedError):
        noyau.valeurs(chaine, {"duration_s": 99})


def test_un_champ_requis_absent_est_refuse():
    chaine = noyau.lire(_minimale(expose={
        "image": {"media": "image", "requis": True, "libelle": "L'image"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf", "media": {"image": "$image"}}}]))
    with pytest.raises(InputValueRefusedError) as refus:
        noyau.valeurs(chaine, {})
    assert "image" in refus.value.detail
    assert noyau.valeurs(chaine, {"image": "a.png"}) == {"image": "a.png"}


def test_un_champ_que_la_chaine_n_expose_pas_est_refuse():
    chaine = noyau.lire(_minimale())
    with pytest.raises(UnknownWorkflowInputError) as refus:
        noyau.valeurs(chaine, {"steps": 8})
    assert "steps" in refus.value.detail


def test_une_valeur_hors_menu_est_refusee_et_le_menu_peut_venir_du_dehors():
    chaine = noyau.lire(_minimale(expose={
        "mode": {"type": "COMBO", "defaut": "a", "options": ["a", "b"]}},
        etapes=[{"id": "un", "rendre": {"workflow": "$mode"}}]))
    assert noyau.valeurs(chaine, {"mode": "b"}) == {"mode": "b"}
    with pytest.raises(InputValueRefusedError):
        noyau.valeurs(chaine, {"mode": "c"})
    # La liste peut être remplie par la passerelle (options_depuis) : c'est
    # celle-là qui fait autorité au moment de valider.
    assert noyau.valeurs(chaine, {"mode": "z"}, {"mode": ("z",)}) == {"mode": "z"}


def test_une_liste_peut_venir_du_catalogue_ou_d_un_menu_declare():
    """Une chaîne qui recopie une liste la fige au jour où on l'a écrite. Elle
    dit d'où elle vient — et les deux sources sont lues à la lecture, pas
    découvertes dans un formulaire aux choix vides."""
    for depuis in ({"catalogue": {"prefixe": "video-"}}, {"menu": "style_narratif"},
                   {"prefixe": "video-"}):
        chaine = noyau.lire(_minimale(expose={
            "mode": {"type": "COMBO", "defaut": "a", "options_depuis": depuis}},
            etapes=[{"id": "un", "rendre": {"workflow": "wf"}}]))
        assert chaine.champs["mode"].options_depuis == depuis


def test_une_liste_qui_nomme_deux_sources_ou_un_menu_sans_nom_est_refusee():
    for depuis, dit in (({"catalogue": {}, "menu": "styles"}, "deux sources"),
                        ({"menu": "   "}, "sans le nommer"),
                        ("style_narratif", "doit être un objet")):
        with pytest.raises(WorkflowMappingError) as refus:
            noyau.lire(_minimale(expose={
                "mode": {"type": "COMBO", "options_depuis": depuis}},
                etapes=[{"id": "un", "rendre": {"workflow": "wf"}}]))
        assert dit in refus.value.detail


def test_resoudre_descend_dans_les_listes_et_les_objets():
    valeurs = {"largeur": 704}
    resultats = {"un": {"livrable": "C:/a.mp4", "mesure": {"duration_s": 4.2}}}
    brut = {"parts": ["$un.livrable", {"fichier": "$un.livrable", "depuis_image": 17}],
            "largeur": "$largeur", "litteral": "video.mp4"}
    assert noyau.resoudre(brut, valeurs, resultats) == {
        "parts": ["C:/a.mp4", {"fichier": "C:/a.mp4", "depuis_image": 17}],
        "largeur": 704, "litteral": "video.mp4"}
    # Ce qui n'existe pas encore reste tel quel quand on DÉCRIT sans exécuter…
    assert noyau.resoudre("$deux.livrable", valeurs, {}, strict=False) == "$deux.livrable"
    # …et se dit quand on exécute.
    with pytest.raises(WorkflowMappingError):
        noyau.resoudre("$deux.livrable", valeurs, {})


def test_les_controles_disent_ce_qui_a_ete_mesure():
    controles = [
        {"id": "duree", "valeur": "$un.mesure.duration_s", "op": "between", "attendu": [1, 3]},
        {"id": "poids", "valeur": "$un.mesure.bytes", "op": "gte", "attendu": 1000},
        {"id": "absent", "valeur": "$un.mesure.frames", "op": "exists"},
    ]
    lignes = noyau.controler(controles, {}, {"un": {"mesure": {"duration_s": 4.0,
                                                               "bytes": 2000}}})
    assert [l["ok"] for l in lignes] == [False, True, False]
    # Un contrôle qui ne dit pas la valeur mesurée oblige à refaire le run.
    assert lignes[0]["mesure"] == 4.0 and lignes[0]["attendu"] == [1, 3]


def test_un_attendu_peut_lui_aussi_renvoyer_a_ce_qui_a_ete_demande():
    lignes = noyau.controler(
        [{"id": "tenue", "valeur": "$un.mesure.duration_s", "op": "gte",
          "attendu": "$duration_s"}],
        {"duration_s": 4}, {"un": {"mesure": {"duration_s": 4.0}}})
    assert lignes[0]["ok"] is True and lignes[0]["attendu"] == 4


def test_un_reglage_de_noeud_peut_renvoyer_a_un_champ_expose():
    """Une étape « rendre » règle une entrée de nœud par « inputs » : le renvoi
    qui s'y niche est lu à la lecture et résolu à l'exécution comme un renvoi
    à la racine. C'est le seul canal par lequel un champ exposé atteint une
    entrée de nœud sans qu'aucun code ne nomme ce nœud."""
    chaine = noyau.lire(_minimale(
        expose={"fond": {"type": "COMBO", "defaut": "washi", "options": ["washi", "sepia"]}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf", "inputs": {"61.fond": "$fond"}}}]))
    assert chaine.etapes[0].params["inputs"] == {"61.fond": "$fond"}
    assert noyau.resoudre(chaine.etapes[0].params, {"fond": "sepia"}, {}) == {
        "workflow": "wf", "inputs": {"61.fond": "sepia"}}


def test_un_renvoi_inconnu_niche_dans_inputs_est_refuse_avec_son_nom():
    """Découvert à l'exécution, il aurait fait échouer l'étape après avoir
    dépensé les précédentes ; à la lecture, il est nommé."""
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(etapes=[{"id": "un", "rendre": {
            "workflow": "wf", "inputs": {"61.fond": "$inconnu"}}}]))
    assert "$inconnu" in refus.value.detail


def test_un_texte_facultatif_vaut_sa_chaine_vide_et_un_booleen_son_faux():
    """« "" » et « false » sont des DÉFAUTS, pas des absences : un appel final
    facultatif laissé vide vaut la chaîne vide dans les valeurs, sinon le
    « $cta » de l'étape n'a rien à désigner et l'étape échoue. Un champ sans
    défaut, lui, reste absent."""
    chaine = noyau.lire(_minimale(
        expose={"cta": {"type": "STRING", "defaut": "", "libelle": "Appel final"},
                "signer": {"type": "BOOLEAN", "defaut": False},
                "libre": {"type": "STRING"}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf",
                                        "inputs": {"7.texte": "$cta", "7.signer": "$signer"}}}]))
    assert noyau.valeurs(chaine, {}) == {"cta": "", "signer": False}
    assert noyau.valeurs(chaine, {"cta": "Abonnez-vous", "signer": "oui"}) == {
        "cta": "Abonnez-vous", "signer": True}
    # …et le renvoi se résout sur la chaîne vide, au lieu de lever.
    assert noyau.resoudre(chaine.etapes[0].params, noyau.valeurs(chaine, {}), {})["inputs"] == {
        "7.texte": "", "7.signer": False}

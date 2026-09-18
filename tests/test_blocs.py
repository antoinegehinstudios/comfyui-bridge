"""Les blocs de boucle : ce qu'ils déplient, et ce qu'ils refusent."""

import pytest

from comfyui_bridge.core.blocs import Fragment, deplier, evaluer, tours_de
from comfyui_bridge.core.errors import IntentValidationError


def _segment(marque="segment"):
    return {"fragment": marque, "contenu": {"n": marque}}


# -- pour ---------------------------------------------------------------------

def test_pour_deduit_les_tours_de_la_duree_demandee():
    # Le graphe ne porte aucun nombre de segments : c'est la durée qui décide.
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    assert len(deplier(plan, {"duration_s": 32})) == 4
    assert len(deplier(plan, {"duration_s": 8})) == 1
    assert len(deplier(plan, {"duration_s": 64})) == 8


def test_pour_arrondit_au_dessus_plutot_que_de_livrer_plus_court():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    # 30 s demandées, 8 s par tour : quatre tours (32 s), pas trois (24 s).
    assert len(deplier(plan, {"duration_s": 30})) == 4


def test_pour_retranche_ce_qui_est_deja_couvert():
    # Le premier segment est produit hors boucle ; la boucle ne couvre que le reste.
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "deja": 8.0},
             "faire": [_segment()]}]
    assert len(deplier(plan, {"duration_s": 32})) == 3


def test_pour_ne_deplie_rien_quand_une_seule_passe_suffit():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "deja": 8.0},
             "faire": [_segment()]}]
    assert deplier(plan, {"duration_s": 8}) == []
    assert deplier(plan, {"duration_s": 5}) == []


def test_pour_respecte_son_plafond():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 1.0, "max": 6},
             "faire": [_segment()]}]
    assert len(deplier(plan, {"duration_s": 600})) == 6


def test_chaque_fragment_porte_son_numero_de_tour():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    tours = [f.tour for f in deplier(plan, {"duration_s": 24})]
    assert tours == [0, 1, 2]


def test_un_corps_a_plusieurs_fragments_garde_le_tour_de_la_boucle():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0},
             "faire": [_segment("a"), _segment("b")]}]
    sortie = deplier(plan, {"duration_s": 16})
    assert [(f.nom, f.tour) for f in sortie] == [("a", 0), ("b", 0), ("a", 1), ("b", 1)]


def test_chaque_fragment_sait_combien_de_tours_compte_la_boucle():
    # Le dernier tour doit pouvoir se reconnaître : c'est lui qui livre.
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    sortie = deplier(plan, {"duration_s": 24})
    assert {f.tours_total for f in sortie} == {3}


def test_pour_refuse_un_pas_nul():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 0}, "faire": [_segment()]}]
    with pytest.raises(IntentValidationError):
        deplier(plan, {"duration_s": 10})


def test_pour_dit_quel_parametre_manque():
    plan = [{"pour": {"jusqu_a": "duree_totale", "chaque": 8.0}, "faire": [_segment()]}]
    with pytest.raises(IntentValidationError) as e:
        deplier(plan, {"duration_s": 10})
    assert "duree_totale" in str(e.value)


# -- si / sinon ---------------------------------------------------------------

def test_si_choisit_une_branche():
    plan = [{"si": {"parametre": "fps", "op": "gte", "valeur": 50},
             "alors": [_segment("rapide")], "sinon": [_segment("normal")]}]
    assert [f.nom for f in deplier(plan, {"fps": 60})] == ["rapide"]
    assert [f.nom for f in deplier(plan, {"fps": 25})] == ["normal"]


def test_si_sans_sinon_n_ajoute_rien():
    plan = [{"si": {"parametre": "fps", "op": "gte", "valeur": 50},
             "alors": [_segment("rapide")]}]
    assert deplier(plan, {"fps": 25}) == []


def test_un_parametre_absent_rend_la_condition_fausse():
    # « si une image d'amorce a été jointe » doit pouvoir s'écrire sans que
    # l'absence de pièce jointe fasse échouer le montage entier.
    plan = [{"si": {"parametre": "image", "op": "ne", "valeur": None},
             "alors": [_segment("amorce")], "sinon": [_segment("sans")]}]
    assert [f.nom for f in deplier(plan, {})] == ["sans"]
    assert [f.nom for f in deplier(plan, {"image": "a.png"})] == ["amorce"]


def test_si_refuse_un_operateur_inconnu():
    with pytest.raises(IntentValidationError):
        evaluer({"parametre": "fps", "op": "presque", "valeur": 25}, {"fps": 25})


def test_une_condition_n_est_jamais_du_code():
    # Une recette peut venir d'un gabarit ingéré : y évaluer une expression
    # ouvrirait l'exécution de code à quiconque en pose un.
    with pytest.raises(IntentValidationError):
        evaluer("__import__('os').system('echo')", {})


# -- imbrication et refus -----------------------------------------------------

def test_si_dans_pour():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0},
             "faire": [{"si": {"parametre": "avec_son", "op": "eq", "valeur": True},
                        "alors": [_segment("son")], "sinon": [_segment("muet")]}]}]
    sortie = deplier(plan, {"duration_s": 16, "avec_son": True})
    assert [(f.nom, f.tour) for f in sortie] == [("son", 0), ("son", 1)]


def test_un_bloc_sans_nature_est_refuse():
    with pytest.raises(IntentValidationError):
        deplier([{"quelque_chose": 1}], {})


def test_le_depliage_ne_connait_aucun_terme_du_moteur():
    # Le contenu reste opaque : le noyau le transporte sans le lire.
    contenu = {"class_type": "PeuImporte", "inputs": {"x": ["3", 0]}}
    sortie = deplier([{"fragment": "brut", "contenu": contenu}], {})
    assert sortie == [Fragment("brut", 0, contenu)]


def test_tours_de_est_utilisable_seul():
    assert tours_de({"jusqu_a": 20, "chaque": 8}, {}) == 3


# -- un run par tour ----------------------------------------------------------

from comfyui_bridge.core.blocs import (boucle_par_run, parametres_pilotes,  # noqa: E402
                                       tours_separes)

RELAIS_IMAGE = {"derniere_image": {
    "ecrire": {"class_type": "SaveImage", "inputs": {"images": "$relais.port"}},
    "lire": {"class_type": "LoadImage", "inputs": {"image": "$relais.fichier"}}}}


def _boucle_par_run(deja=0.0):
    return {"jusqu_a": "duration_s", "chaque": 8.0, "deja": deja,
            "un_run_par_tour": True, "relais": RELAIS_IMAGE}


def test_dans_une_boucle_un_si_voit_le_tour():
    # « Au premier tour, l'amorce ; ensuite, le segment » : l'amorce n'a plus à
    # sortir de la boucle pour être posée une seule fois.
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0},
             "faire": [{"si": {"parametre": "tour", "op": "eq", "valeur": 0},
                        "alors": [_segment("amorce")], "sinon": [_segment("segment")]}]}]
    sortie = deplier(plan, {"duration_s": 24})
    assert [(f.nom, f.tour) for f in sortie] == [("amorce", 0), ("segment", 1), ("segment", 2)]


def test_dans_une_boucle_un_si_voit_aussi_le_total_des_tours():
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0},
             "faire": [{"si": {"parametre": "tours_total", "op": "gt", "valeur": 2},
                        "alors": [_segment("long")], "sinon": [_segment("court")]}]}]
    assert {f.nom for f in deplier(plan, {"duration_s": 24})} == {"long"}
    assert {f.nom for f in deplier(plan, {"duration_s": 16})} == {"court"}


def test_le_tour_vu_par_un_si_ne_sort_pas_de_la_boucle():
    # Hors boucle, « tour » n'est pas un paramètre : la condition est fausse.
    plan = [{"si": {"parametre": "tour", "op": "eq", "valeur": 0}, "alors": [_segment("a")]}]
    assert deplier(plan, {"duration_s": 24}) == []


def test_un_fragment_de_boucle_porte_la_declaration_de_sa_boucle():
    pour = _boucle_par_run()
    sortie = deplier([_segment("commun"), {"pour": pour, "faire": [_segment()]}], {"duration_s": 16})
    assert sortie[0].boucle is None                          # hors boucle
    assert all(f.boucle is pour for f in sortie[1:])         # la déclaration, pas une copie


def test_les_pilotes_d_un_run_par_tour_sont_le_tour_et_les_relais():
    plan = [{"pour": _boucle_par_run(), "faire": [_segment()]}]
    assert parametres_pilotes(plan) == {"duration_s", "tour", "relais_derniere_image"}
    # Sans « un_run_par_tour », rien de tout ça : la durée seule pilote.
    plan = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    assert parametres_pilotes(plan) == {"duration_s"}


def test_tours_separes_compte_les_runs_ou_dit_qu_un_seul_suffit():
    plan = [{"pour": _boucle_par_run(), "faire": [_segment()]}]
    assert tours_separes(plan, {"duration_s": 24}) == 3
    assert tours_separes(plan, {"duration_s": 8}) is None          # un tour : un run ordinaire
    sans = [{"pour": {"jusqu_a": "duration_s", "chaque": 8.0}, "faire": [_segment()]}]
    assert tours_separes(sans, {"duration_s": 24}) is None
    assert boucle_par_run(sans) is None


def test_un_run_par_tour_imbrique_ou_double_est_refuse():
    imbrique = [{"pour": _boucle_par_run(),
                 "faire": [{"pour": _boucle_par_run(), "faire": [_segment()]}]}]
    with pytest.raises(IntentValidationError, match="imbriqu"):
        boucle_par_run(imbrique)
    double = [{"pour": _boucle_par_run(), "faire": [_segment("a")]},
              {"pour": _boucle_par_run(), "faire": [_segment("b")]}]
    with pytest.raises(IntentValidationError, match="deux boucles"):
        boucle_par_run(double)

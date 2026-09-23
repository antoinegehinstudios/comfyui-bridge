"""« quand » sous sa forme NOMMÉE : sauter une étape qui n'a rien à faire.

Un renvoi seul ne sait dire qu'une chose : « ce champ est-il vide ? ». Or une
valeur de menu qui signifie « rien à faire » — « aucune » — est un texte comme
un autre : jamais vide, donc toujours vraie. L'étape tournait pour rien, et le
2026-09-22 elle a tué une production : moteur mort, la chaîne est morte à une
étape qui n'avait rien à faire (job 3396be9d, `charte: "aucune"`, échec à
l'étape « contrainte »).

D'où la forme nommée, mot pour mot celle d'un contrôle — « valeur », « op »,
« attendu » — parce qu'une chaîne ne doit parler qu'une langue.
"""

from __future__ import annotations

import pytest

from comfyui_bridge.core import chaine as noyau
from comfyui_bridge.core.errors import WorkflowMappingError


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


def _avec_charte(**remplace):
    """Une chaîne à deux étapes dont la première ne sert que sous une charte."""
    base = _minimale(
        expose={"charte": {"type": "COMBO", "defaut": "aucune"},
                "prompt": {"type": "STRING", "defaut": ""}},
        etapes=[
            {"id": "contrainte",
             "quand": {"valeur": "$charte", "op": "ne", "attendu": "aucune"},
             "rendre": {"workflow": "wf", "inputs": {"1.charte": "$charte",
                                                     "1.prompt": "$prompt"}},
             "sinon": {"livrable": None,
                       "recit": {"tenue": True, "prompt": "$prompt"}}},
            {"id": "rendu",
             "rendre": {"workflow": "wf", "prompt": "$contrainte.recit.prompt"}},
        ],
        livrable="$rendu.livrable")
    base.update(remplace)
    return base


# -- lecture -------------------------------------------------------------------

def test_la_forme_nommee_est_lue_et_garde_son_renvoi():
    chaine = noyau.lire(_avec_charte())
    quand = chaine.etapes[0].quand
    assert quand == {"valeur": "$charte", "op": "ne", "attendu": "aucune"}
    # Le champ interrogé reste identifiable, quelle que soit la forme : c'est
    # lui que « sans effet » doit épargner.
    assert noyau.tete_de_quand(quand) == "charte"
    assert noyau.tete_de_quand("$cta") == "cta"
    assert noyau.tete_de_quand("$un.livrable") == "un"
    assert noyau.tete_de_quand("") is None and noyau.tete_de_quand({"op": "ne"}) is None


def test_le_renvoi_de_la_condition_est_verifie_comme_les_autres():
    """Un « quand » qui désigne l'aval, ou rien de connu, est refusé À LA
    LECTURE — avant d'avoir dépensé une seule étape."""
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(etapes=[
            {"id": "un", "quand": {"valeur": "$deux.livrable", "op": "ne", "attendu": ""},
             "rendre": {"workflow": "wf"}},
            {"id": "deux", "rendre": {"workflow": "wf"}}], livrable="$deux.livrable"))
    assert "APRÈS" in refus.value.detail


@pytest.mark.parametrize("quand, dans_le_refus", [
    ({"valeur": "aucune", "op": "ne", "attendu": "x"}, "quand.valeur"),
    ({"valeur": "$charte", "op": "vaut", "attendu": "x"}, "quand.op"),
    ({"valeur": "$charte", "op": "ne"}, "attendu"),
])
def test_une_condition_mal_ecrite_est_refusee_en_le_disant(quand, dans_le_refus):
    with pytest.raises(WorkflowMappingError) as refus:
        noyau.lire(_minimale(expose={"charte": {"type": "COMBO", "defaut": "aucune"}},
                             etapes=[{"id": "un", "quand": quand,
                                      "rendre": {"workflow": "wf"}}]))
    assert dans_le_refus in refus.value.detail


def test_exists_se_passe_d_attendu():
    chaine = noyau.lire(_minimale(
        expose={"charte": {"type": "COMBO", "defaut": "aucune"}},
        etapes=[{"id": "un", "quand": {"valeur": "$charte", "op": "exists"},
                 "rendre": {"workflow": "wf"}}]))
    assert chaine.etapes[0].quand["op"] == "exists"


# -- exécution : l'étape est sautée, ou non -------------------------------------

def _sauter(chaine, valeurs, rang=0):
    """Ce que l'adaptateur décide pour une étape, sans rien exécuter."""
    from comfyui_bridge.adapter.chaines import RunnerDeChaines
    return RunnerDeChaines._a_sauter(chaine.etapes[rang], valeurs, {}, chaine, None)


def test_l_etape_est_sautee_quand_la_condition_n_est_pas_tenue():
    chaine = noyau.lire(_avec_charte())
    saut = _sauter(chaine, {"charte": "aucune", "prompt": "un chat"})
    assert saut is not None and saut["sautee"] is True
    # La raison dit ce qu'on a LU, pas « vide » : l'étape ne ment pas sur
    # pourquoi elle n'a pas eu lieu.
    assert "'aucune'" in saut["raison"] and "ne" in saut["raison"]


def test_l_etape_est_jouee_quand_la_condition_tient():
    chaine = noyau.lire(_avec_charte())
    assert _sauter(chaine, {"charte": "grabuge-fest", "prompt": "un chat"}) is None


def test_le_sinon_est_RESOLU_pour_rendre_ce_qu_on_lui_avait_donne():
    """Une étape de passe-plat sautée doit rendre à l'aval ce qu'elle aurait
    enrichi. Sans résolution, « $prompt » partait tel quel et le rendu
    recevait la chaîne « $prompt » au lieu de la consigne."""
    chaine = noyau.lire(_avec_charte())
    saut = _sauter(chaine, {"charte": "aucune", "prompt": "un chat au soleil"})
    assert saut["recit"] == {"tenue": True, "prompt": "un chat au soleil"}
    assert saut["livrable"] is None


def test_un_sinon_de_litteraux_n_est_pas_change_par_la_resolution():
    """Les « sinon » écrits avant ne portent que des littéraux : les résoudre
    ne doit rien leur faire."""
    chaine = noyau.lire(_minimale(
        expose={"cta": {"type": "STRING", "defaut": ""}},
        etapes=[{"id": "un", "rendre": {"workflow": "wf"}},
                {"id": "appel", "quand": "$cta",
                 "rendre": {"workflow": "wf", "media": {"video": "$un.livrable"}},
                 "sinon": {"livrable": None, "recit": {"images_reprises": 0}}}],
        livrable="$appel.livrable"))
    saut = _sauter(chaine, {"cta": ""}, rang=1)
    assert saut["livrable"] is None and saut["recit"] == {"images_reprises": 0}


# -- ce que le « sinon » rend n'est pas sans effet ------------------------------

def test_un_champ_que_le_sinon_renvoie_n_est_pas_dit_sans_effet():
    """Le prompt traverse l'étape sautée par son « sinon » : le dire sans
    effet serait faux, et le journal mentirait sur ce qui a servi."""
    chaine = noyau.lire(_avec_charte())
    contrainte = chaine.etapes[0]
    sans_effet = noyau.sans_effet_si_sautee(chaine, contrainte, None,
                                            {"charte": "aucune", "prompt": "un chat"})
    assert sans_effet == []

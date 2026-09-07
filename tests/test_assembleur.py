"""L'assembleur : des fragments dépliés vers un graphe API ComfyUI."""

import pytest

from comfyui_bridge.adapter.assembleur import assembler
from comfyui_bridge.core.blocs import deplier
from comfyui_bridge.core.errors import WorkflowMappingError

COMMUN = {"fragment": "commun", "contenu": {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx.safetensors"}},
}}

AMORCE = {"fragment": "amorce", "contenu": {
    "1": {"class_type": "EmptyLatent", "inputs": {"model": ["$commun.1", 0]}},
}}

SEGMENT = {"fragment": "segment", "contenu": {
    "1": {"class_type": "Sampler", "inputs": {"model": ["$commun.1", 0],
                                              "latent": ["$precedent.1", 0]}},
    "2": {"class_type": "ImageBatch", "inputs": {"a": ["1", 0]}},
}}


def _montage(duree):
    plan = [COMMUN, AMORCE,
            {"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "deja": 8.0},
             "faire": [SEGMENT]}]
    return assembler(deplier(plan, {"duration_s": duree}))


def test_le_graphe_produit_est_un_graphe_api():
    g = _montage(24)
    assert all(isinstance(k, str) and "class_type" in v for k, v in g.items())


def test_deux_tours_ne_s_ecrasent_pas():
    # Les deux tours portent les mêmes numéros dans leur fragment ; sans
    # renumérotation le second effacerait le premier.
    g = _montage(24)
    echantillonneurs = [k for k, v in g.items() if v["class_type"] == "Sampler"]
    assert len(echantillonneurs) == 2


def test_le_nombre_de_noeuds_suit_la_duree():
    assert len(_montage(8)) == 2                      # commun + amorce
    assert len(_montage(16)) == 2 + 2                  # + un segment (2 nœuds)
    assert len(_montage(40)) == 2 + 4 * 2              # + quatre segments


def test_le_commun_n_est_pose_qu_une_fois():
    g = _montage(40)
    assert sum(1 for v in g.values() if v["class_type"] == "CheckpointLoaderSimple") == 1


def test_chaque_segment_se_branche_sur_le_precedent():
    g = _montage(32)          # amorce + trois segments
    par_type = {}
    for k, v in g.items():
        par_type.setdefault(v["class_type"], []).append(k)
    amorce = par_type["EmptyLatent"][0]
    samplers = sorted(par_type["Sampler"], key=int)
    # Le premier segment se branche sur l'amorce…
    assert g[samplers[0]]["inputs"]["latent"][0] == amorce
    # …et chaque suivant sur le nœud « 1 » du segment d'avant, qui est son
    # échantillonneur : "$precedent.1" vise le nœud 1 de l'instance précédente.
    assert g[samplers[1]]["inputs"]["latent"][0] == samplers[0]
    assert g[samplers[2]]["inputs"]["latent"][0] == samplers[1]


def test_un_lien_local_reste_local():
    g = _montage(16)
    batch = next(k for k, v in g.items() if v["class_type"] == "ImageBatch")
    sampler = next(k for k, v in g.items() if v["class_type"] == "Sampler")
    assert g[batch]["inputs"]["a"] == [sampler, 0]


def test_les_valeurs_qui_ne_sont_pas_des_liens_sont_transmises_telles_quelles():
    g = _montage(8)
    charge = next(v for v in g.values() if v["class_type"] == "CheckpointLoaderSimple")
    assert charge["inputs"]["ckpt_name"] == "ltx.safetensors"


def test_precedent_sans_rien_devant_est_refuse():
    plan = [{"fragment": "seul", "contenu": {
        "1": {"class_type": "X", "inputs": {"a": ["$precedent.1", 0]}}}}]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}))
    assert "precedent" in str(e.value)


def test_un_lien_vers_un_noeud_inexistant_se_lit():
    plan = [{"fragment": "a", "contenu": {
        "1": {"class_type": "X", "inputs": {"a": ["7", 0]}}}}]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}))
    assert "$precedent" in str(e.value)          # le message dit comment viser ailleurs


def test_un_fragment_reference_mais_absent_se_lit():
    plan = [{"fragment": "a", "contenu": {
        "1": {"class_type": "X", "inputs": {"a": ["$commun.1", 0]}}}}]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}))
    assert "commun" in str(e.value)


def test_un_noeud_sans_class_type_est_refuse():
    plan = [{"fragment": "a", "contenu": {"1": {"inputs": {}}}}]
    with pytest.raises(WorkflowMappingError):
        assembler(deplier(plan, {}))


def test_un_montage_vide_est_refuse():
    with pytest.raises(WorkflowMappingError):
        assembler([])


def test_le_meme_fragment_pose_deux_fois_hors_boucle_est_refuse():
    plan = [dict(AMORCE), dict(AMORCE)]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}))
    assert "deux fois" in str(e.value)


# -- valeurs calculées au montage ---------------------------------------------

def test_le_numero_de_tour_est_substitue():
    plan = [{"pour": {"jusqu_a": "n", "chaque": 1},
             "faire": [{"fragment": "b", "contenu": {
                 "1": {"class_type": "X", "inputs": {"i": "$tour"}}}}]}]
    g = assembler(deplier(plan, {"n": 3}))
    assert sorted(v["inputs"]["i"] for v in g.values()) == [0, 1, 2]


def test_un_calcul_sur_le_tour_est_evalue():
    # L'instant ou commence le morceau a produire avance a chaque tour : c'est
    # l'expression d'indice d'une boucle, elle se calcule au montage.
    plan = [{"pour": {"jusqu_a": "n", "chaque": 1},
             "faire": [{"fragment": "b", "contenu": {
                 "1": {"class_type": "X",
                       "inputs": {"debut": {"$calc": "(120 + 120 * tour) / 25"}}}}}]}]
    g = assembler(deplier(plan, {"n": 3}))
    assert sorted(v["inputs"]["debut"] for v in g.values()) == [4.8, 9.6, 14.4]


def test_un_calcul_voit_les_constantes():
    plan = [{"fragment": "b", "contenu": {
        "1": {"class_type": "X", "inputs": {"n": {"$calc": "images - recouvrement"}}}}}]
    g = assembler(deplier(plan, {}), {"images": 121, "recouvrement": 17})
    assert next(iter(g.values()))["inputs"]["n"] == 104


def test_un_calcul_n_est_jamais_du_code():
    plan = [{"fragment": "b", "contenu": {
        "1": {"class_type": "X", "inputs": {"n": {"$calc": "__import__('os').system('echo')"}}}}}]
    with pytest.raises(WorkflowMappingError):
        assembler(deplier(plan, {}))


def test_un_calcul_qui_nomme_l_inconnu_se_lit():
    plan = [{"fragment": "b", "contenu": {
        "1": {"class_type": "X", "inputs": {"n": {"$calc": "duree * 2"}}}}}]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}))
    assert "duree" in str(e.value)


def test_une_constante_manquante_se_lit():
    plan = [{"fragment": "b", "contenu": {
        "1": {"class_type": "X", "inputs": {"n": "$const.absente"}}}}]
    with pytest.raises(WorkflowMappingError) as e:
        assembler(deplier(plan, {}), {"presente": 1})
    assert "absente" in str(e.value)

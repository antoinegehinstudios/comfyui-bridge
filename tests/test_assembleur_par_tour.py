"""L'assembleur, fenêtre par fenêtre : un montage à un run par tour.

Chaque run ne reçoit que son tour, ce qu'il cite par son nom, et les relais
qui le raccordent au run d'avant et au run d'après — en disant la même chose
que le montage d'un seul tenant (numéros, rangs, totaux).
"""

import pytest

from comfyui_bridge.adapter.assembleur import assembler
from comfyui_bridge.core.blocs import deplier
from comfyui_bridge.core.errors import WorkflowMappingError
from test_assembleur import AMORCE, COMMUN, SEGMENT

RELAIS_IMAGES = {"images": {
    "ecrire": {"class_type": "SaveImage",
               "inputs": {"images": "$relais.port", "filename_prefix": "$relais.prefixe"}},
    "lire": {"class_type": "LoadImage", "inputs": {"image": "$relais.fichier"}}}}

AMORCE_DANS_LA_BOUCLE = {"fragment": "amorce", "sorties": {"images": "1"}, "contenu": {
    "1": {"class_type": "EmptyLatent", "inputs": {"model": ["$commun.1", 0]}},
}}

SEGMENT_RELAYE = {"fragment": "segment", "sorties": {"images": "1"}, "contenu": {
    "1": {"class_type": "Sampler", "inputs": {"model": ["$commun.1", 0],
                                              "latent": ["$precedent.images", 0]}},
}}

LIVRER = {"fragment": "livrer", "sorties": {"images": "1"}, "contenu": {
    "1": {"class_type": "Livrer", "inputs": {"images": ["$precedent.images", 0],
                                             "rang": {"$calc": "bloc_rang"},
                                             "total": {"$calc": "blocs_total"}}},
}}

AU_PREMIER_TOUR_L_AMORCE = {"si": {"parametre": "tour", "op": "eq", "valeur": 0},
                            "alors": [AMORCE_DANS_LA_BOUCLE], "sinon": [SEGMENT_RELAYE]}


def _plan(relais=RELAIS_IMAGES, corps=None):
    return [COMMUN,
            {"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "un_run_par_tour": True,
                      "relais": relais},
             "faire": corps or [AU_PREMIER_TOUR_L_AMORCE, LIVRER]}]


def _run(tour, duree=24, fichiers=None, plan=None):
    return assembler(deplier(plan or _plan(), {"duration_s": duree}), {},
                     blocs=["livrer"], tour_seul=tour, relais_fichiers=fichiers,
                     prefixe_relais="cortex/essai")


def _types(g):
    return sorted(v["class_type"] for v in g.values())


def _le(g, class_type):
    return next(k for k, v in g.items() if v["class_type"] == class_type)


def test_le_premier_run_garde_le_commun_et_l_amorce_et_ecrit_le_relais():
    g = _run(0)
    assert _types(g) == ["CheckpointLoaderSimple", "EmptyLatent", "Livrer", "SaveImage"]
    assert g[_le(g, "SaveImage")]["inputs"] == {"images": [_le(g, "Livrer"), 0],
                                                "filename_prefix": "cortex/essai_relais_images"}


def test_un_run_du_milieu_relit_le_relais_et_le_reecrit():
    g = _run(1, fichiers={"images": "ancre_00001_.png"})
    assert _types(g) == ["CheckpointLoaderSimple", "Livrer", "LoadImage", "Sampler", "SaveImage"]
    lu = _le(g, "LoadImage")
    assert g[lu]["inputs"] == {"image": "ancre_00001_.png"}
    sampler = g[_le(g, "Sampler")]
    assert sampler["inputs"]["latent"] == [lu, 0]          # « $precedent.images » → le relais relu
    assert sampler["inputs"]["model"] == [_le(g, "CheckpointLoaderSimple"), 0]   # reposé ici


def test_le_dernier_run_n_ecrit_aucun_relais():
    g = _run(2, fichiers={"images": "ancre.png"})
    assert _types(g) == ["CheckpointLoaderSimple", "Livrer", "LoadImage", "Sampler"]


def test_les_numeros_et_les_rangs_sont_ceux_du_montage_entier():
    # Chaque run dit la même chose que le montage d'un seul tenant : mêmes
    # numéros de nœuds, mêmes rangs — les relais prennent les numéros d'après.
    entier = assembler(deplier(_plan(), {"duration_s": 24}), {}, blocs=["livrer"])
    run = _run(1, fichiers={"images": "a.png"})
    for numero, noeud in run.items():
        if noeud["class_type"] in ("LoadImage", "SaveImage"):
            assert int(numero) > max(int(k) for k in entier)
        else:
            assert entier[numero]["class_type"] == noeud["class_type"]
    livrer = run[_le(run, "Livrer")]
    assert (livrer["inputs"]["rang"], livrer["inputs"]["total"]) == (1, 3)


def test_ce_qui_est_avant_la_boucle_sans_etre_cite_ne_va_qu_au_premier_run():
    avant = {"fragment": "avant", "contenu": {"1": {"class_type": "Avant", "inputs": {}}}}
    plan = [COMMUN, avant] + _plan()[1:]
    assert "Avant" in _types(_run(0, plan=plan))
    assert "Avant" not in _types(_run(1, fichiers={"images": "a.png"}, plan=plan))


def test_ce_qui_est_cite_par_son_nom_est_pose_dans_chaque_run_de_proche_en_proche():
    # Le commun cite un fragment d'avant lui : les deux sont reposés au tour 1.
    socle = {"fragment": "socle", "contenu": {"1": {"class_type": "Socle", "inputs": {}}}}
    commun = {"fragment": "commun", "contenu": {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"base": ["$socle.1", 0]}}}}
    plan = [socle, commun] + _plan()[1:]
    assert "Socle" in _types(_run(1, fichiers={"images": "a.png"}, plan=plan))


def test_ce_qui_est_apres_la_boucle_ne_va_qu_au_dernier_run():
    apres = {"fragment": "apres", "contenu": {
        "1": {"class_type": "Apres", "inputs": {"images": ["$precedent.images", 0]}}}}
    plan = _plan() + [apres]
    assert "Apres" not in _types(_run(0, plan=plan))
    g = _run(2, fichiers={"images": "a.png"}, plan=plan)
    assert g[_le(g, "Apres")]["inputs"]["images"] == [_le(g, "Livrer"), 0]


def test_un_port_qui_traverse_sans_relais_se_lit():
    segment = {"fragment": "segment", "contenu": {
        "1": {"class_type": "Sampler", "inputs": {"latent": ["$precedent.latentes", 0]}}}}
    plan = _plan(corps=[{"si": {"parametre": "tour", "op": "eq", "valeur": 0},
                         "alors": [AMORCE_DANS_LA_BOUCLE], "sinon": [segment]}, LIVRER])
    with pytest.raises(WorkflowMappingError, match="aucun relais ne porte 'latentes'"):
        _run(1, fichiers={"images": "a.png"}, plan=plan)


def test_un_relais_sans_fichier_confie_se_lit():
    with pytest.raises(WorkflowMappingError, match="aucun fichier"):
        _run(1)


def test_un_tour_hors_de_la_boucle_se_lit():
    with pytest.raises(WorkflowMappingError, match="tour 5 demandé"):
        _run(5)


def test_un_tour_demande_sans_boucle_a_un_run_par_tour_se_lit():
    plan = [COMMUN, AMORCE, {"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "deja": 8.0},
                             "faire": [SEGMENT]}]
    with pytest.raises(WorkflowMappingError, match="un_run_par_tour"):
        assembler(deplier(plan, {"duration_s": 24}), tour_seul=0)


def test_un_dernier_fragment_qui_n_offre_pas_le_port_se_lit():
    muet = {"fragment": "livrer", "contenu": {
        "1": {"class_type": "Livrer", "inputs": {"images": ["$precedent.images", 0]}}}}
    plan = _plan(corps=[AU_PREMIER_TOUR_L_AMORCE, muet])
    with pytest.raises(WorkflowMappingError, match="n'offre pas ce port"):
        _run(0, plan=plan)


def test_un_relais_sans_noeud_se_lit():
    plan = _plan(relais={"images": {"lire": {"class_type": "LoadImage", "inputs": {}}}})
    with pytest.raises(WorkflowMappingError, match="« ecrire »"):
        _run(0, plan=plan)


def test_une_reference_a_un_autre_tour_ne_traverse_pas_un_run():
    # « $livrer.1 » vise le tour 0 de « livrer » : hors du run du tour 2.
    segment = {"fragment": "segment", "sorties": {"images": "1"}, "contenu": {
        "1": {"class_type": "Sampler", "inputs": {"latent": ["$livrer.1", 0]}}}}
    plan = _plan(corps=[{"si": {"parametre": "tour", "op": "eq", "valeur": 0},
                         "alors": [AMORCE_DANS_LA_BOUCLE], "sinon": [segment]}, LIVRER])
    with pytest.raises(WorkflowMappingError, match="n'est pas dans le run du tour 2"):
        _run(2, fichiers={"images": "a.png"}, plan=plan)


def test_sans_tour_le_montage_a_un_run_par_tour_se_recoud_entier():
    # Le même montage, sans fenêtre : trois tours, aucun relais — c'est ce que
    # « /io » décrit, et ce qu'un moteur qui tiendrait tout recevrait.
    g = assembler(deplier(_plan(), {"duration_s": 24}), {}, blocs=["livrer"])
    assert _types(g) == ["CheckpointLoaderSimple", "EmptyLatent", "Livrer", "Livrer", "Livrer",
                         "Sampler", "Sampler"]

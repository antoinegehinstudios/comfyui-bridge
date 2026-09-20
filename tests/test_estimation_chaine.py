"""L'estimation d'une chaîne part de la chaîne MESURÉE, jamais de la somme de
ses runs.

Mesuré le 2026-09-20 sur « Écrire une vidéo » (5 s, 720 s de livraison) : la
somme des étapes annonçait 27 s, 345 s (la médiane des runs d'UN tour — un
bloc échantillonné ≈ 660 s, sa livraison ≈ 25 s) ou 41 573 s (un ajustement
sur un travail compté autrement à l'estimation qu'aux runs). Les runs d'un
montage par tours ne sont pas d'une seule nature, et le graphe de l'estimation
n'est pas celui d'un tour : la somme ne dit rien du job. La chaîne, elle, est
enregistrée à chaque livraison, à sa configuration — c'est elle qu'on lit.
"""

import math
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from comfyui_bridge.api import main  # noqa: E402


def _runs(_c, _chaine, valeurs):
    """Le compte de runs d'« Écrire une vidéo » : l'amorce (5,17 s) puis 3 s
    par bloc, chaque bloc suivi de sa livraison — 5 s → 2, 6 et 8 s → 4, 15 s → 12."""
    d = float(valeurs["duration_s"])
    return 2 * (1 + max(0, math.ceil((d - 2.167) / 3)))


def _conteneur(mesures):
    return SimpleNamespace(settings=SimpleNamespace(host_id="h"),
                           registry=SimpleNamespace(durees=lambda host, wf, limit=40: list(mesures)))


CHAINE = SimpleNamespace(nom="video-depuis-un-texte")
BASE = {"width": 720, "height": 1280}


def _mesure(duree_demandee, duree_s):
    return {"config": f"720x1280-{duree_demandee:g}s", "duration_s": duree_s, "ts": "t"}


def test_la_meme_configuration_mesuree_donne_sa_mediane(monkeypatch):
    monkeypatch.setattr(main, "_runs_de_la_chaine", _runs)
    c = _conteneur([_mesure(5, 720.0), _mesure(5, 725.0), _mesure(5, 684.0), _mesure(8, 1234.0)])
    e = main._estimation_par_la_chaine(c, CHAINE, {**BASE, "duration_s": 5.0})
    assert e["basis"] == "chaine-meme-config" and e["samples"] == 3
    assert e["seconds"] == 720 and e["min"] == 684 and e["max"] == 725
    assert e["dit"] == "d'après 3 livraisons de ce mode à cette configuration"


def test_une_autre_duree_s_ajuste_sur_le_nombre_de_runs(monkeypatch):
    """15 s n'ont jamais tourné : les livraisons de 5 s (2 runs) et de 6-8 s
    (4 runs) portent une droite, et 15 s font 12 runs."""
    monkeypatch.setattr(main, "_runs_de_la_chaine", _runs)
    c = _conteneur([_mesure(5, 720.0), _mesure(5, 725.0), _mesure(5, 684.0),
                    _mesure(6, 1226.0), _mesure(8, 1234.0), _mesure(8, 1233.0)])
    e = main._estimation_par_la_chaine(c, CHAINE, {**BASE, "duration_s": 15.0})
    assert e["basis"] == "chaine-runs-fit" and e["runs"] == 12 and e["samples"] == 6
    assert 3100 <= e["seconds"] <= 3500 and e["min"] <= e["seconds"] <= e["max"]
    assert e["dit"].startswith("d'après 6 livraisons de ce mode à d'autres durées (12 runs prévus")
    # Une étiquette qui ne se relit pas (une livraison d'avant les empreintes)
    # est laissée de côté, pas inventée.
    c = _conteneur([_mesure(5, 720.0), _mesure(8, 1234.0),
                    {"config": "workflow-default", "duration_s": 3.0, "ts": "t"}])
    assert main._estimation_par_la_chaine(c, CHAINE, {**BASE, "duration_s": 15.0})["samples"] == 2


def test_une_seule_taille_mesuree_rend_sa_mediane_en_le_disant(monkeypatch):
    monkeypatch.setattr(main, "_runs_de_la_chaine", _runs)
    c = _conteneur([_mesure(5, 720.0), _mesure(5, 700.0)])
    e = main._estimation_par_la_chaine(c, CHAINE, {**BASE, "duration_s": 15.0})
    assert e["basis"] == "chaine-autre-config" and e["seconds"] == 710 and e["samples"] == 2
    assert "à une autre configuration" in e["dit"]


def test_rien_de_mesure_laisse_la_somme_des_etapes(monkeypatch):
    monkeypatch.setattr(main, "_runs_de_la_chaine", _runs)
    assert main._estimation_par_la_chaine(_conteneur([]), CHAINE, {**BASE, "duration_s": 5.0}) is None

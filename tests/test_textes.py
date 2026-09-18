"""L'incrustation de textes au recollage : police résolue, filtre lisible, temps depuis la fin."""
from __future__ import annotations

import os

import pytest

from comfyui_bridge.adapter import textes
from comfyui_bridge.core.errors import MediaAssemblyError


def _une_police(tmp_path):
    p = tmp_path / "MaPolice-Bold.ttf"
    p.write_bytes(b"\0")
    return p


def test_un_chemin_de_police_existant_se_prend_tel_quel(tmp_path):
    p = _une_police(tmp_path)
    assert textes.chemin_police(str(p)) == str(p.resolve())


def test_un_nom_se_resout_dans_les_dossiers_du_poste(tmp_path, monkeypatch):
    p = _une_police(tmp_path)
    monkeypatch.setattr(textes, "DOSSIERS_POLICES", (str(tmp_path),))
    assert textes.chemin_police("mapolice bold") == str(p.resolve())
    assert textes.chemin_police("MaPolice-Bold.ttf") == str(p.resolve())


def test_une_police_introuvable_est_refusee_jamais_remplacee(tmp_path, monkeypatch):
    monkeypatch.setattr(textes, "DOSSIERS_POLICES", (str(tmp_path),))
    with pytest.raises(MediaAssemblyError, match="introuvable"):
        textes.chemin_police("Police Qui N'Existe Pas")


def test_le_filtre_porte_police_texte_minutage_et_fondu(tmp_path):
    p = _une_police(tmp_path)
    f = textes.filtre_drawtext({"texte": "Bonjour, monde : 100%", "police": str(p),
                                "debut_s": 1, "fin_s": 5, "position": "bas", "taille": 0.05}, 1080, 1920)
    assert f.startswith("drawtext=fontfile='")
    assert "MaPolice-Bold.ttf" in f
    # les caractères de commande du texte sont protégés
    assert "Bonjour\\, monde \\: 100\\%" in f
    assert "fontsize=96" in f                      # 5 % de 1920
    assert "enable='between(t,1.000,5.000)'" in f
    assert "if(lt(t,1.400)" in f and "if(gt(t,4.600)" in f   # fondu de 0,4 s des deux côtés
    assert "y=h*0.80-th/2" in f and "box=1" in f


def test_un_texte_qui_finit_avant_de_commencer_est_refuse(tmp_path):
    p = _une_police(tmp_path)
    with pytest.raises(MediaAssemblyError, match="finit"):
        textes.filtre_drawtext({"texte": "x", "police": str(p), "debut_s": 5, "fin_s": 2}, 720, 1280)


def test_une_position_inconnue_est_refusee(tmp_path):
    p = _une_police(tmp_path)
    with pytest.raises(MediaAssemblyError, match="position"):
        textes.filtre_drawtext({"texte": "x", "police": str(p), "debut_s": 0, "fin_s": 2, "position": "gauche"}, 720, 1280)


def test_les_textes_vides_sont_comptes_et_ignores_et_les_temps_negatifs_partent_de_la_fin(tmp_path):
    p = _une_police(tmp_path)
    filtres, vides = textes.filtres([
        {"texte": "", "police": str(p), "debut_s": 0.5, "fin_s": 4.5},
        {"texte": "La suite", "police": str(p), "debut_s": -4, "fin_s": -0.2},
    ], 720, 1280, duree_s=20.5)
    assert vides == 1 and len(filtres) == 1
    assert "enable='between(t,16.500,20.300)'" in filtres[0]


def test_une_fin_au_dela_de_la_duree_est_ramenee_a_la_duree(tmp_path):
    p = _une_police(tmp_path)
    filtres, _ = textes.filtres([{"texte": "Titre", "police": str(p), "debut_s": 0.5, "fin_s": 40}], 720, 1280, duree_s=10.0)
    assert "enable='between(t,0.500,10.000)'" in filtres[0]


def test_sans_textes_rien(tmp_path):
    assert textes.filtres(None, 720, 1280) == ([], 0)
    assert textes.filtres([], 720, 1280) == ([], 0)

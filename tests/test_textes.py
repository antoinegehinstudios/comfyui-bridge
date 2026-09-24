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
    f = textes.filtre_drawtext({"texte": "Bonjour, monde : 100% d'aujourd'hui", "police": str(p),
                                "debut_s": 1, "fin_s": 5, "position": "bas", "taille": 0.05}, 1080, 1920,
                               dossier=tmp_path, rang=3)
    assert isinstance(f, list) and len(f) == 1      # sans règle de mesure (police factice), une seule ligne
    f = f[0]
    assert f.startswith("drawtext=fontfile='")
    assert "MaPolice-Bold.ttf" in f
    # le texte part dans un fichier : apostrophes, deux-points, pour cent, tout y passe
    assert "textfile='" in f and "texte_3_0.txt" in f and "text='" not in f
    assert (tmp_path / "texte_3_0.txt").read_bytes() == "Bonjour, monde : 100% d'aujourd'hui".encode("utf-8")
    assert "fontsize=96" in f                      # 5 % de 1920
    assert "enable='between(t,1.000,5.000)'" in f
    assert "if(lt(t,1.400)" in f and "if(gt(t,4.600)" in f   # fondu de 0,4 s des deux côtés
    assert "y=h*0.80-" in f and "box=1" not in f and "borderw=" in f


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
    ], 720, 1280, duree_s=20.5, dossier=tmp_path)
    assert vides == 1 and len(filtres) == 1
    assert "enable='between(t,16.500,20.300)'" in filtres[0]


def test_un_texte_replie_donne_un_filtre_par_ligne_centre_sur_la_hauteur(tmp_path, monkeypatch):
    p = _une_police(tmp_path)

    class Regle:
        def __init__(self, taille): self.taille = taille
        def getlength(self, s): return len(s) * self.taille * 0.5
    monkeypatch.setattr(textes, "_mesure", lambda police, taille: Regle(taille))
    f = textes.filtre_drawtext({"texte": "Aurore. La bouteille qui garde le froid 24 h.", "police": str(p),
                                "debut_s": 0.5, "fin_s": 4.5, "position": "bas", "taille": 0.05}, 1080, 1920,
                               dossier=tmp_path, rang=0)
    assert len(f) == 3
    assert all((tmp_path / f"texte_0_{i}.txt").exists() for i in range(3))
    lignes = [(tmp_path / f"texte_0_{i}.txt").read_text(encoding="utf-8") for i in range(3)]
    assert " ".join(lignes) == "Aurore. La bouteille qui garde le froid 24 h." and all(len(l) * 96 * 0.5 <= 1080 * 0.88 for l in lignes)
    assert "y=h*0.80-180+0" in f[0] and "y=h*0.80-180+120" in f[1] and "y=h*0.80-180+240" in f[2]


def test_une_fin_au_dela_de_la_duree_est_ramenee_a_la_duree(tmp_path):
    p = _une_police(tmp_path)
    filtres, _ = textes.filtres([{"texte": "Titre", "police": str(p), "debut_s": 0.5, "fin_s": 40}], 720, 1280, duree_s=10.0, dossier=tmp_path)
    assert "enable='between(t,0.500,10.000)'" in filtres[0]


def test_le_repli_coupe_aux_mots_et_reduit_la_taille_si_un_mot_ne_tient_pas(monkeypatch):
    class Regle:
        def __init__(self, taille): self.taille = taille
        def getlength(self, s): return len(s) * self.taille * 0.5   # une chasse simple : 0,5 × taille par caractère
    monkeypatch.setattr(textes, "_mesure", lambda police, taille: Regle(taille))
    lignes, taille = textes.replier("Aurore. La bouteille qui garde le froid 24 h.", "x", 100, 1000)
    assert taille == 100 and len(lignes) == 3 and all(len(l) * 50 <= 1000 for l in lignes)
    assert " ".join(lignes) == "Aurore. La bouteille qui garde le froid 24 h."
    # un mot qui ne tient pas à 100 px : la taille baisse jusqu'à ce qu'il tienne
    lignes, taille = textes.replier("Anticonstitutionnellement", "x", 100, 1000)
    assert lignes == ["Anticonstitutionnellement"] and taille < 100 and len("Anticonstitutionnellement") * taille * 0.5 <= 1000
    # sans règle de mesure, le texte part tel quel
    monkeypatch.setattr(textes, "_mesure", lambda police, taille: None)
    assert textes.replier("un texte", "x", 40, 100) == (["un texte"], 40)


def test_le_fond_declare_se_pose_plein_sans_liseré_ni_ombre(tmp_path):
    p = _une_police(tmp_path)
    f = textes.filtre_drawtext({"texte": "GRABUGE FEST", "police": str(p), "debut_s": 0.5, "fin_s": 4,
                                "couleur": "0x0C0C0C", "fond": "0xFFFFFF", "boite": True}, 720, 1280, dossier=tmp_path)[0]
    assert "box=1:boxcolor=0xFFFFFF" in f and "black@0.45" not in f, "le fond de la charte prime sur le bandeau générique"
    assert "borderw=0:" in f and "shadowx=0" in f
    sans = textes.filtre_drawtext({"texte": "GRABUGE FEST", "police": str(p), "debut_s": 0.5, "fin_s": 4,
                                   "couleur": "white", "fond": None}, 720, 1280, dossier=tmp_path)[0]
    assert "box=1" not in sans, "sans fond déclaré, rien ne change"


def test_un_texte_laisse_au_logo_ce_qu_il_ecrit(tmp_path):
    p = _une_police(tmp_path)
    dits = []
    textes_ = [{"texte": "GRABUGE FEST 2027 · TREMPLIN IV", "police": str(p), "debut_s": 0.5, "fin_s": 4, "laisser_au_logo": "Grabuge Fest"},
               {"texte": "Grabuge-Fest", "police": str(p), "debut_s": 1, "fin_s": 4, "laisser_au_logo": "GRABUGE FEST"},
               {"texte": "Billetterie ouverte", "police": str(p), "debut_s": 1, "fin_s": 4, "laisser_au_logo": None}]
    f, vides = textes.filtres(textes_, 720, 1280, 5.0, tmp_path, signaler=dits.append)
    assert (tmp_path / "texte_0_0.txt").read_text(encoding="utf-8").startswith("2027 · TREMPLIN IV"[:4])
    assert vides == 1, "un texte qui n'était que le nom du wordmark n'est pas incrusté"
    assert any("ôté du texte incrusté" in d and "reste « 2027 · TREMPLIN IV »" in d for d in dits), dits
    assert any("il ne restait rien" in d for d in dits), dits
    assert textes.oter_le_nom("Affiche de Grabuge Fest", "grabugefest") == ("Affiche", ["de Grabuge Fest"])
    for adresse in ("Inscriptions sur grabugefest.fr", "www.grabugefest.fr", "contact@grabugefest.fr", "suivez @grabugefest"):
        assert textes.oter_le_nom(adresse, "GRABUGE FEST") == (adresse, []), f"une adresse n'est pas le nom : {adresse}"


def test_sans_textes_rien(tmp_path):
    assert textes.filtres(None, 720, 1280) == ([], 0)
    assert textes.filtres([], 720, 1280) == ([], 0)

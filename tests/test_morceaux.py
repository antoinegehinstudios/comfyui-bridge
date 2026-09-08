"""Le recollage des morceaux : ordre, sélection, et refus de mentir."""

import pytest

from comfyui_bridge.adapter import morceaux


def _poser(dossier, noms):
    for n in noms:
        (dossier / n).write_bytes(b"")
    return [str(dossier / n) for n in noms]


def test_l_ordre_vient_du_rang_ecrit_dans_le_nom(tmp_path):
    # Se fier a l'ordre ou le moteur a fini d'ecrire, ce serait se fier a son
    # ordonnanceur : le rang est dans le NOM.
    chemins = _poser(tmp_path, ["bloc_002_00001.mp4", "bloc_000_00001.mp4",
                                "bloc_001_00001.mp4"])
    ordre = [p.name for p in morceaux.a_recoller(chemins, "cortex/morceaux/bloc")]
    assert ordre == ["bloc_000_00001.mp4", "bloc_001_00001.mp4", "bloc_002_00001.mp4"]


def test_seuls_les_morceaux_du_montage_sont_pris(tmp_path):
    # Un run peut produire d'autres fichiers ; les joindre tous ferait un
    # livrable faux.
    chemins = _poser(tmp_path, ["bloc_000_00001.mp4", "apercu_00001.mp4",
                                "bloc_001_00001.mp4"])
    pris = [p.name for p in morceaux.a_recoller(chemins, "cortex/morceaux/bloc")]
    assert pris == ["bloc_000_00001.mp4", "bloc_001_00001.mp4"]


def test_une_image_n_est_pas_un_morceau(tmp_path):
    chemins = _poser(tmp_path, ["bloc_000_00001.png", "bloc_001_00001.mp4"])
    assert [p.name for p in morceaux.a_recoller(chemins, "bloc")] == ["bloc_001_00001.mp4"]


def test_sans_ffmpeg_le_recollage_le_dit(monkeypatch, tmp_path):
    # Rendre les morceaux tels quels en le disant vaut mieux que de pretendre
    # avoir livre un film.
    monkeypatch.setattr(morceaux, "ffmpeg", lambda: None)
    with pytest.raises(FileNotFoundError):
        morceaux.joindre([tmp_path / "a.mp4"], tmp_path / "tout.mp4")


def test_ffmpeg_est_cherche_dans_le_chemin_puis_sur_le_poste():
    trouve = morceaux.ffmpeg()
    assert trouve is None or str(trouve).lower().endswith(("ffmpeg", "ffmpeg.exe"))

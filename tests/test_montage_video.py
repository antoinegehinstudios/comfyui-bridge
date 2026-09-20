"""Le montage vidéo, sur de VRAIES petites vidéos.

Fabriquées ici même par l'outil d'encodage : mesurer une extraction sur un
fichier inventé ne prouve rien, et une vidéo de deux secondes en 160x120 coûte
quelques dizaines de millisecondes.
"""

import subprocess

import pytest

from comfyui_bridge.adapter import montage_video
from comfyui_bridge.core.errors import MediaAssemblyError

pytestmark = pytest.mark.skipif(
    not montage_video.disponible(),
    reason="ffmpeg/ffprobe absents de ce poste : le montage vidéo ne peut pas être éprouvé")


def _video(chemin, secondes=2, cadence=25, motif="testsrc", taille="160x120"):
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"{motif}=size={taille}:rate={cadence}:duration={secondes}",
                    "-pix_fmt", "yuv420p", str(chemin)], check=True)
    return chemin


def test_mesurer_lit_ce_que_le_fichier_contient(tmp_path):
    mesure = montage_video.mesurer(_video(tmp_path / "a.mp4", secondes=2))
    assert mesure["width"] == 160 and mesure["height"] == 120
    assert 1.9 <= mesure["duration_s"] <= 2.1
    assert mesure["bytes"] > 0


def test_extraire_queue_rend_le_compte_EXACT(tmp_path):
    """Comptée d'après les métadonnées, la queue en livrait une de moins que
    demandé : le compte est revérifié par décodage, sur le clip produit."""
    source = _video(tmp_path / "a.mp4", secondes=2)            # 50 images
    fait = montage_video.extraire_queue(source, 17, tmp_path / "q.mp4")
    assert fait["images"] == 17
    assert montage_video.compter_images(fait["fichier"]) == 17


def test_une_queue_plus_longue_que_la_video_se_dit(tmp_path):
    source = _video(tmp_path / "a.mp4", secondes=1)            # 25 images
    with pytest.raises(MediaAssemblyError) as refus:
        montage_video.extraire_queue(source, 999, tmp_path / "q.mp4")
    assert "999" in refus.value.detail


def test_extraire_image_prend_la_derniere_pas_une_voisine(tmp_path):
    source = _video(tmp_path / "a.mp4", secondes=1)
    fait = montage_video.extraire_image(source, "last", tmp_path / "fin.png")
    assert fait["index"] == montage_video.compter_images(source) - 1
    premiere = montage_video.extraire_image(source, "first", tmp_path / "debut.png")
    assert premiere["index"] == 0


def test_recoller_uniformise_et_mesure(tmp_path):
    a = _video(tmp_path / "a.mp4", secondes=1, taille="160x120")
    b = _video(tmp_path / "b.mp4", secondes=1, taille="128x96", motif="smptebars")
    fait = montage_video.recoller([str(a), str(b)], tmp_path / "joint.mp4",
                                  fps=25, largeur=160, hauteur=120)
    assert fait["parts"] == 2
    assert fait["mesure"]["width"] == 160 and fait["mesure"]["height"] == 120
    # Les deux secondes sont là : un recollage qui perd une part se voit ici.
    assert 1.8 <= fait["mesure"]["duration_s"] <= 2.3


def test_recoller_rogne_la_tete_declaree(tmp_path):
    """Le chevauchement re-rendu par l'amont est à JETER : sans ce rognage, le
    montage rejoue les mêmes images deux fois."""
    a = _video(tmp_path / "a.mp4", secondes=1)
    b = _video(tmp_path / "b.mp4", secondes=1)
    entier = montage_video.recoller([str(a), str(b)], tmp_path / "entier.mp4",
                                    fps=25, largeur=160, hauteur=120)
    rogne = montage_video.recoller(
        [str(a), {"fichier": str(b), "depuis_image": 13}], tmp_path / "rogne.mp4",
        fps=25, largeur=160, hauteur=120)
    assert rogne["mesure"]["duration_s"] < entier["mesure"]["duration_s"]


def test_mesurer_raccords_compare_les_images_GARDEES(tmp_path):
    a = _video(tmp_path / "a.mp4", secondes=1)
    meme = _video(tmp_path / "b.mp4", secondes=1)
    autre = _video(tmp_path / "c.mp4", secondes=1, motif="smptebars")
    # Deux clips issus du même motif se ressemblent plus que deux motifs
    # différents : c'est exactement ce que le raccord doit dire.
    proche = montage_video.mesurer_raccords([str(a), str(meme)], tmp_path / "t1")
    loin = montage_video.mesurer_raccords([str(a), str(autre)], tmp_path / "t2")
    assert proche["nombre"] == 1 and loin["nombre"] == 1
    assert proche["pire"] > loin["pire"]
    assert proche["pire"] == proche["moyenne"] == proche["meilleure"]


def _extrait(chemin, source, depuis, nombre, cadence=25, taille="160x120"):
    """Les images [depuis, depuis + nombre) du motif testsrc, en un clip."""
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc=size={taille}:rate={cadence}:duration=10",
                    "-vf", f"select='between(n,{depuis},{depuis + nombre - 1})',setpts=N/{cadence}/TB",
                    "-r", str(cadence), "-pix_fmt", "yuv420p", str(chemin)], check=True)
    return chemin


def test_chercher_raccord_trouve_l_image_qui_rejoint_la_precedente(tmp_path):
    """Un tour qui REJOUE la fin du précédent (2026-09-20, texte → vidéo : deux
    secondes au ralenti avant de continuer) : parmi ses premières images, celle
    qui ressemble le plus à la dernière image livrée est l'image de raccord ;
    ce qui la précède, elle comprise, est du déjà-vu."""
    avant = _extrait(tmp_path / "a.mp4", "testsrc", 0, 25)           # images 0..24
    apres = _extrait(tmp_path / "b.mp4", "testsrc", 15, 35)          # 15..49 : rejoue 15..24, puis 25..49
    r = montage_video.chercher_raccord(avant, apres, tmp_path / "r", fenetre=30)
    assert r["image"] == 9 and r["tenu"] is True and r["ssim"] > 0.95
    assert r["premiere"] < r["ssim"] and len(r["scores"]) == 30
    # une suite qui ne rejoue rien : le raccord est sa première image (rien à jeter)
    suite = _extrait(tmp_path / "c.mp4", "testsrc", 25, 25)
    r2 = montage_video.chercher_raccord(avant, suite, tmp_path / "r2", fenetre=30)
    assert r2["image"] in (0, 1) and r2["scores"][0] >= 0.9
    # un tour d'un autre motif ne rejoint rien : dit, pas coupé
    autre = _video(tmp_path / "d.mp4", secondes=1, motif="smptebars")
    r3 = montage_video.chercher_raccord(avant, autre, tmp_path / "r3", fenetre=30)
    assert r3["tenu"] is False


def test_un_fichier_absent_se_dit_plutot_que_de_planter(tmp_path):
    with pytest.raises(MediaAssemblyError) as refus:
        montage_video.recoller([str(tmp_path / "fantome.mp4")], tmp_path / "x.mp4")
    assert "fantome.mp4" in refus.value.detail


def test_la_ligne_decisive_est_celle_qui_dit_pourquoi():
    stderr = ("ffmpeg version 7.0\n  built with gcc\n"
              "[in#0] Error opening input: No such file or directory\n"
              "Conversion failed!\n")
    assert "No such file or directory" in montage_video.ligne_decisive(stderr)

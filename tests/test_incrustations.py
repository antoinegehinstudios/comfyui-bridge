"""Incruster des images au recollage : un logo posé TEL QUEL, une texture fondue — mesuré sur de vraies vidéos.

Le logo se retrouve pixel pour pixel (à l'encodage près) à la place que son
placement annonce, après le recollage ; une taille minimale l'agrandit et le
dit ; une texture fondue se lit dans les images ; un objet sans fichier ne pose
rien et le dit ; un ancrage, une fusion ou un fichier inconnus sont refusés.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from comfyui_bridge.adapter import incrustations, montage_video
from comfyui_bridge.core.errors import MediaAssemblyError


def _video(chemin: Path, secondes: float = 1.0, cadence: int = 25, taille: str = "320x240", couleur: str = "0x203040") -> Path:
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c={couleur}:size={taille}:rate={cadence}:duration={secondes}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(chemin)], check=True)
    return chemin


def _logo(chemin: Path, taille=(120, 40)) -> Path:
    """Un logo d'épreuve : un aplat orange franc et un bord transparent."""
    im = Image.new("RGBA", taille, (0, 0, 0, 0))
    for x in range(8, taille[0] - 8):
        for y in range(6, taille[1] - 6):
            im.putpixel((x, y), (240, 120, 10, 255))
    im.save(chemin)
    return chemin


def _image(video: Path, dossier: Path, t: float = 0.5) -> Image.Image:
    sortie = dossier / f"trame-{t}.png"
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(sortie)], check=True)
    return Image.open(sortie).convert("RGB")


def test_la_boite_suit_le_placement_et_la_taille_minimale():
    b = incrustations.boite("haut-centre", 0.5, 0.05, 320, 240, 3.0)
    assert (b["largeur"], b["hauteur"], b["y"]) == (160, 53, 12) and b["x"] == 80 and not b["agrandi_au_minimum"]
    petit = incrustations.boite("bas-droite", 0.05, 0.05, 320, 240, 3.0, hauteur_min_px=24)
    assert petit["hauteur"] == 24 and petit["agrandi_au_minimum"], "la taille minimale de la charte l'emporte, et c'est dit"
    assert petit["x"] + petit["largeur"] <= 320 and petit["y"] + petit["hauteur"] <= 240
    with pytest.raises(MediaAssemblyError):
        incrustations.boite("en-haut", 0.5, 0.05, 320, 240, 3.0)
    # une marge plus serrée que la zone de protection : l'image s'écarte du bord, sans rétrécir, et c'est dit
    serre = incrustations.boite("haut-centre", 0.5, 0.01, 320, 240, 3.0, espace_min=0.25)
    assert serre["deplace_pour_la_zone"] and serre["y"] == round(0.25 * serre["hauteur"]) and serre["largeur"] == 160
    assert serre["zone_tenue_au_bord"] is True
    trop = incrustations.boite("haut-centre", 1.0, 0.0, 320, 240, 3.0, espace_min=0.25)
    assert trop["zone_tenue_au_bord"] is False, "une image qui remplit la largeur ne peut pas tenir sa zone : c'est dit"


def test_le_logo_se_pose_tel_quel_a_sa_place(tmp_path):
    parts = [_video(tmp_path / "a.mp4"), _video(tmp_path / "b.mp4")]
    logo = _logo(tmp_path / "logo.png")
    dits: list[str] = []
    fait = montage_video.recoller(parts, tmp_path / "sortie.mp4", fps=25, largeur=320, hauteur=240, signaler=dits.append,
                                  images=[{"fichier": str(logo), "ancrage": "haut-centre", "largeur": 0.5, "marge": 0.05}])
    b = incrustations.boite("haut-centre", 0.5, 0.05, 320, 240, 3.0)
    assert fait["images_posees"][0]["x"] == b["x"] and fait["mesure"]["width"] == 320
    trame = _image(Path(fait["livrable"]), tmp_path)
    centre = trame.getpixel((b["x"] + b["largeur"] // 2, b["y"] + b["hauteur"] // 2))
    ailleurs = trame.getpixel((10, 230))
    assert abs(centre[0] - 240) < 20 and abs(centre[1] - 120) < 20 and abs(centre[2] - 10) < 25, centre
    assert abs(ailleurs[0] - 0x20) < 12 and abs(ailleurs[2] - 0x40) < 12, "hors du logo, la vidéo est intacte"
    assert any("posée telle quelle" in d for d in dits)


def test_la_texture_se_fond_et_un_logo_sans_fichier_se_dit(tmp_path):
    parts = [_video(tmp_path / "a.mp4", couleur="0x808080")]
    grain = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            grain.putpixel((x, y), (255, 255, 255) if (x // 8 + y // 8) % 2 else (0, 0, 0))
    grain.save(tmp_path / "grain.png")
    dits: list[str] = []
    fait = montage_video.recoller(parts, tmp_path / "sortie.mp4", fps=25, largeur=320, hauteur=240, signaler=dits.append,
                                  texture={"fichier": str(tmp_path / "grain.png"), "fusion": "normal", "opacite": 0.5},
                                  images=[{"fichier": None, "ancrage": "bas-droite"}])
    assert fait["texture_posee"] is True and fait["images_posees"] == []
    trame = _image(Path(fait["livrable"]), tmp_path)
    valeurs = [trame.getpixel((x, 100))[0] for x in range(0, 320, 4)]  # une ligne au cœur des cases du damier, pas sur leur bord
    assert max(valeurs) - min(valeurs) > 60, "le damier de la texture se lit à travers le gris uni"
    assert any("fondue" in d for d in dits) and any("aucun fichier" in d for d in dits)


def test_une_fusion_ou_un_fichier_inconnus_sont_refuses(tmp_path):
    parts = [_video(tmp_path / "a.mp4")]
    with pytest.raises(MediaAssemblyError, match="fusion"):
        montage_video.recoller(parts, tmp_path / "s.mp4", largeur=320, hauteur=240,
                               texture={"fichier": str(_logo(tmp_path / "l.png")), "fusion": "dissolution"})
    with pytest.raises(MediaAssemblyError, match="introuvable"):
        montage_video.recoller(parts, tmp_path / "s2.mp4", largeur=320, hauteur=240,
                               images=[{"fichier": str(tmp_path / "absent.png"), "ancrage": "centre"}])


def test_la_chaine_admet_images_et_texture_au_recollage():
    from comfyui_bridge.core import chaine as noyau

    cles, _ = noyau._CLES["recoller"]
    assert {"images", "texture"} <= set(cles)

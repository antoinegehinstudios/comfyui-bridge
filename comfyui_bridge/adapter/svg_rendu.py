"""Peindre un dessin vectoriel, pour que le moteur puisse s'en servir.

Un SVG n'est pas une image : c'est un DOCUMENT qui décrit comment en peindre
une, à la taille qu'on veut. Le moteur, lui, ne sait ouvrir que des images
matricielles — et le 2026-09-22 un SVG déposé comme image de référence l'a fait
tomber vingt et une fois (le nœud qui charge une image se rabattait sur le
chemin vidéo, et le démultiplexeur mourait d'une violation d'accès).

D'où ce module : le vectoriel entre, et ce qui part chez le moteur est une
image PEINTE à une taille choisie, fond transparent. Le document original reste
à côté — c'est lui la source, et un nœud peut le repeindre plus grand.

Le peintre est **Inkscape**, déjà installé sur ce poste (1.4.4) : c'est le
rendu de référence du format (CSS, polices, dégradés, masques), là où une
bibliothèque Python n'en couvre qu'une part. Son absence n'est pas une panne
silencieuse : elle se dit, et le dépôt refuse en nommant ce qui manque.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Où chercher le peintre, dans l'ordre : ce que le poste déclare, les endroits
# où il s'installe, puis le PATH. Aucun de ces chemins n'est une obligation —
# le premier qui répond gagne, et si aucun ne répond on le DIT.
VARIABLE = "INKSCAPE_BIN"
CHEMINS = (
    r"E:/Programmes/Inkscape/bin/inkscape.exe",
    r"C:/Program Files/Inkscape/bin/inkscape.exe",
    r"C:/Program Files (x86)/Inkscape/bin/inkscape.exe",
    "/usr/bin/inkscape",
)

# Le côté long par défaut d'une image peinte depuis un vectoriel. 2048 tient
# une pleine page en 4K sans peser : un logo posé sur une vidéo 1080p y est
# encore net, et le document reste là pour repeindre plus grand.
COTE_LONG_DEFAUT = 2048
COTE_LONG_MAX = 8192

# Au-delà, ce n'est plus un dessin : un SVG porte du texte, pas des pixels.
OCTETS_MAX = 20 * 1024 * 1024

# Peindre un dessin simple prend deux secondes (mesuré) ; un dessin lourd peut
# en prendre plus, mais pas une minute.
DELAI_S = 90.0


class SansPeintre(RuntimeError):
    """Aucun peintre de vectoriel sur ce poste — dit, jamais deviné."""


def peintre() -> str | None:
    """Le chemin du peintre, ou None s'il n'y en a pas sur ce poste."""
    declare = os.environ.get(VARIABLE)
    if declare and Path(declare).is_file():
        return declare
    for chemin in CHEMINS:
        if Path(chemin).is_file():
            return chemin
    return shutil.which("inkscape")


def est_svg(octets: bytes) -> bool:
    """Ces octets sont-ils un dessin vectoriel ? Lu à la source, pas au nom :
    un `.png` peut être un SVG (mesuré), et c'est le contenu qui décide."""
    tete = octets[:4096].lstrip()
    if tete[:5].lower() == b"<?xml":
        tete = tete[5:]
    return b"<svg" in tete[:2048].lower()


def balise_racine(octets: bytes):
    """La balise « <svg …> » D'OUVERTURE, et elle seule.

    Chercher `width` dans tout le document lisait celui du premier `<rect>` et
    donnait au dessin la taille d'un de ses traits (mesuré) : ce qui décide de
    la taille d'un document, c'est sa racine.
    """
    tete = octets[:8192].decode("utf-8", "replace")
    debut = tete.lower().find("<svg")
    if debut < 0:
        return None
    fin = tete.find(">", debut)
    return tete[debut:fin + 1] if fin > debut else None


def taille_declaree(octets: bytes) -> tuple[float, float] | None:
    """Ce que le document DÉCLARE à sa racine (viewBox, ou width/height), sans
    rien peindre — assez pour garder son rapport quand on choisit un côté."""
    racine = balise_racine(octets)
    if racine is None:
        return None
    boite = re.search(r'viewBox\s*=\s*["\']\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([-\d.eE]+)',
                      racine)
    if boite:
        try:
            largeur, hauteur = float(boite.group(3)), float(boite.group(4))
            if largeur > 0 and hauteur > 0:
                return largeur, hauteur
        except ValueError:
            pass
    cotes = []
    for nom in ("width", "height"):
        m = re.search(rf'\s{nom}\s*=\s*["\']\s*([\d.]+)', racine)
        cotes.append(float(m.group(1)) if m else 0.0)
    if cotes[0] > 0 and cotes[1] > 0:
        return cotes[0], cotes[1]
    return None


def dimensions_voulues(octets: bytes, largeur: int | None = None, hauteur: int | None = None,
                       cote_long: int = COTE_LONG_DEFAUT) -> tuple[int, int]:
    """La taille à peindre : ce qui est demandé, ou le côté long imposé au
    rapport du document (1:1 quand il ne déclare rien)."""
    cote_long = max(16, min(int(cote_long or COTE_LONG_DEFAUT), COTE_LONG_MAX))
    if largeur and hauteur:
        return max(1, int(largeur)), max(1, int(hauteur))
    declaree = taille_declaree(octets) or (1.0, 1.0)
    rapport = declaree[0] / declaree[1] if declaree[1] else 1.0
    if largeur:
        return max(1, int(largeur)), max(1, round(int(largeur) / rapport))
    if hauteur:
        return max(1, round(int(hauteur) * rapport)), max(1, int(hauteur))
    if rapport >= 1:
        return cote_long, max(1, round(cote_long / rapport))
    return max(1, round(cote_long * rapport)), cote_long


def peindre(octets: bytes, largeur: int | None = None, hauteur: int | None = None,
            cote_long: int = COTE_LONG_DEFAUT) -> tuple[bytes, dict]:
    """Peindre le dessin en PNG (fond transparent) et dire ce qui a été fait.

    Rend ``(octets_png, {"largeur", "hauteur", "peintre", "cote_long"})``.
    Lève ``SansPeintre`` si le poste n'a pas de peintre, ``RuntimeError`` si le
    dessin ne se peint pas (document cassé) — dans les deux cas, l'appelant a
    de quoi le dire à l'utilisateur.
    """
    if len(octets) > OCTETS_MAX:
        raise RuntimeError(f"dessin de {len(octets) // 1024} Kio : au-delà de "
                           f"{OCTETS_MAX // (1024 * 1024)} Mio, ce n'est plus un dessin")
    outil = peintre()
    if outil is None:
        raise SansPeintre(
            "aucun peintre de vectoriel sur ce poste (Inkscape) : un SVG ne peut pas être "
            f"transformé en image ici — l'installer, ou déclarer {VARIABLE}")
    voulue = dimensions_voulues(octets, largeur, hauteur, cote_long)
    with tempfile.TemporaryDirectory(prefix="svg_") as dossier:
        entree = Path(dossier) / "dessin.svg"
        sortie = Path(dossier) / "peint.png"
        entree.write_bytes(octets)
        # `--export-background-opacity=0` : le fond reste TRANSPARENT. Un logo
        # posé sur une vidéo doit l'être ; un fond blanc se verrait.
        commande = [outil, "--export-type=png", f"--export-filename={sortie}",
                    f"--export-width={voulue[0]}", f"--export-height={voulue[1]}",
                    "--export-background-opacity=0", str(entree)]
        fait = subprocess.run(commande, capture_output=True, timeout=DELAI_S, check=False)
        if not sortie.is_file() or sortie.stat().st_size == 0:
            details = (fait.stderr or fait.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError("le dessin ne s'est pas peint"
                               + (f" : {details[:200]}" if details else ""))
        return sortie.read_bytes(), {"largeur": voulue[0], "hauteur": voulue[1],
                                     "peintre": Path(outil).name, "cote_long": cote_long}

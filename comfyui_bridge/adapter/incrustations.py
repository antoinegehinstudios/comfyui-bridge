"""Incruster des IMAGES dans une vidéo au recollage : un logo TEL QUEL, une texture fondue.

Même passage que les textes (`textes.py`) et pour la même raison : c'est au
recollage, APRÈS le dernier agrandissement, que la taille livrée est atteinte.
Un logo posé plus tôt serait rééchantillonné avec la vidéo, adouci par
l'agrandissement, peut-être retouché par un modèle ; posé ici, il n'est
redimensionné qu'UNE fois (Lanczos, depuis son fichier) et n'est plus touché
que par l'encodage final. Une texture se fond de même, en flux, image par image.

Une image est un objet :

    {"fichier": "C:/.../logo.png", "ancrage": "haut-centre", "largeur": 0.72,
     "marge": 0.03, "hauteur_min_px": 24, "debut_s": null, "fin_s": null}

* ``ancrage`` : haut-gauche, haut-centre, haut-droite, centre, bas-gauche,
  bas-centre, bas-droite ;
* ``largeur`` : en part de la LARGEUR du cadre ; ``marge`` : l'écart au bord, en
  part du plus petit côté (la même grammaire que le placement d'Héraldiste) ;
* ``hauteur_min_px`` : la taille minimale de la charte — un logo que le
  placement ferait plus petit est AGRANDI jusqu'à elle, et c'est dit ;
* ``debut_s`` / ``fin_s`` : facultatifs (toute la durée par défaut), négatifs
  comptés depuis la fin comme pour les textes.

Une texture :

    {"fichier": "C:/.../grain.png", "fusion": "lumiere-douce", "opacite": 0.35,
     "taille_relative": null}

* ``fusion`` : normal, multiplier, superposition, lumiere-douce, ecran ;
* ``opacite`` : de 0 (rien) à 1 ; ``taille_relative`` : la largeur d'un
  carreau en part du cadre, absente la texture couvre le cadre (recadrée,
  jamais déformée).

Un objet sans fichier (une charte sans logo, une étape sautée) ne pose rien, et
le recollage le DIT ; un fichier illisible, un ancrage ou une fusion inconnus
sont refusés — jamais remplacés en silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import MediaAssemblyError

ANCRAGES = ("haut-gauche", "haut-centre", "haut-droite", "centre", "bas-gauche", "bas-centre", "bas-droite")
# LES MODES DE FUSION, écrits ici en formules, la vidéo pour BASE (A) et la texture pour CALQUE (B), de 0 à 255.
# Pas les noms du filtre `blend` d'ffmpeg : son « normal » rend la couche du dessus — la vidéo, la texture
# disparaissait (mesuré le 2026-09-24) — et ses modes ne prennent pas tous la même couche pour base.
# multiplier, écran, superposition : W3C Compositing and Blending Level 1 ; lumière douce : la variante
# « pegtop » ((1−2b)a² + 2ab), sans rupture de pente. L'opacité mêle le résultat à la base.
FUSIONS = {
    "normal": "B",
    "multiplier": "A*B/255",
    "ecran": "255-(255-A)*(255-B)/255",
    "superposition": "if(lt(A,128),2*A*B/255,255-2*(255-A)*(255-B)/255)",
    "lumiere-douce": "((255-2*B)*A*A/255+2*B*A)/255",
}


def boite(ancrage: str, largeur_relative: float | None, marge: float | None, largeur: int, hauteur: int,
          rapport: float, hauteur_min_px: float | None = None) -> dict[str, Any]:
    """La boîte, en pixels du cadre, où poser une image de ce rapport largeur/hauteur."""
    if ancrage not in ANCRAGES:
        raise MediaAssemblyError(f"incrustation : ancrage « {ancrage} » inconnu ({', '.join(ANCRAGES)})")
    lw = max(1.0, float(largeur_relative if largeur_relative is not None else 0.25) * largeur)
    lh = lw / max(1e-6, rapport)
    agrandi = False
    if hauteur_min_px and lh < float(hauteur_min_px):
        lh = float(hauteur_min_px)
        lw = lh * rapport
        agrandi = True
    if lw > largeur:
        lw, lh = float(largeur), largeur / rapport
    if lh > hauteur:
        lh, lw = float(hauteur), hauteur * rapport
    m = float(marge if marge is not None else 0.05) * min(largeur, hauteur)
    if ancrage == "centre":
        x, y = (largeur - lw) / 2, (hauteur - lh) / 2
    else:
        ligne, colonne = ancrage.split("-")
        y = m if ligne == "haut" else hauteur - lh - m
        x = m if colonne == "gauche" else (largeur - lw - m if colonne == "droite" else (largeur - lw) / 2)
    x = min(max(0.0, x), largeur - lw)
    y = min(max(0.0, y), hauteur - lh)
    return {"x": int(round(x)), "y": int(round(y)), "largeur": int(round(lw)), "hauteur": int(round(lh)),
            "agrandi_au_minimum": agrandi}


def _ouvrir(fichier: Any, quoi: str):
    from PIL import Image

    chemin = Path(str(fichier or ""))
    if not chemin.is_file():
        raise MediaAssemblyError(f"incrustation : {quoi} introuvable ({fichier})")
    try:
        image = Image.open(chemin)
        image.load()
    except Exception as exc:  # repli: aucun — un fichier illisible REFUSE l'incrustation, et dit pourquoi
        raise MediaAssemblyError(f"incrustation : {quoi} illisible ({chemin.name} : {exc})") from exc
    return image


def couche_de_texture(fichier: Any, largeur: int, hauteur: int, taille_relative: float | None = None):
    """La texture telle qu'elle se pose sur ce cadre : en carreaux de la taille dite, sinon couvrant le cadre."""
    from PIL import Image

    tex = _ouvrir(fichier, "texture").convert("RGB")
    if taille_relative:
        lw = max(1, round(float(taille_relative) * largeur))
        carreau = tex.resize((lw, max(1, round(lw * tex.height / tex.width))), Image.LANCZOS)
        couche = Image.new("RGB", (largeur, hauteur))
        for y in range(0, hauteur, carreau.height):
            for x in range(0, largeur, carreau.width):
                couche.paste(carreau, (x, y))
        return couche
    k = max(largeur / tex.width, hauteur / tex.height)
    grande = tex.resize((max(largeur, round(tex.width * k)), max(hauteur, round(tex.height * k))), Image.LANCZOS)
    x0, y0 = (grande.width - largeur) // 2, (grande.height - hauteur) // 2
    return grande.crop((x0, y0, x0 + largeur, y0 + hauteur))


def filtre_texture(texture: Any, largeur: int, hauteur: int, fps: int, rang: int, dossier: str | Path,
                   entree: str) -> tuple[list[str], list[str], str, list[str]]:
    """La texture fondue sur la vidéo : `(arguments d'entrée, chaînes de filtres, étiquette de sortie, ce qui est dit)`."""
    if not texture or not isinstance(texture, dict) or not texture.get("fichier"):
        return [], [], entree, (["aucune texture à poser"] if texture else [])
    fusion = str(texture.get("fusion") or "normal")
    if fusion not in FUSIONS:
        raise MediaAssemblyError(f"incrustation : fusion « {fusion} » inconnue ({', '.join(FUSIONS)})")
    opacite = float(texture.get("opacite") if texture.get("opacite") is not None else 1.0)
    if not 0.0 <= opacite <= 1.0:
        raise MediaAssemblyError(f"incrustation : opacité {opacite} hors de 0 à 1")
    chemin = Path(dossier) / "texture.png"
    couche_de_texture(texture["fichier"], largeur, hauteur, texture.get("taille_relative")).save(chemin)
    args = ["-loop", "1", "-framerate", str(int(fps)), "-i", str(chemin)]
    chaines = [f"[{rang}:v]format=gbrp,setsar=1[tex]", f"{entree}format=gbrp[vbase]",
               f"[vbase][tex]blend=all_expr='A+(({FUSIONS[fusion]})-A)*{opacite:.4f}':shortest=1,format=yuv420p[vtex]"]
    dit = f"texture « {Path(str(texture['fichier'])).name} » fondue : {fusion} à {round(opacite * 100)} %"
    return args, chaines, "[vtex]", [dit]


def filtres_images(images: Any, largeur: int, hauteur: int, fps: int, premier_rang: int, dossier: str | Path,
                   entree: str, duree_s: float | None = None) -> tuple[list[str], list[str], str, list[str], list[dict]]:
    """Chaque image posée telle quelle, par-dessus : `(arguments, chaînes, étiquette de sortie, dits, poses)`."""
    from PIL import Image

    if not images:
        return [], [], entree, [], []
    if not isinstance(images, list):
        raise MediaAssemblyError("incrustation : « images » doit être une liste")
    args: list[str] = []
    chaines: list[str] = []
    dits: list[str] = []
    poses: list[dict] = []
    courant = entree
    rang = premier_rang
    for n, spec in enumerate(images):
        if not isinstance(spec, dict) or not spec.get("fichier"):
            dits.append(f"image {n + 1} : aucun fichier à poser (rien d'imposé)")
            continue
        source = _ouvrir(spec["fichier"], "image").convert("RGBA")
        b = boite(str(spec.get("ancrage") or "centre"), spec.get("largeur"), spec.get("marge"), int(largeur), int(hauteur),
                  source.width / source.height, spec.get("hauteur_min_px"))
        posee = source.resize((b["largeur"], b["hauteur"]), Image.LANCZOS)
        chemin = Path(dossier) / f"image_{n}.png"
        posee.save(chemin)
        args += ["-loop", "1", "-framerate", str(int(fps)), "-i", str(chemin)]
        enable = ""
        debut, fin = spec.get("debut_s"), spec.get("fin_s")
        if debut is not None or fin is not None:
            d = float(debut or 0.0)
            f = float(fin) if fin is not None else float(duree_s or 1e9)
            if duree_s is not None:
                d = max(0.0, float(duree_s) + d) if d < 0 else d
                f = max(0.0, float(duree_s) + f) if f < 0 else min(f, float(duree_s))
            enable = f":enable='between(t,{d:.3f},{f:.3f})'"
        sortie = f"[vimg{n}]"
        chaines.append(f"{courant}[{rang}:v]overlay=x={b['x']}:y={b['y']}:format=auto:shortest=1{enable}{sortie}")
        courant = sortie
        rang += 1
        nom = Path(str(spec["fichier"])).name
        dits.append(f"image « {nom} » posée telle quelle : {b['largeur']}×{b['hauteur']} px en {spec.get('ancrage') or 'centre'}"
                    f" (x {b['x']}, y {b['y']})" + (" — agrandie à la taille minimale" if b["agrandi_au_minimum"] else ""))
        poses.append({"fichier": str(spec["fichier"]), **b, "ancrage": spec.get("ancrage") or "centre"})
    return args, chaines, courant, dits, poses

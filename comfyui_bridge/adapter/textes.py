"""Incruster des TEXTES dans une vidéo au recollage : un titre, un appel, avec une police.

Aucun nœud du moteur n'écrit un texte avec une police du poste sur des images
sans les tenir toutes en mémoire ; l'outil d'encodage local, lui, le fait en
FLUX (`drawtext`, libfreetype), image par image, avec un minutage exact et un
fondu — c'est donc au recollage, dans l'adaptateur, que le texte s'incruste,
comme le reste du montage des médias (voir `montage_video.recoller`).

Un texte est un objet :

    {"texte": "Le titre", "police": "Segoe UI Bold", "debut_s": 0.5, "fin_s": 4,
     "position": "bas", "taille": 0.055, "couleur": "white", "fondu_s": 0.4,
     "boite": true}

* ``police`` : un CHEMIN de fichier, ou un NOM résolu dans les polices du poste
  (même règle que l'appel final de la révélation : « Segoe UI Bold » →
  ``C:/Windows/Fonts/segoeuib.ttf``) ; introuvable, le texte est refusé, jamais
  écrit dans une autre police en silence ;
* ``taille`` : en part de la HAUTEUR de l'image (0,055 = 5,5 %), pour que le
  même réglage tienne en 480p comme en 4K ;
* ``position`` : ``bas`` (le tiers inférieur, l'usage du titre et de l'appel),
  ``centre``, ``haut`` ;
* ``fondu_s`` : entrée et sortie en fondu, en secondes ;
* ``boite`` : un bandeau sombre translucide sous le texte, pour la lisibilité
  sur une image claire.

Ce module ne connaît aucun flux : il rend une chaîne de filtres que le
recollage pose sur la vidéo recollée.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.errors import MediaAssemblyError

DOSSIERS_POLICES = ("C:/Windows/Fonts", os.path.expandvars("%LOCALAPPDATA%/Microsoft/Windows/Fonts"))
_EXTENSIONS = (".ttf", ".otf", ".ttc")
POSITIONS = ("bas", "centre", "haut")


def _normaliser(nom: str) -> str:
    return "".join(ch for ch in str(nom).lower() if ch.isalnum())


def chemin_police(demande: str) -> str:
    """Le fichier d'une police : un chemin tel quel, ou un nom résolu dans les
    polices du poste — « segoeuib », « Segoe UI Bold », « arial » se lisent."""
    voulue = str(demande or "").strip()
    if not voulue:
        raise MediaAssemblyError("incrustation : aucune police demandée")
    if os.path.isfile(voulue):
        return str(Path(voulue).resolve())
    cible = _normaliser(Path(voulue).stem if voulue.lower().endswith(_EXTENSIONS) else voulue)
    for dossier in DOSSIERS_POLICES:
        try:
            fichiers = sorted(os.listdir(dossier))
        except OSError:
            continue
        for f in fichiers:
            if f.lower().endswith(_EXTENSIONS) and _normaliser(Path(f).stem) == cible:
                return str(Path(dossier, f).resolve())
    # Les polices Windows nomment leur fichier autrement que leur nom d'affichage
    # (« Segoe UI Bold » = segoeuib.ttf) : un second passage lit les noms internes.
    try:
        from PIL import ImageFont  # type: ignore
    except ImportError:
        ImageFont = None
    if ImageFont is not None:
        for dossier in DOSSIERS_POLICES:
            try:
                fichiers = sorted(os.listdir(dossier))
            except OSError:
                continue
            for f in fichiers:
                if not f.lower().endswith(_EXTENSIONS):
                    continue
                try:
                    famille, style = ImageFont.truetype(os.path.join(dossier, f), 12).getname()
                except Exception:
                    continue
                if _normaliser(f"{famille} {style}") == cible or (
                        style.lower() == "regular" and _normaliser(famille) == cible):
                    return str(Path(dossier, f).resolve())
    raise MediaAssemblyError(f"incrustation : police « {voulue} » introuvable sur ce poste")


def _echapper(texte: str) -> str:
    """Le texte tel que drawtext le lit : ses caractères de commande protégés."""
    sortie = []
    for ch in str(texte):
        if ch in "\\':%,[];=":
            sortie.append("\\" + ch)
        else:
            sortie.append(ch)
    return "".join(sortie)


def _chemin_filtre(chemin: str) -> str:
    """Un chemin Windows dans une option de filtre : barres obliques, deux-points protégé."""
    return chemin.replace("\\", "/").replace(":", "\\:")


def filtre_drawtext(texte: dict[str, Any], largeur: int, hauteur: int) -> str:
    """Le filtre d'UN texte, minuté et fondu, prêt à être posé sur la vidéo."""
    contenu = str(texte.get("texte") or "").strip()
    if not contenu:
        raise MediaAssemblyError("incrustation : un texte vide")
    police = chemin_police(texte.get("police") or "")
    debut = float(texte.get("debut_s") or 0.0)
    fin = float(texte.get("fin_s") or 0.0)
    if fin <= debut:
        raise MediaAssemblyError(f"incrustation : « {contenu[:30]} » finit ({fin} s) avant de commencer ({debut} s)")
    fondu = max(0.0, float(texte.get("fondu_s") if texte.get("fondu_s") is not None else 0.4))
    fondu = min(fondu, (fin - debut) / 2)
    taille = max(8, int(round(float(texte.get("taille") or 0.055) * hauteur)))
    position = str(texte.get("position") or "bas")
    if position not in POSITIONS:
        raise MediaAssemblyError(f"incrustation : position « {position} » inconnue ({', '.join(POSITIONS)})")
    y = {"bas": "h*0.80-th/2", "centre": "(h-th)/2", "haut": "h*0.14-th/2"}[position]
    couleur = str(texte.get("couleur") or "white")
    # alpha : 0 → 1 pendant le fondu d'entrée, 1 → 0 pendant celui de sortie.
    if fondu > 0:
        alpha = (f"if(lt(t,{debut + fondu:.3f}),(t-{debut:.3f})/{fondu:.3f},"
                 f"if(gt(t,{fin - fondu:.3f}),({fin:.3f}-t)/{fondu:.3f},1))")
    else:
        alpha = "1"
    marge = max(4, int(round(taille * 0.35)))
    boite = ""
    if texte.get("boite", True):
        boite = f":box=1:boxcolor=black@0.45:boxborderw={marge}"
    ombre = f":shadowcolor=black@0.6:shadowx={max(1, taille // 24)}:shadowy={max(1, taille // 24)}"
    return (f"drawtext=fontfile='{_chemin_filtre(police)}':text='{_echapper(contenu)}'"
            f":fontsize={taille}:fontcolor={couleur}:x=(w-tw)/2:y={y}"
            f":alpha='{alpha}':enable='between(t,{debut:.3f},{fin:.3f})'"
            f"{boite}{ombre}")


def filtres(textes: Any, largeur: int, hauteur: int, duree_s: float | None = None
            ) -> tuple[list[str], int]:
    """Les filtres de tous les textes, dans l'ordre, et le nombre de textes VIDES ignorés.

    Un texte vide n'est pas une faute : une chaîne expose « accroche » et
    « appel » et l'appelant peut n'en vouloir aucun. Un temps NÉGATIF se compte
    depuis la fin de la vidéo (``debut_s: -4`` = quatre secondes avant la fin),
    ce qui permet à une chaîne, qui ne calcule rien, de poser un appel final
    quelle que soit la durée livrée."""
    if not textes:
        return [], 0
    if not isinstance(textes, list):
        raise MediaAssemblyError("incrustation : « textes » doit être une liste")
    sortie: list[str] = []
    vides = 0
    for t in textes:
        if not isinstance(t, dict):
            continue
        if not str(t.get("texte") or "").strip():
            vides += 1
            continue
        t = dict(t)
        if duree_s is not None:
            for cle in ("debut_s", "fin_s"):
                v = t.get(cle)
                if v is not None and float(v) < 0:
                    t[cle] = max(0.0, float(duree_s) + float(v))
            if t.get("fin_s") is None or float(t["fin_s"]) > float(duree_s):
                t["fin_s"] = float(duree_s)
        sortie.append(filtre_drawtext(t, int(largeur), int(hauteur)))
    return sortie, vides

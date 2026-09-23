"""Incruster des TEXTES dans une vidéo au recollage : un titre, un appel, avec une police.

Aucun nœud du moteur n'écrit un texte avec une police du poste sur des images
sans les tenir toutes en mémoire ; l'outil d'encodage local, lui, le fait en
FLUX (`drawtext`, libfreetype), image par image, avec un minutage exact et un
fondu — c'est donc au recollage, dans l'adaptateur, que le texte s'incruste,
comme le reste du montage des médias (voir `montage_video.recoller`).

Un texte est un objet :

    {"texte": "Le titre", "police": "Segoe UI Bold", "debut_s": 0.5, "fin_s": 4,
     "position": "bas", "taille": 0.055, "couleur": "white", "fondu_s": 0.4,
     "boite": true, "fond": null}

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
  sur une image claire ;
* ``fond`` : une couleur PLEINE sous chaque ligne — celle qu'une charte déclare
  pour ses titres (« texte #0c0c0c sur fond #ffffff ») : le couple a été choisi
  pour se lire ensemble, le texte seul ne se lit pas forcément sur la vidéo
  (mesuré le 2026-09-24 : un titre #000000 sans son fond disparaissait sur une
  foule de nuit). Posé tel quel, il prime sur ``boite`` ; le liseré et l'ombre,
  faits pour lire un texte à même l'image, ne s'y ajoutent pas.

Ce module ne connaît aucun flux : il rend une chaîne de filtres que le
recollage pose sur la vidéo recollée.
"""

from __future__ import annotations

import os
import tempfile
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


LIGNES_MAX = 3


def _mesure(police: str, taille: int):
    """Une règle de mesure PIL pour cette police à cette taille, ou None si PIL
    ou la police ne se lisent pas — le texte part alors tel quel, sur une ligne."""
    try:
        from PIL import ImageFont  # type: ignore
        return ImageFont.truetype(police, max(6, int(taille)))
    except Exception:
        return None


def replier(texte: str, police: str, taille: int, largeur_max: int) -> tuple[list[str], int]:
    """Les lignes du texte à cette largeur, et la taille retenue.

    Les mots se replient à la largeur donnée ; si un mot seul ne tient pas, ou
    s'il faut plus de LIGNES_MAX lignes, la taille baisse jusqu'à ce que ça
    tienne (jamais sous 8 px). Un texte qui porte déjà des retours à la ligne
    garde ses lignes. Sans règle de mesure, le texte part tel quel."""
    texte = str(texte).strip()
    taille = int(taille)
    while True:
        regle = _mesure(police, taille)
        if regle is None:
            return [texte], taille
        lignes: list[str] = []
        trop_long = False
        for paragraphe in texte.splitlines() or [texte]:
            courante = ""
            for mot in paragraphe.split():
                essai = f"{courante} {mot}".strip()
                if regle.getlength(essai) <= largeur_max:
                    courante = essai
                else:
                    if courante:
                        lignes.append(courante)
                    courante = mot
                    if regle.getlength(mot) > largeur_max:
                        trop_long = True
            if courante:
                lignes.append(courante)
        if not trop_long and len(lignes) <= LIGNES_MAX:
            return lignes, taille
        if taille <= 8:
            return lignes[:LIGNES_MAX], taille
        taille = max(8, int(taille * 0.9))


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


def filtre_drawtext(texte: dict[str, Any], largeur: int, hauteur: int,
                    dossier: str | Path | None = None, rang: int = 0) -> list[str]:
    """Les filtres d'UN texte — un `drawtext` PAR LIGNE, minutés et fondus ensemble.

    Le texte part dans des FICHIERS (``textfile=``) : entre apostrophes, le
    graphe de filtres ne connaît aucun échappement — « aujourd'hui » fermait la
    citation et cassait tout le graphe (mesuré le 2026-09-18) ; un fichier
    accepte tout caractère. Une ligne par filtre, parce que ffmpeg 8 dessine le
    caractère de saut de ligne d'un fichier comme un glyphe manquant (mesuré :
    un carré en fin de chaque ligne, LF comme CRLF) ; les lignes se centrent
    chacune, et le bloc se centre sur la hauteur choisie."""
    contenu = str(texte.get("texte") or "").strip()
    if not contenu:
        raise MediaAssemblyError("incrustation : un texte vide")
    police = chemin_police(texte.get("police") or "")
    dossier = Path(dossier) if dossier else Path(tempfile.mkdtemp(prefix="incrustation-"))
    debut = float(texte.get("debut_s") or 0.0)
    fin = float(texte.get("fin_s") or 0.0)
    if fin <= debut:
        raise MediaAssemblyError(f"incrustation : « {contenu[:30]} » finit ({fin} s) avant de commencer ({debut} s)")
    fondu = max(0.0, float(texte.get("fondu_s") if texte.get("fondu_s") is not None else 0.4))
    fondu = min(fondu, (fin - debut) / 2)
    taille = max(8, int(round(float(texte.get("taille") or 0.055) * hauteur)))
    lignes, taille = replier(contenu, police, taille, int(largeur * 0.88))
    position = str(texte.get("position") or "bas")
    if position not in POSITIONS:
        raise MediaAssemblyError(f"incrustation : position « {position} » inconnue ({', '.join(POSITIONS)})")
    couleur = str(texte.get("couleur") or "white")
    # alpha : 0 → 1 pendant le fondu d'entrée, 1 → 0 pendant celui de sortie.
    if fondu > 0:
        alpha = (f"if(lt(t,{debut + fondu:.3f}),(t-{debut:.3f})/{fondu:.3f},"
                 f"if(gt(t,{fin - fondu:.3f}),({fin:.3f}-t)/{fondu:.3f},1))")
    else:
        alpha = "1"
    interligne = int(round(taille * 1.25))
    centre = {"bas": "h*0.80", "centre": "h*0.50", "haut": "h*0.14"}[position]
    haut_bloc = len(lignes) * interligne
    liseré = max(1, taille // 40)
    ombre = max(1, taille // 24)
    boite = ""
    fond = str(texte.get("fond") or "").strip()
    if fond:
        boite = f":box=1:boxcolor={fond}:boxborderw={max(2, taille // 4)}"
        liseré = ombre = 0
    elif texte.get("boite", False):
        boite = f":box=1:boxcolor=black@0.45:boxborderw={max(2, taille // 8)}"
    sortie: list[str] = []
    for i, ligne in enumerate(lignes):
        fichier = dossier / f"texte_{rang}_{i}.txt"
        fichier.write_text(ligne, encoding="utf-8", newline="\n")
        y = f"{centre}-{haut_bloc / 2:.0f}+{i * interligne}"
        sortie.append(
            f"drawtext=fontfile='{_chemin_filtre(police)}':textfile='{_chemin_filtre(str(fichier))}'"
            f":fontsize={taille}:fontcolor={couleur}:x=(w-tw)/2:y={y}"
            f":alpha='{alpha}':enable='between(t,{debut:.3f},{fin:.3f})':expansion=none"
            f":borderw={liseré}:bordercolor=black@0.55"
            f":shadowcolor=black@0.6:shadowx={ombre}:shadowy={ombre}{boite}")
    return sortie


def filtres(textes: Any, largeur: int, hauteur: int, duree_s: float | None = None,
            dossier: str | Path | None = None) -> tuple[list[str], int]:
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
        sortie.extend(filtre_drawtext(t, int(largeur), int(hauteur), dossier, len(sortie)))
    return sortie, vides

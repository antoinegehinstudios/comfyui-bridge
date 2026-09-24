"""Composer une IMAGE FIXE livrable : la taille exacte, puis textes, logos, texture.

Ce que `montage_video.recoller` est à une vidéo, `composer` l'est à une image :
le dernier passage avant la livraison, celui où la taille DEMANDÉE est atteinte
et où ce qui doit être lisible tel quel se pose — un titre dans une police du
poste, le logo d'une charte, une texture fondue. Un modèle de diffusion rend à
une taille qui est la sienne (des multiples de seize, un plafond de pixels) ;
l'utilisateur, lui, a demandé 1080 × 1350. C'est ici que l'un rejoint l'autre,
et c'est dit : l'image est mise à l'échelle SANS déformation (couvrir, puis
rogner au centre ce qui dépasse — quelques pixels au plus, comptés), jamais
de bandes.

Les textes, les images et la texture sont les MÊMES objets que ceux du
recollage (`textes.py`, `incrustations.py`) : une chaîne qui sait habiller une
vidéo sait habiller une image avec les mêmes mots. Une image n'a pas de temps :
les minutages et les fondus d'un texte n'y ont pas de sens et sont posés à
« toute l'image » ; un texte vide n'est pas une faute, il est compté et dit.

Ce module lance l'encodeur local une fois (une image en entrée, une image en
sortie), par les filtres que les deux modules voisins construisent.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..core.errors import MediaAssemblyError
from . import incrustations, montage_video
from . import textes as _textes

_FONTSIZE = re.compile(r"fontsize=(\d+)")


def _taille_de(fichier: Path) -> tuple[int, int]:
    from PIL import Image

    try:
        with Image.open(fichier) as im:
            return int(im.width), int(im.height)
    except Exception as exc:  # repli: aucun — une image illisible REFUSE la composition, et dit pourquoi
        raise MediaAssemblyError(f"composition : image illisible ({fichier.name} : {exc})") from exc


def cadrage(source: tuple[int, int], cible: tuple[int, int]) -> dict[str, Any]:
    """Comment la source couvre la cible : le facteur, et ce qui est rogné (en px de la cible).

    La source est mise à l'échelle jusqu'à COUVRIR la cible en gardant ses
    proportions ; ce qui dépasse est rogné au centre. Deux proportions égales
    ne rognent rien ; un modèle qui a rendu à des multiples de seize rogne
    quelques pixels — comptés ici, dits au récit."""
    (sw, sh), (cw, ch) = source, cible
    facteur = max(cw / sw, ch / sh)
    lw, lh = round(sw * facteur), round(sh * facteur)
    return {"facteur": round(facteur, 4),
            "rogne_px": {"largeur": max(0, lw - cw), "hauteur": max(0, lh - ch)}}


def _un_texte_pour_une_image(t: dict[str, Any]) -> dict[str, Any]:
    """Le même objet qu'au recollage, ramené à ce qu'une image sait : toute
    l'image, sans fondu — un texte ne s'y « affiche » pas dans le temps."""
    return {**t, "debut_s": 0.0, "fin_s": 1.0, "fondu_s": 0.0}


# L'ancre de chaque position, en part de la hauteur — la même table que le
# recollage (`textes.filtre_drawtext`), tenue ici pour empiler des textes.
ANCRES = {"bas": 0.80, "centre": 0.50, "haut": 0.14}


def _bloc(t: dict[str, Any], W: int, H: int) -> tuple[int, int]:
    """Ce que ce texte occupera : `(lignes, hauteur du bloc en px)`, replié
    comme le recollage le fera (même police, même largeur utile, même taille
    minimale) — sans rien écrire."""
    contenu = str(t.get("texte") or "").strip()
    if not contenu:
        return 0, 0
    police = _textes.chemin_police(t.get("police") or "")
    taille = max(8, int(round(float(t.get("taille") or 0.055) * H)))
    lignes, taille = _textes.replier(contenu, police, taille, int(W * 0.88))
    return len(lignes), len(lignes) * int(round(taille * 1.25))


def empiler(textes: list[dict[str, Any]], W: int, H: int) -> list[dict[str, Any]]:
    """Poser chaque texte marqué « sous_le_precedent » SOUS le texte posé avant
    lui à la même position, à la hauteur RÉELLE du bloc de celui-ci — un
    sous-titre ne se pose pas à un décalage écrit d'avance : un message qui se
    replie sur trois lignes le recouvrait (mesuré le 2026-09-24 sur un visuel
    sous charte). Un groupe qui déborderait du bas de l'image remonte d'autant,
    et c'est dit (`decalage` posé sur chaque texte du groupe)."""
    sortie: list[dict[str, Any]] = []
    precedent: dict[str, Any] | None = None    # le dernier texte posé : ancre (px), demi-bloc (px)
    groupe: list[int] = []
    for t in textes:
        if not isinstance(t, dict):
            sortie.append(t)
            continue
        t = dict(t)
        vide = not str(t.get("texte") or "").strip()
        position = str(t.get("position") or "bas")
        if position not in ANCRES or vide:
            sortie.append(t)
            continue
        lignes, bloc = _bloc(t, W, H)
        if t.pop("sous_le_precedent", False) and precedent is not None and precedent["position"] == position:
            ancre = precedent["ancre"] + precedent["demi"] + int(round(0.35 * bloc / max(1, lignes))) + bloc // 2
            t["decalage"] = round(ancre / H - ANCRES[position], 4)
            groupe.append(len(sortie))
        else:
            ancre = int(round((ANCRES[position] + float(t.get("decalage") or 0.0)) * H))
            groupe = [len(sortie)]
        precedent = {"position": position, "ancre": ancre, "demi": bloc // 2}
        t["_bas"] = ancre + bloc // 2
        sortie.append(t)
        # Le groupe déborde du bas : il remonte d'autant, d'un bloc.
        deborde = t["_bas"] - (H - int(round(0.02 * H)))
        if deborde > 0:
            for i in groupe:
                sortie[i]["decalage"] = round(float(sortie[i].get("decalage") or 0.0) - deborde / H, 4)
                sortie[i]["_bas"] = int(sortie[i]["_bas"]) - deborde
            precedent["ancre"] -= deborde
    for t in sortie:
        if isinstance(t, dict):
            t.pop("_bas", None)
    return sortie


def composer(image: str | Path, sortie: str | Path, largeur: int | None = None,
             hauteur: int | None = None, textes: Any = None, images: Any = None,
             texture: Any = None, signaler: Any = None) -> dict[str, Any]:
    """L'image livrable : à la taille demandée (ou la sienne), habillée.

    Rend `livrable`, `mesure` (largeur, hauteur, octets — les mêmes clés qu'un
    rendu), `source`, `cadrage` (facteur, pixels rognés), et ce qui a été posé :
    `textes_demandes`, `textes_poses`, `textes_vides`, `textes_dits[]` (par
    texte : posé, lignes, taille en px), `images_posees`, `texture_posee`."""
    source = Path(image)
    if not source.is_file():
        raise MediaAssemblyError(f"composition : image introuvable ({image})")
    cible = Path(sortie)
    cible.parent.mkdir(parents=True, exist_ok=True)
    sw, sh = _taille_de(source)
    W = int(largeur) if largeur else sw
    H = int(hauteur) if hauteur else sh
    if W <= 0 or H <= 0:
        raise MediaAssemblyError(f"composition : taille demandée impossible ({W}×{H})")
    dit = signaler if signaler is not None else (lambda _: None)
    cadre = cadrage((sw, sh), (W, H))
    if (sw, sh) != (W, H):
        dit(f"image {sw}×{sh} portée à {W}×{H} (×{cadre['facteur']}), rognée de "
            f"{cadre['rogne_px']['largeur']}×{cadre['rogne_px']['hauteur']} px au centre")

    args: list[str] = ["-y", "-v", "error", "-i", str(source)]
    filtre: list[str] = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={W}:{H},setsar=1,format=rgb24[v0]"]
    courant = "[v0]"
    rang = 1
    dossier = Path(tempfile.mkdtemp(prefix="composition-"))
    textes_dits: list[dict[str, Any]] = []
    vides = 0
    poses: list[dict[str, Any]] = []
    try:
        if texture:
            entrees, chaines, courant, dits = incrustations.filtre_texture(
                texture, W, H, 1, rang, dossier, courant, format_sortie="rgb24")
            args += entrees
            filtre += chaines
            rang += 1 if entrees else 0
            for d in dits:
                dit(d)
        if textes:
            if not isinstance(textes, list):
                raise MediaAssemblyError("composition : « textes » doit être une liste")
            textes = empiler(textes, W, H)
            for n, t in enumerate(textes):
                if not isinstance(t, dict):
                    continue
                sous = dossier / f"texte-{n}"
                sous.mkdir()
                lignes, vide = _textes.filtres([_un_texte_pour_une_image(t)], W, H, 1.0, sous,
                                               signaler=signaler)
                vides += vide
                if not lignes:
                    textes_dits.append({"rang": n, "pose": False, "lignes": 0, "taille_px": 0,
                                        "texte": str(t.get("texte") or "")[:80]})
                    continue
                trouve = _FONTSIZE.search(lignes[0])
                textes_dits.append({"rang": n, "pose": True, "lignes": len(lignes),
                                    "taille_px": int(trouve.group(1)) if trouve else 0,
                                    "position": str(t.get("position") or "bas"),
                                    "decalage": float(t.get("decalage") or 0.0),
                                    "texte": str(t.get("texte") or "")[:80]})
                filtre.append(courant + ",".join(lignes) + f"[vtxt{n}]")
                courant = f"[vtxt{n}]"
        if images:
            entrees, chaines, courant, dits, poses = incrustations.filtres_images(
                images, W, H, 1, rang, dossier, courant, 1.0)
            args += entrees
            filtre += chaines
            for d in dits:
                dit(d)
        args += ["-filter_complex", ";".join(filtre), "-map", courant, "-frames:v", "1",
                 "-update", "1", "-pix_fmt", "rgb24", str(cible)]
        montage_video._lancer(montage_video.outil(), args, f"composition de {source.name}")
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    if not cible.is_file() or not cible.stat().st_size:
        raise MediaAssemblyError(f"composition : rien n'a été écrit dans {cible}")
    lw, lh = _taille_de(cible)
    poses_textes = sum(1 for t in textes_dits if t["pose"])
    if textes:
        dit(f"{poses_textes} texte(s) posé(s) sur l'image" + (f", {vides} vide(s)" if vides else ""))
    return {"livrable": str(cible.resolve()),
            "mesure": {"width": lw, "height": lh, "bytes": cible.stat().st_size},
            "source": {"width": sw, "height": sh, "fichier": str(source.resolve())},
            "cadrage": cadre,
            "textes_demandes": sum(1 for t in (textes or []) if isinstance(t, dict)
                                   and str(t.get("texte") or "").strip()),
            "textes_poses": poses_textes, "textes_vides": vides, "textes_dits": textes_dits,
            "images_posees": poses,
            "texture_posee": bool(texture and isinstance(texture, dict) and texture.get("fichier"))}


def apercu_fixe(image: str | Path, sortie: str | Path, largeur: int = 240) -> dict[str, Any]:
    """La vignette d'un livrable IMAGE : l'image réduite, en WebP — le pendant
    de `montage_video.apercu_anime` pour ce qui ne bouge pas. ``sortie`` est
    donné SANS extension, comme pour l'aperçu animé ; un seul aperçu par mode."""
    from PIL import Image

    source, sortie = Path(image), Path(sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as im:
        im = im.convert("RGB")
        k = int(largeur) / im.width
        petite = im.resize((int(largeur), max(1, round(im.height * k))), Image.LANCZOS)
    fichier = sortie.with_suffix(".webp")
    brouillon = sortie.with_name(f".{sortie.name}.part.webp")
    petite.save(brouillon, "WEBP", quality=80, method=6)
    os.replace(brouillon, fichier)
    ancien = sortie.with_suffix(".gif")
    if ancien.exists():
        ancien.unlink()
    return {"fichier": str(fichier.resolve()), "octets": fichier.stat().st_size,
            "images": 1, "pas": 1, "format": "webp"}


def apercu_de_livrable(fichier: str | Path, sortie: str | Path) -> dict[str, Any]:
    """L'aperçu qui convient au livrable : animé pour une vidéo, fixe pour une image."""
    from .media import media_kind

    genre = media_kind(Path(fichier))
    if genre == "video":
        return montage_video.apercu_anime(Path(fichier), Path(sortie))
    if genre == "image":
        return apercu_fixe(Path(fichier), Path(sortie))
    raise MediaAssemblyError(f"aperçu : {Path(fichier).name} n'est ni une vidéo ni une image ({genre})")

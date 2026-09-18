"""Le montage des médias : extraire, recoller, mesurer les raccords.

Aucun nœud ComfyUI ne coupe la queue d'une vidéo ni ne joint deux clips : ce
travail-là se fait avec l'outil d'encodage local. Il vit ICI, dans
l'adaptateur, pour la même raison que le reste — le cœur d'une chaîne parle
d'étapes, pas de lignes de commande.

Trois décisions sont structurelles, et elles ont été mesurées ailleurs (le
raccord de blocs de `maestro`) avant d'être reprises ici :

  * une QUEUE remise au tour suivant est extraite SANS PERTE (``-qp 0`` en
    yuv444p) et son compte d'images est REVÉRIFIÉ : ré-encodée avec perte, elle
    ajoutait une dérive à chaque tour, invisible sur un raccord et cumulée sur
    dix ; comptée d'après les métadonnées, elle en livrait 49 pour 50 demandées ;
  * un RECOLLAGE ré-encode uniformément (libx264 crf 18, aac) et substitue une
    piste silencieuse aux parts muettes : la copie de flux ne joint que des
    fichiers déjà identiques, ce que deux workflows différents ne sont jamais ;
  * un RACCORD se mesure entre la dernière image GARDÉE de la part i et la
    première image GARDÉE de la part i+1 — après rognage, donc, sinon on mesure
    des images que le montage a jetées.
"""

from __future__ import annotations

import re
import shutil
import os
import subprocess
from pathlib import Path
from typing import Any

from ..core.errors import MediaAssemblyError
from . import morceaux

_IMAGES = (".png", ".jpg", ".jpeg", ".webp")
_MOTIF_SSIM = re.compile(r"All:\s*([0-9.]+)")
_MOTIF_ERREUR = re.compile(
    r"error|invalid|no such|failed|unable|not found|permission|denied|could not",
    re.IGNORECASE)
# Les dernières lignes de l'outil ne disent rien de la cause : elles constatent
# l'échec. Prendre « la dernière ligne fautive » les prenait, elles, et laissait
# la vraie raison (« No such file or directory ») deux lignes plus haut.
_EPILOGUES = re.compile(r"^(conversion failed!?|error opening output file.*)$", re.IGNORECASE)


def outil() -> str:
    """L'encodeur. Le même que le recollage des morceaux : une seule élection."""
    trouve = morceaux.ffmpeg()
    if trouve is None:
        raise MediaAssemblyError("ffmpeg est introuvable : aucun montage n'est possible ici")
    return trouve


def sonde() -> str:
    """La sonde de mesure est le binaire frère de l'encodeur (ffmpeg -> ffprobe)."""
    encodeur = Path(outil())
    voisine = encodeur.with_name(encodeur.name.replace("ffmpeg", "ffprobe"))
    if voisine.exists():
        return str(voisine)
    trouve = shutil.which("ffprobe")
    if trouve:
        return trouve
    raise MediaAssemblyError(f"ffprobe est introuvable à côté de {encodeur}")


def disponible() -> bool:
    """Y a-t-il de quoi monter ici ? Répond sans lever : un test s'en sert."""
    try:
        outil()
        sonde()
        return True
    except MediaAssemblyError:
        return False


def ligne_decisive(stderr: str) -> str:
    """La ligne qui dit POURQUOI. Rendre les 400 lignes d'un ffmpeg bavard
    revient à ne rien dire du tout."""
    lignes = [l.strip() for l in str(stderr).splitlines() if l.strip()]
    fautives = [l for l in lignes if _MOTIF_ERREUR.search(l)]
    parlantes = [l for l in fautives if not _EPILOGUES.match(l)]
    for candidates in (parlantes, fautives, lignes):
        if candidates:
            return candidates[-1]
    return "aucune sortie d'erreur"


def _lancer(binaire: str, args: list[str], quoi: str, brut: bool = False) -> tuple[Any, str]:
    try:
        proc = subprocess.run([binaire, *args], capture_output=True,
                              text=not brut, encoding=None if brut else "utf-8",
                              errors=None if brut else "replace")
    except OSError as exc:
        raise MediaAssemblyError(f"{quoi} : impossible de lancer {binaire} ({exc})") from exc
    stderr = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode(
        "utf-8", "replace")
    if proc.returncode != 0:
        raise MediaAssemblyError(f"{quoi} : {ligne_decisive(stderr)}",
                                 returncode=proc.returncode, stderr_tail=stderr[-800:])
    return proc.stdout, stderr


def _cadence(brut: Any) -> float:
    if not brut:
        return 0.0
    texte = str(brut)
    if "/" in texte:
        haut, _, bas = texte.partition("/")
        try:
            return float(haut) / float(bas) if float(bas) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(texte)
    except ValueError:
        return 0.0


def _sonder(chemin: Path) -> dict[str, Any]:
    import json
    stdout, _ = _lancer(sonde(), ["-v", "error", "-print_format", "json", "-show_format",
                                  "-show_streams", str(chemin)], f"mesure de {chemin.name}")
    try:
        return json.loads(stdout)
    except Exception as exc:
        raise MediaAssemblyError(f"mesure de {chemin.name} : sortie de sonde illisible") from exc


def mesurer(chemin: str | Path) -> dict[str, Any]:
    """Ce que le fichier contient : dimensions, durée, images, octets."""
    p = Path(chemin)
    if not p.is_file():
        raise MediaAssemblyError(f"fichier absent ou illisible : {p}")
    doc = _sonder(p)
    flux = doc.get("streams") or []
    video = next((s for s in flux if s.get("codec_type") == "video"), None)
    duree = doc.get("format", {}).get("duration") or (video or {}).get("duration")
    try:
        duree = float(duree)
    except (TypeError, ValueError):
        duree = 0.0
    fps = _cadence((video or {}).get("avg_frame_rate")) or _cadence((video or {}).get("r_frame_rate"))
    images = (video or {}).get("nb_frames")
    try:
        images = int(images)
    except (TypeError, ValueError):
        images = round(duree * fps) if duree and fps else 0
    return {"width": int((video or {}).get("width") or 0),
            "height": int((video or {}).get("height") or 0),
            "duration_s": round(duree, 3), "frames": images,
            "fps": round(fps, 3) if fps else 0,
            "bytes": p.stat().st_size}


def compter_images(chemin: str | Path) -> int:
    """Le nombre EXACT d'images, par décodage. Les métadonnées peuvent mentir,
    et un compte faux fait remettre au tour suivant une queue tronquée."""
    stdout, _ = _lancer(sonde(), ["-v", "error", "-count_frames", "-select_streams", "v:0",
                                  "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                                  str(chemin)], f"comptage de {Path(chemin).name}")
    try:
        return int(str(stdout).strip().splitlines()[0])
    except (ValueError, IndexError):
        raise MediaAssemblyError(
            f"comptage de {Path(chemin).name} : nombre d'images illisible") from None


def extraire_queue(video: str | Path, images: int, sortie: str | Path) -> dict[str, Any]:
    """Les ``images`` DERNIÈRES images, en clip sans perte, compte vérifié."""
    source = Path(video)
    if not source.is_file():
        raise MediaAssemblyError(f"extraction de queue : fichier absent ({source})")
    voulues = int(images)
    if voulues <= 0:
        raise MediaAssemblyError(f"extraction de queue : « images » doit être > 0 (reçu {images})")
    total = compter_images(source)
    if voulues > total:
        raise MediaAssemblyError(
            f"extraction de queue : {source.name} tient {total} images, {voulues} demandées")
    debut = total - voulues
    cible = Path(sortie)
    cible.parent.mkdir(parents=True, exist_ok=True)
    _lancer(outil(), ["-y", "-v", "error", "-i", str(source),
                      "-vf", f"select='gte(n,{debut})',setpts=PTS-STARTPTS", "-vsync", "0",
                      "-an", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(cible)],
            f"extraction de queue de {source.name}")
    obtenues = compter_images(cible)
    if obtenues != voulues:
        raise MediaAssemblyError(
            f"extraction de queue inexacte : {cible.name} porte {obtenues} images "
            f"au lieu de {voulues}")
    return {"fichier": str(cible.resolve()), "images": obtenues,
            "bytes": cible.stat().st_size}


def extraire_image(video: str | Path, position: Any, sortie: str | Path) -> dict[str, Any]:
    """Une image, par son INDEX exact — jamais par un temps approché : « last »
    doit livrer LA dernière image, pas une voisine."""
    source = Path(video)
    if not source.is_file():
        raise MediaAssemblyError(f"extraction d'image : fichier absent ({source})")
    cible = Path(sortie)
    cible.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() in _IMAGES:
        shutil.copyfile(source, cible)
        return {"fichier": str(cible.resolve()), "bytes": cible.stat().st_size}
    if position in (None, "first"):
        index = 0
    elif position == "last":
        index = compter_images(source) - 1
    else:
        try:
            index = max(0, int(position))
        except (TypeError, ValueError):
            raise MediaAssemblyError(
                f"extraction d'image : position incomprise ({position!r})") from None
    _lancer(outil(), ["-y", "-v", "error", "-i", str(source),
                      "-vf", f"select='eq(n,{index})'", "-vsync", "0", "-frames:v", "1",
                      str(cible)], f"extraction d'image de {source.name}")
    if not cible.exists() or not cible.stat().st_size:
        raise MediaAssemblyError(f"extraction d'image : rien n'est sorti pour {source.name}")
    return {"fichier": str(cible.resolve()), "index": index, "bytes": cible.stat().st_size}


def parts_normalisees(parts: Any, chevauchement: int = 0,
                      signaler: Any = None) -> list[dict[str, Any]]:
    """Des parts en une forme unique : ``{fichier, depuis_image, sauf_les_dernieres}``.

    ``depuis_image`` est un rognage de TÊTE : les images de chevauchement que
    l'amont a re-rendues et qu'il faut jeter. La première part n'a rien devant
    elle : le rognage par défaut ne s'y applique pas.

    ``sauf_les_dernieres`` est le rognage de QUEUE, en images. Il sert quand
    l'AVAL a déjà repris la fin de cette part : depuis le 2026-09-16, l'appel
    final lit la conclusion paresseusement et ne rend QUE ses propres images —
    la conclusion entre donc dans le montage sans les images que l'appel a
    reprises, sinon on les verrait deux fois.

    Une part dont le fichier est vide ou ``null`` est IGNORÉE, et dite : c'est
    ainsi qu'une étape sautée rend « rien » sans casser le montage qui la nomme.
    Il en faut au moins une qui reste.
    """
    if not isinstance(parts, (list, tuple)) or not parts:
        raise MediaAssemblyError("« parts » doit être une liste non vide")
    plates: list[Any] = []
    for p in parts:
        plates.extend(p) if isinstance(p, list) else plates.append(p)
    sorties: list[dict[str, Any]] = []
    for rang, p in enumerate(plates):
        # Le défaut de chevauchement suit le rang des parts RETENUES : une part
        # ignorée ne doit pas faire croire à la suivante qu'elle a un amont.
        defaut = 0 if not sorties else max(0, int(chevauchement or 0))
        if isinstance(p, dict):
            fichier = p.get("fichier") or p.get("path")
            ecrit = p.get("depuis_image")
            depuis = defaut if ecrit is None else max(0, int(ecrit))
            queue = max(0, int(p.get("sauf_les_dernieres") or 0))
        else:
            fichier, depuis, queue = p, defaut, 0
        if not fichier:
            if callable(signaler):
                signaler(f"part n°{rang + 1} sans fichier : ignorée")
            continue
        chemin = Path(str(fichier))
        if not chemin.is_file():
            raise MediaAssemblyError(f"part n°{rang + 1} absente : {chemin}")
        sorties.append({"fichier": str(chemin.resolve()), "depuis_image": depuis,
                        "sauf_les_dernieres": queue})
    if not sorties:
        raise MediaAssemblyError("aucune part à joindre : toutes sont vides")
    return sorties


def recoller(parts: Any, sortie: str | Path, fps: int = 25, largeur: int = 1280,
             hauteur: int = 720, chevauchement: int = 0,
             signaler: Any = None, textes: Any = None) -> dict[str, Any]:
    """Joindre des parts en UN livrable, ré-encodé uniformément — et y incruster
    des textes (voir `textes.py`), en flux, au même passage."""
    pieces = parts_normalisees(parts, chevauchement, signaler)
    cible = Path(sortie)
    cible.parent.mkdir(parents=True, exist_ok=True)
    fps = int(fps or 25)
    largeur, hauteur = int(largeur), int(hauteur)

    sons: list[dict[str, Any]] = []
    for piece in pieces:
        doc = _sonder(Path(piece["fichier"]))
        flux = doc.get("streams") or []
        video = next((s for s in flux if s.get("codec_type") == "video"), None)
        a_du_son = any(s.get("codec_type") == "audio" for s in flux)
        try:
            duree = float(doc.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            duree = 0.0
        cadence = _cadence((video or {}).get("avg_frame_rate")) or \
            _cadence((video or {}).get("r_frame_rate")) or fps
        saute = piece["depuis_image"] / cadence if piece["depuis_image"] and cadence else 0.0
        rogne = (piece["sauf_les_dernieres"] / cadence
                 if piece["sauf_les_dernieres"] and cadence else 0.0)
        if not a_du_son and duree <= 0:
            raise MediaAssemblyError(
                f"recollage : {Path(piece['fichier']).name} n'a ni piste audio ni durée "
                f"mesurable — impossible de dimensionner la piste silencieuse")
        sons.append({"present": a_du_son, "duree": duree, "saute": saute, "rogne": rogne,
                     "images": compter_images(Path(piece["fichier"]))
                     if piece["sauf_les_dernieres"] else 0})

    args: list[str] = ["-y", "-v", "error"]
    for piece in pieces:
        args += ["-i", piece["fichier"]]
    rang_muet: list[int | None] = []
    suivant = len(pieces)
    for i, piece in enumerate(pieces):
        if sons[i]["present"]:
            rang_muet.append(None)
            continue
        args += ["-f", "lavfi",
                 "-t", f"{max(0.04, sons[i]['duree'] - sons[i]['saute'] - sons[i]['rogne']):.3f}",
                 "-i", "anullsrc=r=48000:cl=stereo"]
        rang_muet.append(suivant)
        suivant += 1

    filtre: list[str] = []
    for i, piece in enumerate(pieces):
        garde = ""
        if piece["depuis_image"] or piece["sauf_les_dernieres"]:
            bornes = []
            if piece["depuis_image"]:
                bornes.append(f"start_frame={piece['depuis_image']}")
            if piece["sauf_les_dernieres"]:
                # `trim` compte les images de l'ENTRÉE : la fin se dit en numéro
                # absolu (total − N), jamais en longueur. Une part plus courte
                # que ce qu'on lui retire ne garderait rien : on la refuse
                # plutôt que de livrer un montage amputé en silence.
                reste = int(sons[i]["images"]) - int(piece["sauf_les_dernieres"])
                if reste <= int(piece["depuis_image"]):
                    raise MediaAssemblyError(
                        f"recollage : {Path(piece['fichier']).name} n'a que "
                        f"{sons[i]['images']} images, on lui en retire "
                        f"{piece['sauf_les_dernieres']} de queue — il n'en resterait rien")
                bornes.append(f"end_frame={reste}")
            garde = f"trim={':'.join(bornes)},setpts=PTS-STARTPTS,"
        tete = garde
        filtre.append(
            f"[{i}:v]{tete}scale={largeur}:{hauteur}:force_original_aspect_ratio=decrease,"
            f"pad={largeur}:{hauteur}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[v{i}]")
        if rang_muet[i] is None:
            coupe = (f"atrim=start={sons[i]['saute']:.3f},asetpts=PTS-STARTPTS,"
                     if sons[i]["saute"] else "")
            filtre.append(f"[{i}:a]{coupe}aresample=48000,"
                          f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            filtre.append(f"[{rang_muet[i]}:a]"
                          f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    entrelace = "".join(f"[v{i}][a{i}]" for i in range(len(pieces)))
    filtre.append(f"{entrelace}concat=n={len(pieces)}:v=1:a=1[vout][aout]")
    sortie_video = "[vout]"
    dossier_textes = None
    if textes:
        import shutil as _shutil
        import tempfile as _tempfile
        from . import textes as _textes
        dossier_textes = _tempfile.mkdtemp(prefix="incrustation-")
        duree_totale = sum(max(0.0, s["duree"] - s["saute"] - s["rogne"]) for s in sons)
        incrustations, vides = _textes.filtres(textes, largeur, hauteur, duree_totale, dossier_textes)
        if signaler is not None:
            if incrustations:
                signaler(f"{len(incrustations)} texte(s) incrusté(s) au recollage")
            if vides:
                signaler(f"{vides} texte(s) vide(s), sans incrustation")
        if incrustations:
            filtre.append("[vout]" + ",".join(incrustations) + "[vtxt]")
            sortie_video = "[vtxt]"

    args += ["-filter_complex", ";".join(filtre), "-map", sortie_video, "-map", "[aout]",
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-movflags", "+faststart", str(cible)]
    try:
        _lancer(outil(), args, f"recollage de {len(pieces)} parts")
    finally:
        if dossier_textes:
            _shutil.rmtree(dossier_textes, ignore_errors=True)
    return {"livrable": str(cible.resolve()), "parts": len(pieces),
            "mesure": mesurer(cible)}


def concatener(parts: Any, sortie: str | Path) -> dict[str, Any]:
    """Joindre des parts SANS les ré-encoder : la copie de flux.

    Réservée aux parts qu'un MÊME encodeur a écrites avec les MÊMES réglages —
    les tranches d'un seul rendu, découpé pour tenir en mémoire. Le démultiplexeur
    « concat » d'ffmpeg réécrit les horodatages et ne touche à aucune image :
    la jonction est celle des images elles-mêmes, et le livrable ne subit
    aucune génération de perte de plus que le rendu d'un seul tenant. Un
    rognage de tête (``depuis_image``) est refusé : une copie ne coupe pas.
    """
    pieces = parts_normalisees(parts)
    if any(piece["depuis_image"] for piece in pieces):
        raise MediaAssemblyError("concaténation sans ré-encodage : une part demande un rognage "
                                 "de tête, ce qu'une copie de flux ne sait pas faire")
    cible = Path(sortie)
    cible.parent.mkdir(parents=True, exist_ok=True)
    liste = cible.with_name(f".{cible.stem}.parts.txt")
    # Le format du démultiplexeur : une ligne « file '<chemin>' » par part, le
    # chemin en barres obliques et les apostrophes échappées à sa façon.
    lignes = []
    for piece in pieces:
        chemin = Path(piece["fichier"]).resolve().as_posix().replace("'", "'\\''")
        lignes.append("file '" + chemin + "'\n")
    liste.write_text("".join(lignes), encoding="utf-8")
    try:
        _lancer(outil(), ["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
                          "-c", "copy", "-movflags", "+faststart", str(cible)],
                "concaténation sans ré-encodage")
    finally:
        liste.unlink(missing_ok=True)
    if not cible.exists() or not cible.stat().st_size:
        raise MediaAssemblyError(f"concaténation sans ré-encodage : rien n'a été écrit dans {cible}")
    return {"livrable": str(cible.resolve()), "mesure": mesurer(cible), "parts": len(pieces),
            "reencode": False}


def mesurer_raccords(parts: Any, travail: str | Path, chevauchement: int = 0,
                     signaler: Any = None) -> dict[str, Any]:
    """La ressemblance de part en part, aux frontières du montage RÉEL.

    « Réel » veut dire : la dernière image GARDÉE d'une part (sa queue rognée ne
    sera pas dans le livrable) contre la première gardée de la suivante. Mesurer
    la dernière image du FICHIER aurait jugé une frontière que personne ne voit.
    """
    pieces = parts_normalisees(parts, chevauchement, signaler)
    if len(pieces) < 2:
        raise MediaAssemblyError("mesure de raccords : il en faut au moins deux")
    dossier = Path(travail)
    dossier.mkdir(parents=True, exist_ok=True)
    paires: list[dict[str, Any]] = []
    for i in range(len(pieces) - 1):
        avant, apres = pieces[i], pieces[i + 1]
        derniere = "last"
        if avant["sauf_les_dernieres"]:
            derniere = max(0, compter_images(Path(avant["fichier"]))
                           - int(avant["sauf_les_dernieres"]) - 1)
        fin = extraire_image(avant["fichier"], derniere, dossier / f"raccord_{i}_a.png")
        depart = apres["depuis_image"] if apres["depuis_image"] else "first"
        debut = extraire_image(apres["fichier"], depart, dossier / f"raccord_{i}_b.png")
        _, stderr = _lancer(outil(), ["-v", "info", "-i", fin["fichier"], "-i", debut["fichier"],
                                      "-filter_complex", "ssim", "-f", "null", "-"],
                            f"mesure du raccord n°{i + 1}")
        trouve = _MOTIF_SSIM.search(stderr)
        if not trouve:
            raise MediaAssemblyError(
                f"mesure du raccord n°{i + 1} : sortie ssim illisible "
                f"({ligne_decisive(stderr)})")
        paires.append({"a": avant["fichier"], "b": apres["fichier"],
                       "similarite": float(trouve.group(1))})
    valeurs = [p["similarite"] for p in paires]
    return {"paires": paires, "nombre": len(paires), "pire": min(valeurs),
            "meilleure": max(valeurs), "moyenne": sum(valeurs) / len(valeurs)}


_ENCODEURS: set[str] | None = None


def encodeur_present(nom: str) -> bool:
    """L'encodeur est-il compilé dans CET ffmpeg ? Demandé une fois, jamais
    supposé : un WebP animé sans libwebp sort vide, sans une ligne d'erreur."""
    global _ENCODEURS
    if _ENCODEURS is None:
        try:
            stdout, _ = _lancer(outil(), ["-hide_banner", "-encoders"], "liste des encodeurs")
            _ENCODEURS = {ligne.split()[1] for ligne in str(stdout).splitlines()
                          if len(ligne.split()) > 1 and ligne.startswith(" ")}
        except MediaAssemblyError:
            _ENCODEURS = set()
    return nom in _ENCODEURS


def apercu_anime(video: str | Path, sortie: str | Path, largeur: int = 240,
                 images: int = 48, cadence: int = 8) -> dict[str, Any]:
    """Une image animée LÉGÈRE qui résume TOUTE la vidéo — ce qu'un mode
    produit, en un coup d'œil sur sa vignette.

    Pas les premières secondes : une révélation se joue sur la durée entière,
    et ses six premières secondes ne montrent que du papier blanc. On prend
    donc une image toutes les K, réparties sur la longueur, jouées en boucle à
    huit par seconde : 48 images font six secondes.

    Le format est le WebP animé quand cet ffmpeg sait l'écrire, le GIF sinon —
    et le résultat DIT lequel. Mesuré sur la révélation pour podcast (82 s,
    704×1280) : 2 272 Ko en GIF 240 px à 64 couleurs, 267 Ko en WebP 240 px
    (q 55). Un GIF n'est léger qu'à 160 px et 32 couleurs (499 Ko), où la
    vignette ne se lit plus ; le WebP tient la lisibilité ET le poids.
    ``sortie`` est donné SANS extension : la fonction la pose selon le format.
    """
    video, sortie = Path(video), Path(sortie)
    total = compter_images(video)
    pas = max(1, round(total / max(1, images)))
    sortie.parent.mkdir(parents=True, exist_ok=True)
    # La virgule de `mod` est échappée pour le parseur de filtres d'ffmpeg
    # (elle séparerait sinon deux filtres) : un antislash réel, écrit ici sans
    # que Python n'en fasse une séquence.
    virgule = chr(92) + ","
    base = (f"select='not(mod(n{virgule}{pas}))',setpts=N/({cadence}*TB),"
            f"scale={int(largeur)}:-2:flags=lanczos")
    # L'encodeur écrit dans un fichier de côté, remplacé d'un coup à la fin :
    # écrit en place, une vignette en cours de refabrication était servie
    # tronquée — mesuré : un lanceur a reçu 0 octet en 200 pendant qu'ffmpeg
    # réécrivait l'aperçu, et la carte est restée vide.
    if encodeur_present("libwebp_anim"):
        fichier = sortie.with_suffix(".webp")
        brouillon = sortie.with_name(f".{sortie.name}.part.webp")
        _lancer(outil(), ["-y", "-v", "error", "-i", str(video), "-vf", base,
                          "-c:v", "libwebp_anim", "-q:v", "55", "-compression_level", "6",
                          "-loop", "0", "-an", str(brouillon)],
                f"aperçu animé de {video.name}")
        forme = "webp"
    else:
        fichier = sortie.with_suffix(".gif")
        brouillon = sortie.with_name(f".{sortie.name}.part.gif")
        filtre = (f"{base},split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];"
                  f"[b][p]paletteuse=dither=bayer:bayer_scale=3")
        _lancer(outil(), ["-y", "-v", "error", "-i", str(video), "-vf", filtre,
                          "-loop", "0", "-an", str(brouillon)],
                f"aperçu animé de {video.name}")
        forme = "gif"
    if not brouillon.exists() or not brouillon.stat().st_size:
        raise MediaAssemblyError(f"aperçu animé : rien n'a été écrit dans {brouillon}")
    os.replace(brouillon, fichier)
    for ancien in (sortie.with_suffix(".gif"), sortie.with_suffix(".webp")):
        if ancien != fichier and ancien.exists():
            ancien.unlink()              # un seul aperçu par mode, jamais deux formats
    return {"fichier": str(fichier.resolve()), "octets": fichier.stat().st_size,
            "images": min(images, total), "pas": pas, "format": forme}

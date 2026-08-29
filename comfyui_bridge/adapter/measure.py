"""Mesurer le média RÉELLEMENT produit.

Ce qu'un appelant demande n'est pas toujours ce qu'il obtient : un workflow peut
recalculer les dimensions (latent à la moitié puis suréchantillonné, longueur
passée par une expression) : 352x224 demandés pour 1 s peuvent rendre 320x192
pour 0,75 s, sans que rien ne le signale.

On lit donc le fichier produit. On ne lit que ce qu'on sait lire, et on se tait
sur le reste : une mesure absente vaut mieux qu'une mesure inventée.

    PNG / JPEG / WEBP  -> Pillow (déjà utilisé pour l'élément neutre)
    MP4 / MOV          -> les boîtes `moov/mvhd` (durée) et `moov/trak/tkhd`
    FLAC               -> le bloc STREAMINFO
    GLB / glTF         -> le document glTF : sommets et triangles
    WEBM / MKV / OGG   -> non lus : rien n'est annoncé
    OBJ / STL / PLY / splats -> non lus : rien n'est annoncé

Une géométrie n'a ni largeur ni durée : lui en inventer une pour remplir la
fiche ferait dire au service qu'un maillage fait 1024x1024. On dit ce qu'un
maillage a — des sommets et des triangles — ou on ne dit rien.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


def _iter_boxes(buf: bytes, start: int, end: int):
    i = start
    while i + 8 <= end:
        size = struct.unpack(">I", buf[i:i + 4])[0]
        kind = buf[i + 4:i + 8]
        if size == 0:
            size = end - i
        yield kind, i + 8, i + size
        if size < 8:
            break
        i += size


def _find_box(buf: bytes, path: tuple[bytes, ...], start: int = 0, end: int | None = None):
    end = len(buf) if end is None else end
    for kind, first, last in _iter_boxes(buf, start, end):
        if kind != path[0]:
            continue
        if len(path) == 1:
            return first, last
        found = _find_box(buf, path[1:], first, last)
        if found:
            return found
    return None


def _measure_mp4(data: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    header = _find_box(data, (b"moov", b"mvhd"))
    if header:
        at, _ = header
        if data[at] == 0:
            scale, length = struct.unpack(">II", data[at + 12:at + 20])
        else:
            scale, length = struct.unpack(">IQ", data[at + 20:at + 32])
        if scale:
            out["duration_s"] = round(length / scale, 3)
    track = _find_box(data, (b"moov", b"trak", b"tkhd"))
    if track:
        at, _ = track
        offset = 76 if data[at] == 0 else 88
        width, height = struct.unpack(">II", data[at + offset:at + offset + 8])
        if width and height:                  # 16.16 fixed point
            out["width"], out["height"] = width >> 16, height >> 16
    return out


def _measure_flac(data: bytes) -> dict[str, Any]:
    # STREAMINFO : premier bloc de métadonnées, longueur fixe de 34 octets.
    if len(data) < 42 or data[:4] != b"fLaC":
        return {}
    info = data[8:42]
    rate = int.from_bytes(info[10:13], "big") >> 4
    samples = ((info[13] & 0x0F) << 32) | int.from_bytes(info[14:18], "big")
    return {"duration_s": round(samples / rate, 3)} if rate and samples else {}


def _gltf_document(p: Path) -> dict[str, Any]:
    """Le document glTF d'un fichier — le JSON seul, jamais la géométrie binaire.

    Un GLB est un conteneur : entête `glTF`, puis des morceaux. Le premier est
    le JSON qui décrit la scène ; on ne lit que celui-là. Un maillage estimé
    depuis une photo pèse des dizaines de méga-octets dont on n'a aucun besoin
    pour le compter.
    """
    if p.suffix.lower() == ".gltf":
        return json.loads(p.read_text(encoding="utf-8"))
    with p.open("rb") as f:
        entete = f.read(12)
        if len(entete) < 12 or entete[:4] != b"glTF":
            return {}
        morceau = f.read(8)
        if len(morceau) < 8:
            return {}
        taille, genre = struct.unpack("<I4s", morceau)
        if genre != b"JSON":
            return {}
        return json.loads(f.read(taille).decode("utf-8"))


def _measure_gltf(p: Path) -> dict[str, Any]:
    doc = _gltf_document(p)
    accesseurs = doc.get("accessors") or []

    def compte(indice: Any) -> int:
        if isinstance(indice, int) and 0 <= indice < len(accesseurs):
            return int(accesseurs[indice].get("count") or 0)
        return 0

    sommets = triangles = 0
    for maillage in doc.get("meshes") or []:
        for morceau in maillage.get("primitives") or []:
            positions = compte((morceau.get("attributes") or {}).get("POSITION"))
            sommets += positions
            # 4 = TRIANGLES, le mode par défaut de glTF. Un autre mode (lignes,
            # points) ne compte pas des triangles : on n'en annonce pas.
            if morceau.get("mode", 4) == 4:
                indices = morceau.get("indices")
                triangles += (compte(indices) if indices is not None else positions) // 3
    mesure: dict[str, Any] = {}
    if sommets:
        mesure["vertices"] = sommets
    if triangles:
        mesure["triangles"] = triangles
    return mesure


def measure(path: str | Path) -> dict[str, Any]:
    """Ce que le fichier produit contient vraiment. ``{}`` si on ne sait pas lire."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix in (".png", ".jpg", ".jpeg", ".webp"):
            from PIL import Image
            with Image.open(p) as img:
                out = {"width": img.width, "height": img.height}
                frames = getattr(img, "n_frames", 1)
                if frames > 1:
                    out["frames"] = frames
                return out
        if suffix in (".mp4", ".mov", ".m4a"):
            return _measure_mp4(p.read_bytes())
        if suffix == ".flac":
            return _measure_flac(p.read_bytes())
        if suffix in (".glb", ".gltf"):
            return _measure_gltf(p)
    except Exception:
        return {}                              # illisible : on n'annonce rien
    return {}

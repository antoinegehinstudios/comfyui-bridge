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
    WEBM / MKV / OGG   -> non lus : rien n'est annoncé
"""

from __future__ import annotations

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
    except Exception:
        return {}                              # illisible : on n'annonce rien
    return {}

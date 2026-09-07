"""Recoller les morceaux d'un montage en un seul livrable.

Un montage en blocs produit un fichier par bloc : c'est ce qui garde la mémoire
constante, un bloc à la fois, quelle que soit la durée. Mais ce que le demandeur
a commandé est UNE vidéo, pas une collection de bouts — la fin de chaîne doit
donc les joindre et nommer le fichier obtenu.

Aucun nœud ComfyUI ne concatène des fichiers vidéo ; le recollage se fait donc
ici, en COPIE DE FLUX : les morceaux ne sont pas ré-encodés, donc rien n'est
re-compressé et l'opération ne coûte ni qualité ni mémoire.

L'ordre vient du NOM (``bloc_000``, ``bloc_001``…), écrit par le montage à
partir du rang du bloc. Se fier à l'ordre où le moteur a fini d'écrire aurait
été se fier à son ordonnanceur.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_RANG = re.compile(r"_(\d+)(?:_\d+)?\s*$")


def _rang(chemin: Path) -> tuple[int, str]:
    """Le rang écrit dans le nom, sinon le nom lui-même comme dernier recours."""
    m = _RANG.search(chemin.stem)
    return (int(m.group(1)) if m else 10 ** 9, chemin.stem)


def ffmpeg() -> str | None:
    """Le ffmpeg à employer : celui du PATH, sinon celui posé sur ce poste."""
    trouve = shutil.which("ffmpeg")
    if trouve:
        return trouve
    connu = Path(r"E:\Programmes\ffmpeg\bin\ffmpeg.exe")
    return str(connu) if connu.exists() else None


def a_recoller(chemins: list[str], prefixe: str) -> list[Path]:
    """Les morceaux du montage, dans l'ordre de leur rang.

    Le préfixe déclaré par la recette dit lesquels en sont : un run peut
    produire d'autres fichiers, et les joindre tous ferait un livrable faux.
    """
    marque = prefixe.replace("\\", "/").rsplit("/", 1)[-1]
    morceaux = [Path(c) for c in chemins
                if marque in Path(c).name and Path(c).suffix.lower() in (".mp4", ".mkv", ".webm")]
    return sorted(morceaux, key=_rang)


def joindre(morceaux: list[Path], sortie: Path) -> Path:
    """Concaténer sans ré-encoder. Rend le chemin du fichier produit."""
    outil = ffmpeg()
    if outil is None:
        raise FileNotFoundError("ffmpeg est introuvable : impossible de recoller les morceaux")
    sortie.parent.mkdir(parents=True, exist_ok=True)
    liste = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            for m in morceaux:
                # Le démultiplexeur concat lit des chemins entre apostrophes ;
                # celles du chemin lui-même doivent être échappées.
                f.write("file '%s'\n" % str(m.resolve()).replace("'", "'\\''"))
            liste = f.name
        subprocess.run([outil, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", liste, "-c", "copy", str(sortie)], check=True)
    finally:
        if liste:
            try:
                os.remove(liste)
            except OSError:
                pass
    return sortie

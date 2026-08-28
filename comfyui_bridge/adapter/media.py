"""Single source for artifact facts: media kind by extension, and artifact URL.

Both backends produce artifacts; before this module each had its own extension
table and its own URL construction, and they had already drifted (audio known to
one, ignored by the other). One definition, imported by both.
"""

from __future__ import annotations

from pathlib import Path

# Extension (en minuscules, sans point) -> Artifact.kind
KIND_BY_EXT: dict[str, str] = {
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
    "mp4": "video", "webm": "video", "gif": "video", "mkv": "video",
    "flac": "audio", "wav": "audio", "mp3": "audio", "ogg": "audio",
    # Un workflow ne produit pas que du média : certains MESURENT et rendent des
    # nombres, une légende, une liste de régions. Ignorer ces fichiers livrait
    # l'illustration d'une analyse sans jamais livrer son résultat.
    "txt": "text", "json": "text", "csv": "text", "md": "text", "yaml": "text",
}

MEDIA_EXT = frozenset("." + e for e, k in KIND_BY_EXT.items() if k != "text")
DATA_EXT = frozenset("." + e for e, k in KIND_BY_EXT.items() if k == "text")
# Tout ce qu'un run peut livrer. Les deux backends s'y réfèrent : leurs tables
# d'extensions avaient déjà divergé une fois, l'un connaissant l'audio et
# l'autre pas.
DELIVERABLE_EXT = MEDIA_EXT | DATA_EXT

# Les clés sous lesquelles ComfyUI rapporte ce qu'un nœud a produit, dans son
# historique. `files` est celle des sorties NON média — elle manquait, et un
# graphe d'analyse ne livrait donc jamais ses nombres.
OUTPUT_KEYS = ("images", "gifs", "videos", "audio", "files")

# Ce que ce service écrit lui-même dans le dossier de sortie pour travailler :
# un backend qui ramasse « tout fichier nouveau » se livrerait ses propres
# brouillons comme s'ils étaient le résultat demandé.
# Suffixe du fichier d'origine, écrit à côté des livrables qui ne savent pas
# embarquer la leur.
SIDECAR_SUFFIX = ".origine.json"


def is_working_file(path: Path | str) -> bool:
    nom = Path(path).name
    return (nom.startswith("_workflow_") or nom.endswith(".manifest.json")
            # L'origine écrite à côté d'un livrable qui ne sait pas la porter
            # accompagne ce livrable : elle n'en est pas un elle-même.
            or nom.endswith(SIDECAR_SUFFIX))


def media_kind(path: Path | str) -> str:
    return KIND_BY_EXT.get(Path(path).suffix.lstrip(".").lower(), "image")


def artifact_url(out_dir: Path, path: Path) -> str:
    """URL under the /artifacts mount that serves ``out_dir``."""
    rel = Path(path).resolve().relative_to(Path(out_dir).resolve()).as_posix()
    return f"/artifacts/{rel}"

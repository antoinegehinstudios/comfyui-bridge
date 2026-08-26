"""Single source for artifact facts: media kind by extension, and artifact URL.

Both backends produce artifacts; before this module each had its own extension
table and its own URL construction, and they had already drifted (audio known to
one, ignored by the other). One definition, imported by both.
"""

from __future__ import annotations

from pathlib import Path

# Extension (lowercase, no dot) -> Artifact.kind
KIND_BY_EXT: dict[str, str] = {
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
    "mp4": "video", "webm": "video", "gif": "video", "mkv": "video",
    "flac": "audio", "wav": "audio", "mp3": "audio", "ogg": "audio",
}

MEDIA_EXT = frozenset("." + e for e in KIND_BY_EXT)


def media_kind(path: Path | str) -> str:
    return KIND_BY_EXT.get(Path(path).suffix.lstrip(".").lower(), "image")


def artifact_url(out_dir: Path, path: Path) -> str:
    """URL under the /artifacts mount that serves ``out_dir``."""
    rel = Path(path).resolve().relative_to(Path(out_dir).resolve()).as_posix()
    return f"/artifacts/{rel}"

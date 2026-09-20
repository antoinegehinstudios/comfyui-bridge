"""Configuration fingerprint — a truthful label for what a run PINS.

Only the values the caller actually pinned appear. When nothing is pinned the
workflow runs on its own settings, and the label says exactly that instead of
inventing zeros. Deterministic bookkeeping, NOT a verdict: nothing is accepted
or refused on this value alone. Hermes uses it as the key for "the same
configuration", so two runs that pin nothing are correctly the same key.
"""

from __future__ import annotations

import re
from typing import Any

WORKFLOW_DEFAULT = "workflow-default"

_EMPREINTE = re.compile(r"^(?:(\d+)x(\d+)|w(\d+)|h(\d+))?(?:x(\d+))?(?:-?([\d.]+)s)?$")


def config_lue(empreinte: str) -> dict[str, Any]:
    """Ce qu'une empreinte ÉPINGLAIT, relu : largeur, hauteur, lot, durée —
    ceux qui y sont. La mémoire d'une chaîne ne garde que cette étiquette de
    ses livraisons ; pour comparer une demande neuve à ce qui a été mesuré à
    d'autres durées, il faut la relire. Une étiquette qui ne se relit pas
    (« workflow-default », une forme inconnue) rend un dict vide."""
    m = _EMPREINTE.match(str(empreinte or ""))
    if not m or not any(m.groups()):
        return {}
    w, h, seul_w, seul_h, lot, duree = m.groups()
    lu: dict[str, Any] = {}
    if w and h:
        lu["width"], lu["height"] = int(w), int(h)
    elif seul_w:
        lu["width"] = int(seul_w)
    elif seul_h:
        lu["height"] = int(seul_h)
    if lot:
        lu["latent_batch"] = int(lot)
    if duree:
        lu["duration_s"] = float(duree)
    return lu


def config_fingerprint(params: dict[str, Any]) -> str:
    width, height = params.get("width"), params.get("height")
    batch = params.get("latent_batch")
    bits: list[str] = []
    if width is not None and height is not None:
        bits.append(f"{int(width)}x{int(height)}")
    elif width is not None:
        bits.append(f"w{int(width)}")
    elif height is not None:
        bits.append(f"h{int(height)}")
    if batch is not None:
        bits.append(f"x{max(1, int(batch))}")
    # A duration pinned by the caller is part of WHAT was asked: without it two
    # audio runs of 6 s and 30 s wore the same label, and the memory of one
    # answered for the other.
    # …but only when the batch does not already carry it: a video pins its
    # frame count (which comes from the duration), whereas a batch of 1 says
    # nothing about how much was asked for.
    duration = params.get("duration_s")
    if duration is not None and (batch is None or int(batch) <= 1):
        # Separated: "x16s" read as one number and told nobody anything.
        bits.append(f"-{duration:g}s" if bits else f"{duration:g}s")
    return "".join(bits) or WORKFLOW_DEFAULT

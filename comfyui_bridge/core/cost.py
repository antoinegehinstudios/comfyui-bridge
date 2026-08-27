"""Configuration fingerprint — a truthful label for what a run PINS.

Only the values the caller actually pinned appear. When nothing is pinned the
workflow runs on its own settings, and the label says exactly that instead of
inventing zeros. Deterministic bookkeeping, NOT a verdict: nothing is accepted
or refused on this value alone. Hermes uses it as the key for "the same
configuration", so two runs that pin nothing are correctly the same key.
"""

from __future__ import annotations

from typing import Any

WORKFLOW_DEFAULT = "workflow-default"


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

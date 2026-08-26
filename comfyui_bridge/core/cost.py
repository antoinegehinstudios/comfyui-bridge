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
    return "".join(bits) or WORKFLOW_DEFAULT

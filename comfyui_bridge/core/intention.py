"""Domain vocabulary: what the *caller* wants, expressed without any ComfyUI term.

A ``RenderIntent`` is a declarative wish. It names the *workflow* to run (a
logical name resolved by the reconciliation file) and, optionally, parameter
overrides. Fields left ``None`` fall back to the workflow's declared defaults —
so calling ``z-image-turbo`` uses its 8 steps / cfg 1 without the caller
repeating them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class ConstraintOp(str, Enum):
    EQ = "eq"      # force the value
    LTE = "lte"    # clamp above (value is a max)
    GTE = "gte"    # clamp below (value is a min)
    IN = "in"      # value is an allow-list; snap to first member if outside


@dataclass(frozen=True)
class Constraint:
    key: str
    op: ConstraintOp
    value: object


@dataclass(frozen=True)
class RenderIntent:
    prompt: str
    workflow: str | None = None          # named entry in the reconciliation file
    kind: MediaKind | None = None        # None -> the workflow's declared kind
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    duration_s: float | None = None
    seed: int | None = None
    image: str | None = None      # input media, by the name ComfyUI knows
    video: str | None = None      # idem, for a workflow that starts from a clip
    steps: int | None = None
    cfg: float | None = None
    batch: int | None = None
    # Direct overrides on the workflow's OWN inputs, keyed "node.input".
    # The neutral params above are conveniences; this is the full surface the
    # workflow declares (discovered from ComfyUI's node schemas).
    inputs: dict[str, object] = field(default_factory=dict)
    constraints: tuple[Constraint, ...] = field(default_factory=tuple)

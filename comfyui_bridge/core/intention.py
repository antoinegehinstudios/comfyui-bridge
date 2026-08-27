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
    # How the caller names its own output. A larger flow that drives this
    # service needs to recognise ITS files among the others — by name, since a
    # job id lives only as long as the service process.
    label: str | None = None
    inputs: dict[str, object] = field(default_factory=dict)
    constraints: tuple[Constraint, ...] = field(default_factory=tuple)


# The ONE correspondence between a resolved plan parameter and the intent field
# that drives it. A workflow's analysis speaks in plan parameters; a caller
# speaks in intent fields. Announcing one and accepting the other silently
# dropped what an integrator sent: `latent_batch` was advertised, only `batch`
# was read, and nothing said so.
INTENT_FIELD_OF: dict[str, str] = {"latent_batch": "batch", "filename_prefix": "label"}


def intent_field_of(param: str) -> str:
    return INTENT_FIELD_OF.get(param, param)


def intent_fields(params) -> list[str]:
    """The intent fields that actually drive these plan parameters.

    This is the input contract a caller programs against — the names to put in
    the body of a render request, nothing else.
    """
    fields = {intent_field_of(p) for p in params}
    return sorted(f for f in fields if f in RenderIntent.__dataclass_fields__)

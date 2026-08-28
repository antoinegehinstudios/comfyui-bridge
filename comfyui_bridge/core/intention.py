"""Domain vocabulary: what the *caller* wants, expressed without any ComfyUI term.

A ``RenderIntent`` is a declarative wish. It names the *workflow* to run (a
logical name resolved by the reconciliation file) and, optionally, parameter
overrides. Fields left ``None`` fall back to the workflow's declared defaults —
so calling ``z-image-turbo`` uses its 8 steps / cfg 1 without the caller
repeating them.
"""

from __future__ import annotations

import re
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


# Les catégories de média qu'un appelant peut joindre. Un workflow en expose
# souvent PLUSIEURS de la même catégorie — une première et une dernière image,
# quatre vues à assembler, une voix et une musique. La première garde le nom nu
# ("image"), les suivantes sont numérotées ("image_2", "image_3"…), dans l'ordre
# des nœuds : ainsi le nom est stable d'un appel à l'autre, et le nom NU reste
# celui qu'il a toujours été pour les workflows à une seule entrée.
MEDIA_CATEGORIES: tuple[str, ...] = ("image", "video", "audio", "model3d")

_MEDIA_PARAM = re.compile(r"^(" + "|".join(MEDIA_CATEGORIES) + r")(?:_(\d+))?$")


def media_param(category: str, rank: int) -> str:
    """Le nom d'une entrée média : nu pour la première de sa catégorie."""
    return category if rank <= 1 else f"{category}_{rank}"


def media_category(param: str) -> str | None:
    """La catégorie d'un paramètre média, ou None s'il n'en est pas un."""
    m = _MEDIA_PARAM.match(param or "")
    return m.group(1) if m else None


def is_media_param(param: str) -> bool:
    return media_category(param) is not None


@dataclass(frozen=True)
class Constraint:
    key: str
    op: ConstraintOp
    value: object


@dataclass(frozen=True)
class RenderIntent:
    prompt: str | None = None
    workflow: str | None = None          # named entry in the reconciliation file
    kind: MediaKind | None = None        # None -> the workflow's declared kind
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    duration_s: float | None = None
    seed: int | None = None
    # Les pièces jointes, par le nom que ComfyUI leur donne : {paramètre média ->
    # nom}. UNE seule table, parce qu'un workflow n'a pas un nombre fixe
    # d'entrées média — deux champs figés ("image", "vidéo") laissaient la
    # dernière image d'un flf2v et toute entrée audio sans aucun moyen d'être
    # remplies. Les noms sont ceux que le workflow annonce (voir `accepts`).
    media: dict[str, str] = field(default_factory=dict)
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
    the body of a render request, nothing else. Un paramètre média en fait
    partie quel que soit son rang : `image_2` se remplit comme `image`, sans
    quoi la découverte annoncerait une entrée que la requête refuserait.
    """
    fields = {intent_field_of(p) for p in params}
    return sorted(f for f in fields
                  if f in RenderIntent.__dataclass_fields__ or is_media_param(f))

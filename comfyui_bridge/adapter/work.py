"""How much work a run actually represents.

An estimate keyed on "same workflow" alone cannot be right: 704×448 for 2 s and
1280×720 for 5 s are not the same job. What drives compute is, to first order,
the number of pixels times the number of frames times the number of steps.

The values used are the EFFECTIVE ones: what the caller set, or else what the
workflow itself carries — read from the injected graph, never assumed. Hermes
then fits the seconds-per-unit from measured runs; the shape is ours, the number
is always experience.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_STEPS = 1  # unknown step count must not multiply the estimate


def effective_values(catalog, plan) -> dict[str, Any]:
    """Width, height, frames and steps as the engine will really see them."""
    from .injector import apply_overrides, inject

    spec = catalog.get_spec(plan.workflow)
    graph = apply_overrides(
        inject(catalog.load_template(spec), spec.bindings, plan.params), plan.overrides)

    def at(param: str) -> Any:
        b = spec.bindings.get(param)
        if not b:
            return None
        value = ((graph.get(b.node) or {}).get("inputs") or {}).get(b.input)
        return value if isinstance(value, (int, float)) else None

    width, height = at("width"), at("height")
    steps = at("steps")
    frames = at("latent_batch")
    if frames is None:
        duration, fps = at("duration_s"), at("fps")
        if duration and fps:
            frames = round(duration * fps)
    return {
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "frames": int(frames) if frames else None,
        "steps": int(steps) if steps else None,
    }


def work_units(values: dict[str, Any]) -> float | None:
    """Pixels × frames × steps, in millions. None when nothing is known."""
    width, height = values.get("width"), values.get("height")
    frames = values.get("frames") or 1
    steps = values.get("steps") or _DEFAULT_STEPS
    if not width or not height:
        return None
    return (float(width) * float(height) * float(frames) * float(steps)) / 1_000_000.0

"""How much work a run actually represents.

An estimate keyed on "same workflow" alone cannot be right: 704×448 for 2 s and
1280×720 for 5 s are not the same job. What drives compute is, to first order,
the number of pixels times the number of frames times the number of SAMPLING
STEPS actually performed.

Everything is read from the INJECTED graph — what the engine will really be
given, the caller's values and the workflow's own alike. Reading it through our
semantic bindings alone was not enough: a real LTX workflow carries no ``steps``
input at all (its two passes are driven by explicit sigma lists), so changing
them moved the run from 3 to 11 steps while the estimate never budged.

Hermes then fits seconds-per-unit on measured runs; the shape is ours, the
number is always experience. The shape has a VERSION: change how work is
counted and old measurements are on another scale, so they must not be mixed
into the same fit.
"""

from __future__ import annotations

from typing import Any

# 1: pixels x frames x (bound `steps` only — sigma-driven passes counted as 1)
# 2: pixels x frames x steps actually performed, summed over every sampler
WORK_MODEL = 2

_DEFAULT_STEPS = 1  # an unreadable step count must not multiply the estimate


def _is_link(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int))


def _inputs(node: Any) -> dict[str, Any]:
    return (node.get("inputs") if isinstance(node, dict) else None) or {}


def _literal_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sigma_count(text: Any) -> int | None:
    """Steps behind an explicit sigma list: N sigmas describe N-1 steps."""
    if not isinstance(text, str):
        return None
    values = [p for p in text.replace(";", ",").split(",") if p.strip()]
    return len(values) - 1 if len(values) >= 2 else None


def _through_primitive(graph: dict[str, Any], value: Any, depth: int = 4) -> Any:
    """Follow a link only when it lands on a node holding ONE plain value.

    A workflow usually feeds its latent from a Primitive node rather than typing
    the number in place. Following that is reading, not guessing — so it stops
    there: a switch or an expression is not resolved, and the value stays
    unknown rather than invented.
    """
    if not _is_link(value):
        return value
    source = graph.get(str(value[0])) or {}
    ins = _inputs(source)
    # A switch names its own branches: taking the one its literal selector
    # points at is reading the graph, not choosing for the author.
    if "switch" in ins and "on_true" in ins and "on_false" in ins and depth > 0:
        chosen = ins["on_true"] if _through_primitive(graph, ins["switch"], depth - 1) else ins["on_false"]
        return _through_primitive(graph, chosen, depth - 1)
    literals = [v for v in ins.values() if not _is_link(v)]
    if len(ins) == 1 and len(literals) == 1:
        return literals[0]
    return None


def _steps_of_sampler(graph: dict[str, Any], node: dict[str, Any]) -> int | None:
    """How many steps THIS sampler performs, read from the graph.

    Three shapes met in real workflows: the step count on the sampler itself,
    a scheduler upstream carrying it, or an explicit list of sigmas.
    """
    ins = _inputs(node)
    steps = _literal_int(_through_primitive(graph, ins.get("steps")))
    if steps is None and _is_link(ins.get("sigmas")):
        source = graph.get(str(ins["sigmas"][0])) or {}
        src_in = _inputs(source)
        # A scheduler usually gets its step count from elsewhere too.
        steps = (_sigma_count(src_in.get("sigmas"))
                 or _literal_int(_through_primitive(graph, src_in.get("steps"))))
    if steps is None:
        return None
    # A pass that only covers part of the schedule does only that part: Wan 2.2
    # splits one 20-step schedule across two samplers, which would count double.
    start = _literal_int(ins.get("start_at_step"))
    end = _literal_int(ins.get("end_at_step"))
    if start is not None or end is not None:
        first = max(0, start or 0)
        last = min(steps, end if end is not None else steps)
        return max(0, last - first)
    return steps


def _total_steps(graph: dict[str, Any]) -> int | None:
    """Steps performed by the whole graph — every sampling pass added up.

    A "Select" node names an algorithm and a "Loader" loads a model: neither
    samples anything, so neither is counted.
    """
    total, seen_any = 0, False
    for node in graph.values():
        class_type = node.get("class_type", "") if isinstance(node, dict) else ""
        if "Sampler" not in class_type or "Select" in class_type or "Loader" in class_type:
            continue
        steps = _steps_of_sampler(graph, node)
        if steps is not None:
            total += steps
            seen_any = True
    return total if seen_any and total > 0 else None


def _latent_size(graph: dict[str, Any]) -> tuple[int | None, int | None]:
    """Width and height of the latent the graph builds, when it states them."""
    for node in graph.values():
        ins = _inputs(node)
        if "width" not in ins or "height" not in ins:
            continue
        w = _literal_int(_through_primitive(graph, ins["width"]))
        h = _literal_int(_through_primitive(graph, ins["height"]))
        if w and h:
            return w, h
    return None, None


def _frame_count(graph: dict[str, Any]) -> int | None:
    for node in graph.values():
        ins = _inputs(node)
        if "length" not in ins:
            continue
        frames = _literal_int(_through_primitive(graph, ins["length"]))
        if frames:
            return frames
    return None


def effective_values(catalog, plan) -> dict[str, Any]:
    """Width, height, frames and steps as the engine will really see them."""
    from .injector import apply_overrides, inject

    spec = catalog.get_spec(plan.workflow)
    graph = apply_overrides(
        inject(catalog.load_template(spec), spec.bindings, plan.params), plan.overrides)

    def at(param: str) -> Any:
        """The value of a bound parameter, read back from the injected graph."""
        b = spec.bindings.get(param)
        if not b:
            return None
        return _literal_int(((graph.get(b.node) or {}).get("inputs") or {}).get(b.input))

    # The binding says it best when there is one; the graph answers otherwise.
    width, height = at("width"), at("height")
    if width is None or height is None:
        guessed_w, guessed_h = _latent_size(graph)
        width, height = width or guessed_w, height or guessed_h

    frames = at("latent_batch") or _frame_count(graph)
    if frames is None:
        duration, fps = at("duration_s"), at("fps")
        if duration and fps:
            frames = round(duration * fps)

    return {
        "width": int(width) if width else None,
        "height": int(height) if height else None,
        "frames": int(frames) if frames else None,
        "steps": _total_steps(graph),
    }


def work_units(values: dict[str, Any]) -> float | None:
    """Megapixels × frames × steps — the load of one run.

    The recognised shape for diffusion: time grows linearly with the number of
    denoising steps and with the surface generated, times the number of frames.
    Hermes fits the seconds per unit; only the SHAPE is ours.

    A factor that cannot be read counts as 1 — NEUTRAL, not annulling. It is
    fixed in the graph, so it is the same for every run of that workflow and the
    fitted coefficient absorbs it. Returning nothing instead made the estimate
    deaf to every parameter: a run of 24 frames and one of 96 got the same
    answer (measured: 134 s and 411 s).
    """
    width, height = values.get("width"), values.get("height")
    frames = values.get("frames")
    steps = values.get("steps")
    if not any((width and height, frames, steps)):
        return None                    # nothing readable at all: say nothing
    surface = (float(width) * float(height) / 1_000_000.0) if (width and height) else 1.0
    return surface * float(frames or 1) * float(steps or _DEFAULT_STEPS)

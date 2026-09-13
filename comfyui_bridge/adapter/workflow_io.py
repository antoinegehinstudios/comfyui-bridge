"""What a workflow expects and what it delivers — read from ComfyUI itself.

ComfyUI declares, per node class (``GET /object_info``):
  * every input with its TYPE (STRING / INT / COMBO / IMAGE …) and tooltip,
  * ``output_node: true`` for the nodes that actually deliver a file.

So the I/O contract of a workflow is not something to guess from parameter
names: it is derived from the graph (which inputs are literal, hence settable)
crossed with ComfyUI's own schema. Wiring adds the one thing the schema cannot
say: whether a conditioning text is the positive or the negative side.
"""

from __future__ import annotations

from typing import Any

from . import autobind


def _spec_type(spec: Any) -> str:
    """ComfyUI input spec -> declared type. Handles both enum shapes."""
    if isinstance(spec, list) and spec:
        head = spec[0]
        if isinstance(head, str):
            return head                      # "STRING", "INT", "COMBO", …
        if isinstance(head, list):
            return "COMBO"                   # legacy: the list IS the options
    return "UNKNOWN"


def _spec_meta(spec: Any) -> dict[str, Any]:
    if isinstance(spec, list):
        for extra in spec[1:]:
            if isinstance(extra, dict):
                return extra
    return {}


# Au-delà, une liste de choix n'est plus un menu : elle est coupée, et l'entrée
# le DIT (« options_total ») — une coupe muette faisait disparaître des styles
# d'un catalogue de 59 entrées derrière un plafond de 50 posé sans le dire.
OPTIONS_MAX = 500


def _option_values(options: list[Any], limit: int = OPTIONS_MAX) -> list[Any]:
    """The values a combo really accepts.

    A plain enum lists strings. ComfyUI's dynamic combos list objects whose
    ``key`` is what the graph stores (the rest describes the inputs that option
    reveals). Relaying the object made every choice read "[object Object]" in
    the form; the key is the one thing a caller can actually send.
    """
    values: list[Any] = []
    for opt in options[:limit]:
        if isinstance(opt, dict):
            for k in ("key", "value", "content", "name"):
                if isinstance(opt.get(k), (str, int, float, bool)):
                    values.append(opt[k])
                    break
        else:
            values.append(opt)
    return values


# Beyond this, a declared bound is the machine's limit, not the author's: a
# Primitive node says min=-2^63, which bounds nothing and only clutters a form.
_NO_REAL_BOUND = 2 ** 53


def _real_bound(value: Any) -> bool:
    return isinstance(value, (int, float)) and abs(value) < _NO_REAL_BOUND


def _type_sans_noeud(valeur: Any) -> str:
    """Le type d'un paramètre que le moteur ne déclare pas — lu sur ce qu'il vaut.

    Un paramètre PILOTE ne vise aucun nœud : ComfyUI n'en sait rien, il n'y a
    donc personne à qui demander son type. Sa valeur d'exemple le dit, et sans
    type un formulaire ne sait pas quel champ poser.
    """
    if isinstance(valeur, bool):
        return "BOOLEAN"
    if isinstance(valeur, int):
        return "INT"
    if isinstance(valeur, float):
        return "FLOAT"
    return "STRING"


def intent_inputs(io_inputs: list[dict[str, Any]], bindings, kind: str,
                  defaults: dict[str, Any] | None = None,
                  pilotes: Any = (), exemple: dict[str, Any] | None = None
                  ) -> list[dict[str, Any]]:
    """The input contract, field by field: what to send and within which bounds.

    The join between "the field a caller sends" and "what ComfyUI declares for
    the node it drives" is done ONCE here. Left to each client, it was done in
    the browser only — so anything driving this service without the console had
    to redo it, or go without bounds.

    Trois sources, et non une seule liaison : les champs qu'un NŒUD porte, les
    paramètres qui PILOTENT un montage (aucun nœud ne les reçoit, ils décident
    du nombre de blocs) et ceux que la passerelle DÉRIVE (la durée, convertie en
    nombre d'images). Ne rendre que la première laissait un montage piloté par
    sa durée sans le moindre champ de durée.
    """
    from ..core.intention import intent_field_of, intent_fields
    from ..core.orchestrator import derivable_params

    declares = dict(defaults or {})
    exemples = dict(exemple or {})
    declared = {(i["node"], i["input"]): i for i in io_inputs}
    out: list[dict[str, Any]] = []
    vus: set[str] = set()
    for param in sorted(bindings):
        field = intent_field_of(param)
        if field not in intent_fields(bindings):
            continue                              # service-owned, e.g. filename_prefix
        binding = bindings[param]
        spec = declared.get((binding.node, binding.input), {})
        entry = {"field": field, "param": param, "node": binding.node,
                 "input": binding.input, "type": spec.get("type"),
                 "value": spec.get("value"), "derived": False}
        for key in ("min", "max", "step", "options", "tooltip", "label"):
            value = spec.get(key)
            if value is None:
                continue
            if key in ("min", "max") and not _real_bound(value):
                continue                          # a limit that limits nothing
            entry[key] = value
        vus.add(field)
        out.append(entry)
    for param in sorted(set(pilotes or ()) - set(bindings)):
        field = intent_field_of(param)
        if field in vus or field not in intent_fields([param]):
            continue        # une constante du montage n'est pas un champ d'appel
        valeur = exemples.get(param, declares.get(field, declares.get(param)))
        out.append({"field": field, "param": param, "node": None, "input": None,
                    "type": _type_sans_noeud(valeur), "value": valeur, "derived": False})
        vus.add(field)
    for field in derivable_params(kind, set(bindings) | set(pilotes or ()), declares):
        if field in vus:
            continue
        # Converted rather than carried by a node: no node, no bounds, said so.
        # La valeur, elle, est celle que le catalogue DÉCLARE : un formulaire
        # ouvert sur un champ vide ne dit pas ce que ce montage produit par
        # défaut, et le demandeur devait deviner sa propre durée.
        out.append({"field": field, "param": None, "node": None, "input": None,
                    "type": "FLOAT", "value": declares.get(field), "derived": True})
    return out


def describe_io(graph: dict[str, Any], object_info: dict[str, Any],
                titles: dict[str, str] | None = None) -> dict[str, Any]:
    """The workflow's settable inputs and its delivering outputs.

    ``titles`` carries the author's own node names (lost by the API export):
    with them an entry reads "Duration" instead of "PrimitiveInt · value".
    """
    from .labels import label_for
    negatives = autobind._negative_text_nodes(graph)
    positives: set[str] = set()
    for node in graph.values():
        for key, val in (node.get("inputs") or {}).items():
            if autobind._is_link(val) and key in ("positive", "prompt"):
                positives.add(str(val[0]))

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    for nid, node in graph.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        schema = object_info.get(ct) or {}
        declared = {}
        for section in ("required", "optional"):
            declared.update((schema.get("input") or {}).get(section) or {})

        if schema.get("output_node"):
            outputs.append({
                "node": nid,
                "class_type": ct,
                "display_name": schema.get("display_name") or ct,
            })

        for key, val in (node.get("inputs") or {}).items():
            if autobind._is_link(val):
                continue                      # produced by another node: not settable
            spec = declared.get(key)
            label = label_for(nid, titles or {})
            entry = {
                "node": nid,
                "class_type": ct,
                "label": label,          # the author's name, when they gave one
                "input": key,
                "type": _spec_type(spec) if spec is not None else "UNKNOWN",
                "value": val,
            }
            meta = _spec_meta(spec)
            if meta.get("tooltip"):
                entry["tooltip"] = meta["tooltip"]
            # Bounds and defaults are declared by ComfyUI: relay them so a form
            # can be built from the workflow instead of from our guesses.
            for key in ("min", "max", "step", "default", "multiline", "round"):
                if key in meta:
                    entry[key] = meta[key]
            brutes = None
            if isinstance(meta.get("options"), list):
                brutes = meta["options"]
            elif isinstance(spec, list) and isinstance(spec[0], list):
                brutes = spec[0]
            if brutes is not None:
                entry["options"] = _option_values(brutes)
                if len(brutes) > OPTIONS_MAX:
                    entry["options_total"] = len(brutes)   # coupée, et dit
            if nid in negatives:
                entry["role"] = "negative"
            elif nid in positives:
                entry["role"] = "positive"
            inputs.append(entry)

    inputs.sort(key=lambda e: (e["class_type"], e["node"], e["input"]))
    return {"inputs": inputs, "outputs": outputs}

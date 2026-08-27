"""Inject resolved params onto a ComfyUI workflow graph.

Rules:
- A param with no binding is ignored (e.g. ``fps`` on a still workflow).
- A binding whose param is absent is skipped.
- A binding that points at a node/input the workflow does not have is a
  configuration error — raised loudly, never silently dropped.
"""

from __future__ import annotations

import copy
from typing import Any

from ..core.errors import UnknownWorkflowInputError, WorkflowMappingError
from .mapping import Binding


# Ce paramètre nomme la sortie du RUN, pas celle d'un nœud : un workflow qui
# délivre une image ET une mesure a deux nœuds de sauvegarde, et n'en piloter
# qu'un laissait la moitié des livrables hors du nom demandé — donc introuvable
# pour l'appelant qui les cherche.
_APPLIQUE_A_TOUS = frozenset({"filename_prefix"})


def inject(
    workflow: dict[str, Any],
    bindings: dict[str, Binding],
    params: dict[str, Any],
) -> dict[str, Any]:
    graph = copy.deepcopy(workflow)
    for key in _APPLIQUE_A_TOUS & set(params):
        for node in graph.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if isinstance(inputs, dict) and isinstance(inputs.get(key), str):
                inputs[key] = params[key]
    for key, binding in bindings.items():
        if key not in params or key in _APPLIQUE_A_TOUS:
            continue
        node = graph.get(binding.node)
        if node is None:
            raise WorkflowMappingError(
                f"binding {key!r} points at node {binding.node!r} absent from workflow",
                binding=key, node=binding.node,
            )
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or binding.input not in inputs:
            raise WorkflowMappingError(
                f"binding {key!r} points at input {binding.input!r} "
                f"absent from node {binding.node!r}",
                binding=key, node=binding.node, input=binding.input,
            )
        inputs[binding.input] = params[key]
    return graph


def apply_overrides(graph: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Set the workflow's OWN inputs, addressed as "node.input".

    Same contract as bindings: a target the workflow does not have is an error,
    never a silent no-op — the caller must know their value went nowhere.
    """
    for key, value in (overrides or {}).items():
        node_id, _, input_name = str(key).rpartition(".")
        node = graph.get(node_id)
        if node is None or not isinstance(node.get("inputs"), dict):
            raise UnknownWorkflowInputError(
                f"override {key!r}: nœud {node_id!r} absent du workflow", override=key)
        if input_name not in node["inputs"]:
            raise UnknownWorkflowInputError(
                f"override {key!r}: entrée {input_name!r} absente du nœud {node_id!r}", override=key)
        node["inputs"][input_name] = value
    return graph

"""Known problem kinds — the vocabulary of what actually went wrong.

Classification is derived from the REAL failure text (engine output, exception),
never invented. ``UNKNOWN`` is a valid, honest answer: an unclassified failure is
recorded as-is rather than forced into a category.
"""

from __future__ import annotations

OOM = "oom"                       # GPU ran out of memory
MISSING_MODEL = "missing-model"   # a model file the workflow needs is absent
MISSING_NODE = "missing-node"     # a node type the workflow needs is not installed
BLOCKED_BY_OS = "blocked-by-os"   # OS security blocked a component (e.g. Smart App Control)
UNREACHABLE = "unreachable"       # engine not running / not answering
TIMEOUT = "timeout"               # engine took longer than allowed
WORKFLOW_ERROR = "workflow-error" # engine rejected/failed the graph
LOST = "lost"                     # the engine no longer knows the run (cancelled, or it restarted)
UNKNOWN = "unknown"

# Ordered: first match wins. Patterns are lowercase substrings seen in real output.
_SIGNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (OOM, ("out of memory", "cuda oom", "outofmemoryerror", "failed to allocate")),
    (BLOCKED_BY_OS, ("code integrity", "smart app control", "signing level",
                     "violated code integrity", "0xc0000428")),
    # ComfyUI's own validation wording (POST /prompt -> 400 node_errors).
    (MISSING_MODEL, ("value_not_in_list", "value not in list", "' not in [",
                     "no such file or directory", "checkpoint not found")),
    (MISSING_NODE, ("node type not found", "unknown node type", "does not exist in the node",
                    "invalid_prompt")),
    # Cancelling from the console lands here too: the run simply left the
    # engine. Calling that a workflow failure filled Hermes with problems the
    # user had caused on purpose.
    (LOST, ("ne connaît plus ce run", "ne connait plus ce run")),
    (UNREACHABLE, ("unreachable", "connection refused", "actively refused",
                   "serveur lancé", "failed to establish")),
    (TIMEOUT, ("timed out", "timeout")),
    (WORKFLOW_ERROR, ("workflow error", "rejected the graph", "prompt_outputs_failed",
                      "execution_error")),
)


def classify(text: str) -> str:
    """Map a real failure message to a problem kind. UNKNOWN when unsure."""
    low = (text or "").lower()
    for kind, needles in _SIGNS:
        if any(n in low for n in needles):
            return kind
    return UNKNOWN


# Kinds that a repeat run would hit again unless the environment changed. A
# recorded problem of these kinds is a reason to warn/refuse up front.
BLOCKING = frozenset({OOM, MISSING_MODEL, MISSING_NODE, BLOCKED_BY_OS})

# Of those, the ones that owe nothing to the size of the job: an absent model
# file stays absent at any resolution, whereas an out-of-memory depends on how
# much was asked for. Only the size-dependent ones may be retried by changing
# the configuration.
INDEPENDENT_OF_CONFIG = frozenset({MISSING_MODEL, MISSING_NODE, BLOCKED_BY_OS})

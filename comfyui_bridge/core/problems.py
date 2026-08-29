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


# Refuser d'avance n'a de sens que si RÉESSAYER COÛTE CHER. Un souvenir qui
# refuse interdit aussi le seul run qui prouverait la réparation : il ne doit
# donc barrer la route que lorsque l'essai se paie.
#
#   - une dépendance absente est refusée par le moteur AVANT tout calcul (il
#     valide le graphe et répond en quelques millisecondes) : la retenter ne
#     coûte rien, et l'interdire condamnait un workflow réparé à rester refusé
#     pour une cause disparue ;
#   - un dépassement mémoire ou un blocage du système coûtent des minutes, et
#     peuvent emporter le moteur : ceux-là valent un refus, avec une porte
#     explicite pour qui veut quand même essayer.
BLOCKING = frozenset({OOM, BLOCKED_BY_OS})

# Connus, mais sans frais à redécouvrir : on prévient, on ne refuse pas.
CHEAP_TO_RETRY = frozenset({MISSING_MODEL, MISSING_NODE})

# Ce dont la PASSERELLE elle-même peut être la cause, et non le moteur. Un échec
# non classé en fait partie : il peut venir de la façon dont elle ramasse le
# résultat. Mesuré : un maillage écrit sur le disque par le moteur, non ramassé
# parce que la clé `3d` n'était pas lue, a été retenu contre un workflow qui
# marchait. Quand la livraison change de mécanisme, ces souvenirs-là parlent
# d'un processus qui n'existe plus : ils doivent être revus, pas conservés.
MECHANISM_DEPENDENT = frozenset({UNKNOWN})

# Parmi les bloquants, ceux qui ne doivent rien à la taille du travail : un
# système qui bloque un composant le bloque à toute résolution, alors qu'un
# dépassement mémoire dépend de ce qui a été demandé.
INDEPENDENT_OF_CONFIG = frozenset({BLOCKED_BY_OS})

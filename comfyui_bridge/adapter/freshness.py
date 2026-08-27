"""Un extrait est-il encore fidèle à sa source dans ComfyUI ?

La question se pose à trois endroits — la vue de gestion, le catalogue qu'un
appelant consulte, et le moment du lancement — et doit recevoir partout la même
réponse. Elle vivait dans la seule vue de gestion : un run a donc exécuté
l'ancienne version d'un workflow modifié sans un mot, et rendu un .flac là où la
nouvelle sauve un .mp3.

Rien n'est deviné : on compare l'empreinte de la source à celle enregistrée lors
de l'extraction, et on regarde si la source porte des noms de nœuds que
l'analyse stockée n'a pas lus.
"""

from __future__ import annotations

import time
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_S = 5.0        # le catalogue est un chemin chaud : on ne re-lit pas à chaque appel


def source_state(comfyui, spec, now: float | None = None) -> dict[str, Any]:
    """``{"fresh": bool, "reason": str | None}`` pour un workflow extrait.

    Un workflow sans source (déclaré à la main) est toujours à jour : il n'a
    pas de source à surveiller.
    """
    if not spec.source:
        return {"fresh": True, "reason": None}

    key = f"{spec.name}|{spec.source}"
    stamp = time.monotonic() if now is None else now
    cached = _CACHE.get(key)
    if cached and stamp - cached[0] < _TTL_S:
        return cached[1]

    state = {"fresh": True, "reason": None}
    try:
        from .comfyui_client import source_hash
        from .labels import titles_from_ui_workflow
        saved = comfyui.get_saved_workflow(spec.source)
        if spec.source_hash and source_hash(saved) != spec.source_hash:
            state = {"fresh": False, "reason": "le workflow a changé dans ComfyUI"}
        elif titles_from_ui_workflow(saved) and not spec.titles:
            state = {"fresh": False,
                     "reason": "analyse incomplète — les noms de nœuds de l'auteur "
                               "n'ont pas été lus"}
    except Exception:
        # Moteur muet ou source disparue : on ne conclut pas à une péremption.
        return {"fresh": True, "reason": None}

    _CACHE[key] = (stamp, state)
    return state


def forget(spec_name: str | None = None) -> None:
    """Oublier ce qu'on sait — après une ré-extraction, la réponse a changé."""
    if spec_name is None:
        _CACHE.clear()
        return
    for key in [k for k in _CACHE if k.startswith(f"{spec_name}|")]:
        _CACHE.pop(key, None)

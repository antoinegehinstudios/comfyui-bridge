"""Le fichier d'origine écrit à côté d'un livrable.

Le moteur inscrit déjà son graphe dans un PNG, un MP4 ou un FLAC : le recopier
ferait une seconde vérité qui peut mentir, et ``provenance.read_embedded`` sait
la lire. Mais deux choses n'existent QUE de ce côté-ci du pont, et aucun format
de fichier ne les porte : le nom du workflow appelé, et ce que l'appelant avait
demandé. Le compagnon porte donc toujours ce complément — et n'ajoute le graphe
que si le fichier ne sait pas le garder (.txt, .csv, .webp).

Vit ici, et non dans un backend : un livrable s'accompagne de son origine quel
que soit le chemin par lequel il a été produit. Écrit dans le backend HTTP, le
mécanisme laissait le backend CLI produire des fichiers muets.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .media import SIDECAR_SUFFIX, media_kind
from .provenance import read_embedded


def write_companion(fichier: Path, plan: Any = None, graph: dict | None = None) -> Path | None:
    """Écrire ``<livrable>.origine.json``. Renvoie son chemin, ou None si échec.

    Un compagnon manquant ne doit jamais coûter le livrable : toute erreur est
    absorbée, le fichier produit reste livré.
    """
    try:
        compagnon: dict[str, Any] = {
            "fichier": fichier.name,
            # La nature se LIT sur le fichier produit. La prendre du plan, c'est
            # rapporter une intention : un run repris sans nature notée faisait
            # dire "image" à un .mp4. Le fichier, lui, ne se trompe pas.
            "kind": media_kind(fichier),
            "produit_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if plan is not None:
            compagnon["workflow"] = getattr(plan, "workflow", None)
            # Nommée : c'est l'empreinte sous laquelle l'expérience de ce run
            # est rangée, pas une résolution lisible ("x240" = 240 images).
            compagnon["empreinte_config"] = getattr(plan, "config", None)
            compagnon["demande"] = dict(getattr(plan, "params", {}) or {})
        if isinstance(graph, dict) and read_embedded(fichier) is None:
            compagnon["prompt"] = graph      # le fichier ne sait pas le porter
        cible = fichier.with_name(fichier.name + SIDECAR_SUFFIX)
        cible.write_text(json.dumps(compagnon, ensure_ascii=False, indent=1), encoding="utf-8")
        return cible
    except Exception:
        return None


class WithOrigin:
    """Tout backend, enveloppé : ce qu'il livre part avec son origine.

    Posé sur le PORT et non dans un backend. Écrit dans chaque adaptateur, le
    mécanisme se répétait deux fois et un troisième backend l'aurait oublié en
    silence — un livrable muet est indistinguable d'un livrable normal. Ici la
    règle tient en un seul endroit et vaut pour tout ce qui passe la frontière,
    quel que soit le chemin par lequel c'est arrivé.

    Le graphe vient de ``preview(plan)`` — l'API que les backends exposent déjà
    pour dire quel graphe ils exécuteraient. Il n'est écrit que si le fichier ne
    sait pas porter le sien : le graphe embarqué par le moteur reste la source
    de vérité, celui-ci n'est qu'un secours pour les formats muets.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:      # health, preview, load_of, queue…
        return getattr(self._inner, name)

    def _graph(self, plan: Any) -> dict | None:
        try:
            g = self._inner.preview(plan)
            return g if isinstance(g, dict) else None
        except Exception:
            return None

    def _sign(self, artifacts: Any, plan: Any) -> None:
        graphe = self._graph(plan)
        for art in artifacts or ():
            chemin = getattr(art, "path", None)
            if chemin:
                write_companion(Path(chemin), plan, graphe)

    def submit(self, plan: Any, *a: Any, **k: Any) -> Any:
        result = self._inner.submit(plan, *a, **k)
        self._sign(getattr(result, "artifacts", None), plan)
        return result

    def collect(self, prompt_id: str, plan: Any = None) -> Any:
        """Reprise d'un run retrouvé : il livre aussi, il signe donc aussi."""
        out = self._inner.collect(prompt_id, plan)
        if out:
            self._sign(out[0], plan)
        return out

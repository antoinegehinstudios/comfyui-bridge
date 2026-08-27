"""Ce qui est LIVRÉ avec le module, et ce qui appartient à la machine.

Le paquet embarque des ressources neutres (des workflows d'exemple, des profils
de moteur décrits sans chemin). Tout ce qui décrit UNE machine — où ComfyUI est
installé, quels workflows y ont été extraits — vit à côté, dans le dossier de
données, et n'est jamais versionné.

Un fichier ``<nom>.local.json`` placé là est fusionné par-dessus la ressource
livrée : ses entrées s'ajoutent, et remplacent celles de même nom. Le module
reste ainsi utilisable tel quel sur un poste neuf, sans rien éditer dans le
paquet — et le dépôt ne publie l'arborescence de personne.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def local_path(data_dir: Path, shipped: Path) -> Path:
    """Le fichier de surcharge qui correspond à une ressource livrée."""
    return Path(data_dir) / f"{Path(shipped).stem}.local.json"


def merge(shipped: dict[str, Any], overlay: dict[str, Any], section: str) -> dict[str, Any]:
    """Fusionne une surcharge locale dans une ressource livrée.

    Seule la section nommée est fusionnée entrée par entrée ; les autres clés de
    premier niveau (``default``, ``version``…) sont remplacées si la surcharge
    les donne. Rien n'est deviné : ce que la surcharge ne dit pas reste tel quel.
    """
    if not overlay:
        return shipped
    merged = dict(shipped)
    entries = dict(shipped.get(section) or {})
    entries.update(overlay.get(section) or {})
    merged[section] = entries
    for key, value in overlay.items():
        if key != section:
            merged[key] = value
    return merged


def read_overlay(data_dir: Path | None, shipped: Path) -> dict[str, Any]:
    """La surcharge locale, ou ``{}`` s'il n'y en a pas.

    Un fichier illisible est une erreur de l'utilisateur, pas une fatalité
    silencieuse : il remonte telle quelle.
    """
    if data_dir is None:
        return {}
    path = local_path(data_dir, shipped)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

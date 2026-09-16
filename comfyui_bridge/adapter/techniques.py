"""Où vivent les TECHNIQUES, et comment on les lit.

Une chaîne est un PLAN : ses étapes nomment des rôles (« deroulement »,
« conclusion »), jamais un graphe de peinture. Une technique dit quel graphe
tient chaque rôle, ce qu'il reçoit, quels réglages elle ajoute et quels
contrôles elle porte.

Antoine, 2026-09-16 : « la mention de brume ne doit pas être tenue par le
workflow de la passerelle : cela veut dire qu'il porte une dépendance à la
brume et devra se faire doublon pour faire autrement ». Avant ce jour, deux
chaînes jumelles se recopiaient à onze étapes près deux — une technique de plus
était une chaîne de plus. C'est maintenant un FICHIER de plus, et rien d'autre :
ni la chaîne, ni le code ne nomment une technique (la règle `flux-hors-du-code`
du socle tient ce point).

Les fichiers vivent à côté des chaînes, dans le dossier de données de la machine
(`_data/techniques/*.json`) ; le paquet en garde une copie de référence
(`resources/techniques-exemples/`), comme pour les chaînes — sans elle, une
installation neuve repartirait sur l'ancienne version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.chaine import Technique, lire_technique
from ..core.errors import WorkflowMappingError

# Le nom du dossier, à côté de `chaines/` et `workflows/` dans les données de la
# machine. Pas une variable d'environnement de plus : une technique n'est pas un
# réglage de poste, c'est une donnée du flux.
DOSSIER = "techniques"


def dossier(data_dir: Path | str | None) -> Path | None:
    return (Path(data_dir) / DOSSIER) if data_dir else None


def lire_toutes(data_dir: Path | str | None) -> dict[str, Technique]:
    """Les techniques déclarées sur cette machine, par leur nom.

    Aucune n'est un cas particulier : le fichier se nomme lui-même (clé
    « technique »), et son nom de fichier ne sert qu'à le trouver. Un fichier
    illisible ou qui ne tient pas est refusé ICI, en le nommant : découvert au
    cinquième run d'une chaîne, il aurait fait perdre les quatre précédents.
    """
    d = dossier(data_dir)
    if d is None or not d.is_dir():
        return {}
    lues: dict[str, Technique] = {}
    for fichier in sorted(d.glob("*.json")):
        technique = lire_fichier(fichier)
        if technique.nom in lues:
            raise WorkflowMappingError(
                f"deux fichiers déclarent la technique {technique.nom!r} dans {d} — "
                f"laquelle l'emporterait ?")
        lues[technique.nom] = technique
    return lues


def lire_fichier(fichier: Path) -> Technique:
    try:
        brut = json.loads(Path(fichier).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(
            f"technique {Path(fichier).stem!r} : impossible de lire {fichier} : {exc}") from exc
    return lire_technique(brut, Path(fichier).stem)


def vues(techniques: dict[str, Technique]) -> list[dict[str, Any]]:
    """Les techniques telles qu'un lanceur les rend : de quoi peupler une liste.

    Le libellé et le résumé viennent du fichier, jamais d'une table écrite dans
    un client — c'est la même règle que pour les menus : la liste appartient au
    fournisseur.
    """
    return [{"valeur": nom, "libelle": technique.libelle or nom, "resume": technique.resume,
             "par_defaut": bool(technique.par_defaut)}
            for nom, technique in sorted(techniques.items())]

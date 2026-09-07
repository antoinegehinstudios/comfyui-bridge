"""Bibliothèque de blocs réutilisables.

Un montage ne doit pas recopier la plomberie d'un bloc : le jour où le raccord
change, il faudrait la corriger dans chaque recette, et celles qu'on oublie
continuent de tourner avec l'ancienne. Un bloc vit donc UNE fois, dans
``_data/blocs/``, et les recettes l'incluent :

    {"pour": {...}, "faire": [{"utiliser": "segment-h3"}]}

Un bloc réutilisable déclare ses PORTS, et c'est ce qui le rend câblable sans
le lire :

    {
      "bloc": "segment-h3",
      "besoin":  {"modele": "MODEL", "vae": "VAE", ...},   # du fragment "commun"
      "attend":  {"derniere_image": "IMAGE"},              # du bloc précédent
      "sorties": {"images": "5", "derniere_image": "6"},   # ce qu'il offre
      "contenu": { ... }
    }

Les ports sont VÉRIFIÉS à l'inclusion : un montage qui ne fournit pas ce que le
bloc réclame est refusé avec le nom de ce qui manque, au lieu de produire un
graphe qui échouera trente minutes plus tard dans le moteur.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.errors import WorkflowMappingError

DOSSIER = "blocs"
UTILISER = "utiliser"


def _lire(chemin: Path) -> dict[str, Any]:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"bloc réutilisable illisible : {chemin} ({exc})") from None


def charger(racine: Path) -> dict[str, dict[str, Any]]:
    """Les blocs disponibles, par nom."""
    dossier = Path(racine) / DOSSIER
    if not dossier.is_dir():
        return {}
    blocs: dict[str, dict[str, Any]] = {}
    for fichier in sorted(dossier.glob("*.json")):
        brut = _lire(fichier)
        nom = str(brut.get("bloc") or fichier.stem)
        blocs[nom] = brut
    return blocs


def _offert_par(montage: list[Any], nom_fragment: str) -> dict[str, Any]:
    for bloc in montage or []:
        if isinstance(bloc, dict) and bloc.get("fragment") == nom_fragment:
            return dict(bloc.get("sorties") or {})
    return {}


def _verifier(bloc: dict[str, Any], montage: list[Any], depuis: str | None) -> None:
    """Les ports du bloc sont-ils servis par ce montage ?"""
    nom = bloc.get("bloc", "?")
    besoin = dict(bloc.get("besoin") or {})
    if besoin:
        commun = _offert_par(montage, "commun")
        manque = [r for r in besoin if r not in commun]
        if manque:
            raise WorkflowMappingError(
                f"bloc {nom!r} : le montage ne fournit pas {', '.join(sorted(manque))} "
                f"dans les sorties de « commun »",
                available=sorted(commun))
    attend = dict(bloc.get("attend") or {})
    if attend and depuis is not None:
        offert = _offert_par(montage, depuis)
        # Un bloc répété se sert lui-même au tour suivant : ses propres sorties
        # comptent donc aussi comme ce qui le précède.
        offert.update(dict(bloc.get("sorties") or {}))
        manque = [r for r in attend if r not in offert]
        if manque:
            raise WorkflowMappingError(
                f"bloc {nom!r} : ce qui le précède ({depuis!r}) n'offre pas "
                f"{', '.join(sorted(manque))}",
                available=sorted(offert))


def resoudre(montage: list[Any], blocs: dict[str, dict[str, Any]],
             amont: list[Any] | None = None, dernier: str | None = None) -> list[Any]:
    """Remplacer chaque « utiliser » par le bloc de la bibliothèque.

    Le montage rendu ne contient plus que des fragments ordinaires : le reste de
    la chaîne — dépliage, assemblage — n'a pas à connaître la bibliothèque.
    """
    # Le contexte suit dans les boucles : un bloc repete se raccorde a ce qui
    # precede la boucle, et la verification de ses ports doit le savoir.
    sortie: list[Any] = []
    vus: list[Any] = list(amont or [])
    dernier_fragment: str | None = dernier
    for element in montage or []:
        if not isinstance(element, dict):
            sortie.append(element)
            continue
        if UTILISER in element:
            nom = str(element[UTILISER])
            bloc = blocs.get(nom)
            if bloc is None:
                raise WorkflowMappingError(
                    f"bloc réutilisable {nom!r} introuvable",
                    available=sorted(blocs))
            _verifier(bloc, vus + sortie, dernier_fragment)
            fragment = {"fragment": bloc.get("fragment") or nom,
                        "contenu": bloc.get("contenu"),
                        "sorties": bloc.get("sorties")}
            # Une inclusion peut renommer le fragment ou surcharger ses sorties,
            # sans toucher au bloc partagé.
            for champ in ("fragment", "sorties"):
                if champ in element:
                    fragment[champ] = element[champ]
            sortie.append(fragment)
            dernier_fragment = fragment["fragment"]
            continue
        if "pour" in element:
            element = dict(element)
            element["faire"] = resoudre(element.get("faire") or [], blocs,
                                        vus + sortie, dernier_fragment)
        elif "si" in element:
            element = dict(element)
            element["alors"] = resoudre(element.get("alors") or [], blocs,
                                        vus + sortie, dernier_fragment)
            element["sinon"] = resoudre(element.get("sinon") or [], blocs,
                                        vus + sortie, dernier_fragment)
        elif "fragment" in element:
            dernier_fragment = str(element["fragment"])
        sortie.append(element)
    return sortie

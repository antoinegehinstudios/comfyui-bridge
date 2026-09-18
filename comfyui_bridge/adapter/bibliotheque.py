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


def _livres() -> Path:
    """Les blocs livrés avec le paquet, pour qu'une machine neuve en ait."""
    return Path(__file__).resolve().parent / "resources" / "montages-exemples" / DOSSIER


def charger(racine: Path | None = None) -> dict[str, dict[str, Any]]:
    """Les blocs disponibles, par nom.

    Deux sources, dans cet ordre : ceux livrés avec le paquet, puis ceux de la
    machine (``_data/blocs/``). Les seconds l'emportent — un poste peut adapter
    un bloc sans modifier le paquet, et sans que l'adaptation soit écrasée à la
    prochaine mise à jour.
    """
    blocs: dict[str, dict[str, Any]] = {}
    dossiers = [_livres()]
    if racine is not None:
        dossiers.append(Path(racine) / DOSSIER)
    for dossier in dossiers:
        if not dossier.is_dir():
            continue
        for fichier in sorted(dossier.glob("*.json")):
            brut = _lire(fichier)
            blocs[str(brut.get("bloc") or fichier.stem)] = brut
    return blocs


def _offert_par(montage: list[Any], nom_fragment: str) -> dict[str, Any]:
    for bloc in montage or []:
        if isinstance(bloc, dict) and bloc.get("fragment") == nom_fragment:
            return dict(bloc.get("sorties") or {})
    return {}


def _verifier(bloc: dict[str, Any], montage: list[Any], precedents: list[dict[str, Any]],
              se_suit: bool = False) -> None:
    """Les ports du bloc sont-ils servis par ce montage ?

    ``precedents`` : les fragments qui peuvent précéder ce bloc à l'exécution —
    un seul d'ordinaire ; après un « si », le dernier de chaque branche. Ce que
    le bloc attend doit être offert par CHACUN : lequel précède ne se sait
    qu'au dépliage.
    """
    nom = bloc.get("bloc", "?")
    besoin = dict(bloc.get("besoin") or {})
    if besoin:
        # Ce que le bloc a besoin de trouver posé UNE fois : dans « commun », ou
        # dans tout autre fragment nommé hors boucle (« modele », « texte » —
        # quand un montage sépare ses chargeurs pour que chaque run ne tienne
        # que les siens). Le contenu du bloc dit lequel il vise.
        offert: dict[str, Any] = {}
        for element in montage or []:
            if isinstance(element, dict) and element.get("fragment") and element.get("sorties"):
                offert.update(dict(element["sorties"]))
        manque = [r for r in besoin if r not in offert]
        if manque:
            raise WorkflowMappingError(
                f"bloc {nom!r} : le montage ne fournit pas {', '.join(sorted(manque))} "
                f"dans les sorties de ses fragments nommés (« commun »…)",
                available=sorted(offert))
    attend = dict(bloc.get("attend") or {})
    for precedent in (precedents if attend else []):
        offert = dict(precedent.get("sorties") or {})
        if se_suit:
            # DANS une boucle seulement : au deuxième tour, le bloc se suit
            # lui-même. Hors boucle, se compter comme son propre fournisseur
            # rendait le contrôle toujours vrai — donc inutile.
            offert.update(dict(bloc.get("sorties") or {}))
        manque = [r for r in attend if r not in offert]
        if manque:
            depuis = precedent.get("fragment")
            raise WorkflowMappingError(
                f"bloc {nom!r} : ce qui le précède ({depuis!r}) n'offre pas "
                f"{', '.join(sorted(manque))}",
                available=sorted(offert))


def resoudre(montage: list[Any], blocs: dict[str, dict[str, Any]],
             amont: list[Any] | None = None, dernier: str | None = None,
             dans_boucle: bool = False) -> list[Any]:
    """Remplacer chaque « utiliser » par le bloc de la bibliothèque.

    Le montage rendu ne contient plus que des fragments ordinaires : le reste de
    la chaîne — dépliage, assemblage — n'a pas à connaître la bibliothèque.
    """
    precedents = [f for f in (amont or []) if isinstance(f, dict) and f.get("fragment") == dernier]
    return _resoudre(montage, blocs, list(amont or []), precedents, dans_boucle)[0]


def _resoudre(montage: list[Any], blocs: dict[str, dict[str, Any]], vus: list[Any],
              precedents: list[dict[str, Any]], dans_boucle: bool
              ) -> tuple[list[Any], list[dict[str, Any]]]:
    """Le montage résolu, et les fragments par lesquels il FINIT (ceux qu'un
    bloc posé juste après verra comme « précédent »)."""
    # Le contexte suit dans les boucles : un bloc repete se raccorde a ce qui
    # precede la boucle, et la verification de ses ports doit le savoir.
    sortie: list[Any] = []
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
            _verifier(bloc, vus + sortie, precedents, dans_boucle)
            fragment = {"fragment": bloc.get("fragment") or nom,
                        "contenu": bloc.get("contenu"),
                        "sorties": bloc.get("sorties")}
            # Une inclusion peut renommer le fragment ou surcharger ses sorties,
            # sans toucher au bloc partagé.
            for champ in ("fragment", "sorties"):
                if champ in element:
                    fragment[champ] = element[champ]
            sortie.append(fragment)
            precedents = [fragment]
            continue
        if "pour" in element:
            element = dict(element)
            element["faire"], fin = _resoudre(element.get("faire") or [], blocs,
                                              vus + sortie, precedents, True)
            precedents = fin or precedents
        elif "si" in element:
            # Après un « si », le précédent est le dernier fragment de la branche
            # prise — et laquelle ne se sait qu'au dépliage : les deux comptent.
            element = dict(element)
            element["alors"], fin_alors = _resoudre(element.get("alors") or [], blocs,
                                                    vus + sortie, precedents, dans_boucle)
            element["sinon"], fin_sinon = _resoudre(element.get("sinon") or [], blocs,
                                                    vus + sortie, precedents, dans_boucle)
            precedents = (fin_alors or precedents) + [f for f in (fin_sinon or precedents)
                                                       if f not in (fin_alors or precedents)]
        elif "fragment" in element:
            precedents = [element]
        sortie.append(element)
    return sortie, precedents

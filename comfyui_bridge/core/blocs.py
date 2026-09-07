"""Blocs de boucle : ``pour``, ``si`` / ``sinon``.

Un graphe qui recopie huit fois le même bloc a sa durée écrite dedans. Changer
la durée demande de réécrire le graphe. Ces blocs déplacent le nombre de
répétitions dans les PARAMÈTRES : le demandeur donne une durée, le dépliage en
déduit le nombre de tours.

Rien ici ne sait ce qu'est un nœud. Un bloc porte un ``contenu`` opaque que
seul l'adaptateur sait recoudre. C'est ce qui permet à la même boucle de servir
LTX aujourd'hui et un autre moteur demain.

    plan = [
        {"fragment": "commun", "contenu": {...}},
        {"pour": {"jusqu_a": "duration_s", "chaque": 8.0, "deja": 8.0},
         "faire": [{"fragment": "segment", "contenu": {...}}]},
        {"si": {"parametre": "fps", "op": "gte", "valeur": 50},
         "alors": [...], "sinon": [...]},
    ]

    deplier(plan, {"duration_s": 32, "fps": 25})
    -> [Fragment("commun", 0), Fragment("segment", 0), ... Fragment("segment", 2)]
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .errors import IntentValidationError


@dataclass(frozen=True)
class Fragment:
    """Un morceau du montage, tel que le dépliage le rend.

    ``tour`` vaut 0 hors boucle, puis 0, 1, 2… pour chaque répétition. C'est ce
    numéro qui permet ensuite de renuméroter les nœuds sans collision et de
    savoir à qui « le précédent » renvoie.
    """

    nom: str
    tour: int
    contenu: Any
    tours_total: int = 1


# -- lecture des valeurs ------------------------------------------------------

def _valeur(x: Any, params: dict[str, Any]) -> Any:
    """Une valeur littérale, ou le nom d'un paramètre résolu.

    Écrire ``"duration_s"`` là où un nombre est attendu vaut « la durée
    demandée ». C'est ce qui fait qu'une recette n'a aucun chiffre de durée
    en dur.
    """
    if isinstance(x, str):
        if x not in params:
            raise IntentValidationError(
                f"bloc de boucle : le paramètre {x!r} n'a pas été résolu",
                available=sorted(params),
            )
        return params[x]
    return x


def _nombre(x: Any, params: dict[str, Any], ou: str) -> float:
    v = _valeur(x, params)
    try:
        return float(v)
    except (TypeError, ValueError):
        raise IntentValidationError(
            f"bloc de boucle : {ou} vaut {v!r}, un nombre était attendu") from None


# -- prédicats ----------------------------------------------------------------

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "in": lambda a, b: a in (b if isinstance(b, (list, tuple)) else [b]),
}


def evaluer(condition: dict[str, Any], params: dict[str, Any]) -> bool:
    """Un prédicat déclaratif, jamais du code.

    Une recette est une donnée : elle peut venir d'un fichier ingéré. Y évaluer
    une expression serait ouvrir l'exécution de code arbitraire à quiconque pose
    un gabarit. Les comparaisons nommées suffisent à ce que la boucle demande.
    """
    if not isinstance(condition, dict):
        raise IntentValidationError("bloc « si » : une condition nommée était attendue")
    parametre = condition.get("parametre")
    if not parametre:
        raise IntentValidationError("bloc « si » : il manque « parametre »")
    op = condition.get("op", "eq")
    fn = _OPS.get(op)
    if fn is None:
        raise IntentValidationError(f"bloc « si » : opérateur inconnu {op!r}",
                                    available=sorted(_OPS))
    # Un paramètre ABSENT n'est pas une erreur : il vaut « non fourni », c'est
    # à dire None. C'est ce qui permet d'écrire « si une image d'amorce a été
    # jointe » (`ne` None) sans que l'absence fasse échouer tout le montage, et
    # « si aucune n'a été jointe » (`eq` None) dans l'autre sens.
    gauche = params.get(parametre)
    droite = condition.get("valeur")
    if gauche is None and op in ("lt", "lte", "gt", "gte"):
        # Comparer un ordre à « non fourni » n'a pas de sens ; c'est faux, et
        # ça ne lève pas : un montage ne casse pas parce qu'un champ manque.
        return False
    return bool(fn(gauche, droite))


# -- comptage des tours -------------------------------------------------------

def tours_de(pour: dict[str, Any], params: dict[str, Any]) -> int:
    """Combien de fois le corps se répète.

    ``jusqu_a`` est la grandeur à couvrir, ``chaque`` ce qu'un tour couvre, et
    ``deja`` ce qui est couvert avant la boucle (le premier segment, produit
    hors boucle). Le reste est arrondi au tour supérieur : mieux vaut dépasser
    la durée demandée que la rendre plus courte que promis.
    """
    cible = _nombre(pour.get("jusqu_a"), params, "« jusqu_a »")
    chaque = _nombre(pour.get("chaque"), params, "« chaque »")
    if chaque <= 0:
        raise IntentValidationError("bloc « pour » : « chaque » doit être positif")
    deja = _nombre(pour.get("deja", 0), params, "« deja »") if "deja" in pour else 0.0
    tours = math.ceil((cible - deja) / chaque - 1e-9)
    tours = max(0, tours)
    plafond = pour.get("max")
    if plafond is not None:
        tours = min(tours, int(_nombre(plafond, params, "« max »")))
    return tours


# -- dépliage -----------------------------------------------------------------

def parametres_pilotes(plan: list[dict[str, Any]]) -> set[str]:
    """Les paramètres qui PILOTENT le montage, sans viser aucun nœud.

    La durée demandée ne s'écrit nulle part dans le graphe : elle décide du
    nombre de blocs. Sans cette liste, la passerelle répondrait « ce workflow
    n'expose pas duration_s » à quelqu'un qui vient précisément de régler la
    durée. Elle se déduit du montage lui-même — une seconde déclaration
    pourrait le contredire.
    """
    noms: set[str] = set()
    for bloc in plan or []:
        if not isinstance(bloc, dict):
            continue
        if "pour" in bloc:
            pour = bloc["pour"] or {}
            for champ in ("jusqu_a", "chaque", "deja", "max"):
                v = pour.get(champ)
                if isinstance(v, str):
                    noms.add(v)
            noms |= parametres_pilotes(bloc.get("faire") or [])
        elif "si" in bloc:
            condition = bloc["si"] if isinstance(bloc["si"], dict) else {}
            if condition.get("parametre"):
                noms.add(str(condition["parametre"]))
            noms |= parametres_pilotes(bloc.get("alors") or [])
            noms |= parametres_pilotes(bloc.get("sinon") or [])
        elif "fragment" in bloc:
            noms |= _consignes_decoupees(bloc.get("contenu"))
    return noms


def _consignes_decoupees(contenu) -> set[str]:
    """Les consignes qu'un fragment decoupe en temps, donc qu'il faut accepter."""
    noms: set[str] = set()
    for noeud in (contenu or {}).values():
        for valeur in ((noeud or {}).get("inputs") or {}).values():
            if isinstance(valeur, dict) and "$segment" in valeur:
                noms.add(str(valeur["$segment"]))
    return noms


def deplier(plan: list[dict[str, Any]], params: dict[str, Any]) -> list[Fragment]:
    """Le montage à plat : plus de boucle, plus de condition, que des fragments."""
    sortie: list[Fragment] = []
    for bloc in plan or []:
        if not isinstance(bloc, dict):
            raise IntentValidationError("montage : un bloc doit être un objet nommé")
        if "pour" in bloc:
            n = tours_de(bloc["pour"], params)
            corps = bloc.get("faire") or []
            for tour in range(n):
                for f in deplier(corps, params):
                    # Le tour du fragment est celui de la boucle qui l'englobe ;
                    # une boucle imbriquée garderait sinon le tour de l'intérieur
                    # et deux tours différents porteraient le même numéro.
                    sortie.append(Fragment(f.nom, tour, f.contenu, n))
        elif "si" in bloc:
            branche = bloc.get("alors") if evaluer(bloc["si"], params) else bloc.get("sinon")
            sortie.extend(deplier(branche or [], params))
        elif "fragment" in bloc:
            sortie.append(Fragment(str(bloc["fragment"]), 0, bloc.get("contenu")))
        else:
            raise IntentValidationError(
                "montage : un bloc doit être « fragment », « pour » ou « si »",
                available=["fragment", "pour", "si"])
    return sortie

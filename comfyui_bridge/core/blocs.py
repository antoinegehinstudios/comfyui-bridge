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
    # Ce que ce fragment OFFRE au suivant, par nom de rôle : {"images": "6"}.
    # Sans ça, le bloc suivant devrait viser un numéro de nœud convenu — une
    # convention tacite qui casse en silence le jour où le fragment change.
    sorties: Any = None
    # La déclaration « pour » de la boucle qui englobe ce fragment, telle
    # qu'elle est écrite (None hors boucle). C'est elle qui dit si chaque tour
    # est un RUN à part (« un_run_par_tour ») et ce qui passe d'un run au
    # suivant (« relais ») : l'assembleur le lit sur le fragment lui-même, sans
    # qu'une seconde déclaration ait à redire où la boucle commence.
    boucle: Any = None


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


# -- un run par tour ----------------------------------------------------------
#
# Un montage qui rend plusieurs blocs dans UN SEUL prompt du moteur les tient
# tous dans la même exécution : mesuré le 2026-09-18, les modèles y restent en
# mémoire jusqu'au dernier nœud (le moteur retient chaque modèle tant que le
# prompt court) et la passerelle n'a aucun point de contrôle entre deux blocs —
# le poste a fini par tuer le moteur. Une boucle qui déclare
# « un_run_par_tour » demande un run PAR TOUR : la passerelle attend la place
# avant chacun, et ce qui passe d'un tour au suivant est déclaré dans
# « relais » — un port du dernier fragment du tour, écrit par un nœud à la fin
# du run, relu par un nœud au début du suivant.

UN_RUN_PAR_TOUR = "un_run_par_tour"
RELAIS = "relais"
TOUR = "tour"
TOURS_TOTAL = "tours_total"
# Des PHASES : chaque tour de boucle (un bloc) se déplie en `phases` tours
# successifs — « encoder » puis « rendre », par exemple — pour qu'un run n'ait
# à tenir que les modèles de sa phase. Dans le corps, `bloc` est le tour de
# boucle proprement dit et `phase` le rang dans le bloc ; `tour` numérote les
# runs. Sans déclaration : une phase, et `bloc` vaut `tour`.
PHASES = "phases"
BLOC = "bloc"
PHASE = "phase"


def phases_de(pour: dict[str, Any]) -> int:
    """Combien de phases par tour de boucle (1 sans déclaration)."""
    brut = pour.get(PHASES, 1)
    try:
        phases = int(brut)
    except (TypeError, ValueError):
        raise IntentValidationError(f"bloc « pour » : « phases » vaut {brut!r}, un entier était attendu") from None
    if phases < 1:
        raise IntentValidationError("bloc « pour » : « phases » doit valoir au moins 1")
    return phases


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
            if pour.get(UN_RUN_PAR_TOUR):
                # Le tour à rendre seul, et le fichier que chaque relais relit :
                # la passerelle les envoie run après run, ils pilotent le
                # dépliage sans viser aucun nœud — comme la durée.
                noms.add(TOUR)
                for port in (pour.get(RELAIS) or {}):
                    noms.add(f"{RELAIS}_{port}")
            noms |= parametres_pilotes(bloc.get("faire") or [])
        elif "si" in bloc:
            condition = bloc["si"] if isinstance(bloc["si"], dict) else {}
            if condition.get("parametre"):
                noms.add(str(condition["parametre"]))
            noms |= parametres_pilotes(bloc.get("alors") or [])
            noms |= parametres_pilotes(bloc.get("sinon") or [])
    return noms


def deplier(plan: list[dict[str, Any]], params: dict[str, Any]) -> list[Fragment]:
    """Le montage à plat : plus de boucle, plus de condition, que des fragments."""
    sortie: list[Fragment] = []
    for bloc in plan or []:
        if not isinstance(bloc, dict):
            raise IntentValidationError("montage : un bloc doit être un objet nommé")
        if "pour" in bloc:
            pour = bloc["pour"] or {}
            n = tours_de(pour, params)
            phases = phases_de(pour)
            corps = bloc.get("faire") or []
            for tour in range(n * phases):
                # Dans le corps, un « si » voit le tour : c'est ce qui permet
                # d'écrire « au premier tour, l'amorce ; ensuite, le segment »
                # sans sortir l'amorce de la boucle — et donc sans lui donner
                # un run à part quand chaque tour est un run. Avec des PHASES,
                # il voit aussi le bloc (le tour de boucle proprement dit) et
                # la phase : « en phase 0, encoder ; en phase 1, rendre ».
                portee = dict(params, **{TOUR: tour, TOURS_TOTAL: n * phases,
                                         BLOC: tour // phases, PHASE: tour % phases})
                for f in deplier(corps, portee):
                    # Le tour du fragment est celui de la boucle qui l'englobe ;
                    # une boucle imbriquée garderait sinon le tour de l'intérieur
                    # et deux tours différents porteraient le même numéro.
                    sortie.append(Fragment(f.nom, tour, f.contenu, n * phases, f.sorties,
                                           f.boucle if f.boucle is not None else pour))
        elif "si" in bloc:
            branche = bloc.get("alors") if evaluer(bloc["si"], params) else bloc.get("sinon")
            sortie.extend(deplier(branche or [], params))
        elif "fragment" in bloc:
            sortie.append(Fragment(str(bloc["fragment"]), 0, bloc.get("contenu"),
                                   1, bloc.get("sorties")))
        else:
            raise IntentValidationError(
                "montage : un bloc doit être « fragment », « pour » ou « si »",
                available=["fragment", "pour", "si"])
    return sortie


# -- un run par tour : ce que le montage demande ------------------------------

def boucle_par_run(plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La déclaration « pour » qui demande un run par tour, s'il y en a une.

    Une seule : deux boucles qui demanderaient chacune leurs runs n'auraient
    pas d'ordre entre elles, et une boucle imbriquée dans l'autre non plus.
    """
    trouvees: list[dict[str, Any]] = []

    def parcourir(blocs: list[Any], dedans: bool) -> None:
        for bloc in blocs or []:
            if not isinstance(bloc, dict):
                continue
            if "pour" in bloc:
                pour = bloc["pour"] or {}
                if pour.get(UN_RUN_PAR_TOUR):
                    if dedans:
                        raise IntentValidationError(
                            "montage : « un_run_par_tour » dans une boucle imbriquée — "
                            "seule une boucle de premier niveau peut demander un run par tour")
                    trouvees.append(pour)
                parcourir(bloc.get("faire") or [], dedans or bool(pour.get(UN_RUN_PAR_TOUR)))
            elif "si" in bloc:
                parcourir(bloc.get("alors") or [], dedans)
                parcourir(bloc.get("sinon") or [], dedans)

    parcourir(plan, False)
    if len(trouvees) > 1:
        raise IntentValidationError(
            "montage : deux boucles demandent un run par tour ; une seule le peut")
    return trouvees[0] if trouvees else None


def tours_separes(plan: list[dict[str, Any]], params: dict[str, Any]) -> int | None:
    """Combien de runs ce montage demande — un par tour — ou None quand il
    tient en un seul run (aucune boucle ne le demande, ou un seul tour)."""
    pour = boucle_par_run(plan)
    if pour is None:
        return None
    n = tours_de(pour, params) * phases_de(pour)
    return n if n > 1 else None

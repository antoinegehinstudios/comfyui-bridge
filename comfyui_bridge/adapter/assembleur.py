"""Recoudre les fragments dépliés en un graphe API ComfyUI.

``core.blocs`` compte les tours et rend une liste à plat :

    amorce · segment(tour 0) · segment(tour 1) · segment(tour 2) · montage

Chaque fragment porte un bout de graphe écrit avec ses PROPRES numéros de
nœuds. Deux tours du même fragment portent donc les mêmes numéros : les poser
tels quels dans un seul graphe les ferait s'écraser. L'assembleur renumérote
chaque instance, puis réécrit les liens.

Trois façons de désigner un nœud dans un lien :

* ``["3", 0]``            — un nœud du MÊME fragment, tel que son auteur l'a numéroté ;
* ``["$commun.20", 0]``   — un nœud d'un fragment nommé, posé une seule fois
                            (le modèle, l'encodeur de texte, le VAE) ;
* ``["$precedent.29", 0]``— le même nœud dans l'instance qui précède celle-ci.

``$precedent`` est ce qui fait la chaîne : au tour 2, il renvoie au tour 1 ; au
tour 0, il renvoie à ce qui vient juste avant dans le montage — l'amorce. C'est
l'ORDRE du montage qui dit à quoi chaque bloc se raccorde, pas une déclaration
séparée qui pourrait le contredire.
"""

from __future__ import annotations

from typing import Any

from ..core.blocs import Fragment
from ..core.errors import WorkflowMappingError

PREFIXE = "$"
PRECEDENT = "precedent"


def _instances(fragments: list[Fragment]) -> list[tuple[str, int]]:
    return [(f.nom, f.tour) for f in fragments]


def adresse(reference: str, table: dict[tuple[str, int], dict[str, str]],
            sorties: dict[tuple[str, int], dict[str, str]] | None = None) -> str:
    """Le numéro final d'un nœud désigné DE L'EXTÉRIEUR du montage.

    C'est ce qui permet à une déclaration de liaison de viser un nœud dont le
    numéro n'existe qu'après le dépliage : ``"$commun.26"`` (le tour 0 est
    sous-entendu) ou ``"$segment#2.1"`` pour un tour précis.
    """
    corps = reference[len(PREFIXE):] if reference.startswith(PREFIXE) else reference
    if "." not in corps:
        raise WorkflowMappingError(
            f"liaison {reference!r} : il manque le numéro de nœud "
            f"(attendu \"$<fragment>.<nœud>\")")
    nom, local = corps.split(".", 1)
    tour = 0
    if "#" in nom:
        nom, _, brut = nom.partition("#")
        try:
            tour = int(brut)
        except ValueError:
            raise WorkflowMappingError(f"liaison {reference!r} : tour {brut!r} illisible") from None
    if sorties:
        local = _resoudre_nom(local, (nom, tour), sorties)
    locaux = table.get((nom, tour))
    if locaux is None:
        raise WorkflowMappingError(
            f"liaison {reference!r} : {nom!r} (tour {tour}) n'est pas dans ce montage",
            available=sorted({f"{n}#{t}" for n, t in table}))
    if local not in locaux:
        raise WorkflowMappingError(
            f"liaison {reference!r} : le nœud {local!r} n'existe pas dans {nom!r}")
    return locaux[local]


def numeroter(fragments: list[Fragment]) -> dict[tuple[str, int], dict[str, str]]:
    """La table des numéros du montage — même résultat que ce qu'assemble ``assembler``."""
    return _numeroter(fragments)


def sorties_nommees(fragments: list[Fragment]) -> dict[tuple[str, int], dict[str, str]]:
    """Ce que chaque instance offre, par nom de rôle."""
    return _sorties(fragments)


def _sorties(fragments: list[Fragment]) -> dict[tuple[str, int], dict[str, str]]:
    """Ce que chaque instance offre, par nom de rôle."""
    return {(f.nom, f.tour): dict(f.sorties or {}) for f in fragments}


def _numeroter(fragments: list[Fragment]) -> dict[tuple[str, int], dict[str, str]]:
    """À chaque instance, ses nouveaux numéros de nœuds.

    Les numéros sont attribués dans l'ordre du montage : le graphe produit se
    lit dans l'ordre où il a été monté, ce qui rend un dépliage inspectable à
    l'œil quand il faut comprendre ce que le moteur a reçu.
    """
    table: dict[tuple[str, int], dict[str, str]] = {}
    suivant = 1
    for f in fragments:
        contenu = f.contenu or {}
        if not isinstance(contenu, dict):
            raise WorkflowMappingError(
                f"fragment {f.nom!r} : son contenu doit être un morceau de graphe")
        locaux: dict[str, str] = {}
        for local in contenu:
            locaux[str(local)] = str(suivant)
            suivant += 1
        table[(f.nom, f.tour)] = locaux
    return table


def _resoudre_nom(local: str, vise: tuple[str, int],
                  sorties: dict[tuple[str, int], dict[str, str]]) -> str:
    """Traduire un nom de rôle en numéro de nœud, quand le fragment en déclare.

    Viser « les images » plutôt que « le nœud 13 » : le fragment peut changer sa
    plomberie sans que la chaîne se casse, et un nom absent se dit au lieu de
    pointer silencieusement ailleurs.
    """
    return (sorties.get(vise) or {}).get(local, local)


def _cible(reference: str, moi: tuple[str, int], ordre: list[tuple[str, int]],
           table: dict[tuple[str, int], dict[str, str]],
           sorties: dict[tuple[str, int], dict[str, str]] | None = None) -> str:
    """Le nouveau numéro visé par une référence, ou une erreur qui se lit."""
    sorties = sorties or {}
    if not reference.startswith(PREFIXE):
        locaux = table[moi]
        if reference not in locaux:
            raise WorkflowMappingError(
                f"fragment {moi[0]!r} (tour {moi[1]}) : lien vers le nœud {reference!r}, "
                f"qui n'est pas dans ce fragment ; pour viser un autre fragment, "
                f"écrire \"$<fragment>.{reference}\" ou \"$precedent.{reference}\"")
        return locaux[reference]

    corps = reference[len(PREFIXE):]
    if "." not in corps:
        raise WorkflowMappingError(
            f"fragment {moi[0]!r} : référence {reference!r} sans numéro de nœud "
            f"(attendu \"$<fragment>.<nœud>\")")
    nom, local = corps.split(".", 1)

    if nom == PRECEDENT:
        position = ordre.index(moi)
        if position == 0:
            raise WorkflowMappingError(
                f"fragment {moi[0]!r} (tour {moi[1]}) : \"$precedent\" alors que rien "
                f"ne le précède dans le montage")
        vise = ordre[position - 1]
    else:
        # Un fragment nommé est posé une seule fois : on vise son tour 0. C'est
        # ce qui évite de recharger le modèle à chaque segment.
        vise = (nom, 0)
        if vise not in table:
            raise WorkflowMappingError(
                f"fragment {moi[0]!r} : référence à {nom!r}, qui n'est pas dans ce montage",
                available=sorted({n for n, _ in table}))

    locaux = table[vise]
    vrai = _resoudre_nom(local, vise, sorties)
    if vrai not in locaux:
        offert = sorted((sorties.get(vise) or {}))
        raise WorkflowMappingError(
            f"fragment {moi[0]!r} : {local!r} n'est ni une sortie déclarée de "
            f"{vise[0]!r} ni un de ses nœuds",
            available=offert or sorted(locaux))
    return locaux[vrai]


def _est_lien(valeur: Any) -> bool:
    """Un lien ComfyUI : [numéro de nœud, numéro de sortie]."""
    return (isinstance(valeur, list) and len(valeur) == 2
            and isinstance(valeur[0], str) and isinstance(valeur[1], int))


CONSTANTE = "$const."
TOUR = "$tour"
CALCUL = "$calc"

_NOEUDS_AUTORISES = None       # rempli à la première utilisation (voir _calculer)


def _calculer(expression: str, portee: dict[str, Any], ou: str) -> Any:
    """Une arithmétique sur le numéro de tour, évaluée au montage.

    Un bloc répété doit souvent placer quelque chose plus loin à chaque tour —
    l'instant où commence le morceau à produire, par exemple. C'est l'expression
    d'indice d'une boucle ordinaire, et elle se calcule ici plutôt que dans le
    graphe, où il faudrait un nœud de calcul par terme.

    L'expression est parcourue, pas exécutée : une recette peut venir d'un
    gabarit ingéré, et ``eval`` y ouvrirait l'exécution de code arbitraire.
    """
    global _NOEUDS_AUTORISES
    import ast
    if _NOEUDS_AUTORISES is None:
        _NOEUDS_AUTORISES = (
            ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
            ast.USub, ast.UAdd,
        )
    try:
        arbre = ast.parse(str(expression), mode="eval")
    except SyntaxError as exc:
        raise WorkflowMappingError(f"{ou} : calcul illisible {expression!r} ({exc.msg})") from None
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, _NOEUDS_AUTORISES):
            raise WorkflowMappingError(
                f"{ou} : le calcul {expression!r} contient {type(noeud).__name__}, "
                f"qui n'est pas une arithmétique")
        if isinstance(noeud, ast.Name) and noeud.id not in portee:
            raise WorkflowMappingError(
                f"{ou} : le calcul {expression!r} nomme {noeud.id!r}, inconnu ici",
                available=sorted(portee))
    return eval(compile(arbre, "<calc>", "eval"), {"__builtins__": {}}, dict(portee))


SEGMENT = "$segment"
RANG = "$rang"
TOTAL = "$total"
TEXTE = "$texte"


def _texte(forme: dict[str, Any], portee: dict[str, Any], ou: str) -> str:
    """Un nom numéroté par le tour : ``bloc`` devient ``bloc_003``.

    Chaque bloc écrit son propre fichier. Sans numéro dans le NOM, l'ordre des
    morceaux dépendrait de l'ordre où le moteur a fini de les écrire — et rien
    ne garantit qu'il suive celui du montage. Le rang est écrit, il ne se
    devine pas.
    """
    rang = int(_calculer(forme.get(RANG, "0"), portee, ou))
    base = forme[TEXTE]
    if isinstance(base, str) and base.startswith(CONSTANTE):
        nom = base[len(CONSTANTE):]
        if nom not in portee:
            raise WorkflowMappingError(
                f"{ou} : constante {nom!r} non déclarée dans ce montage",
                available=sorted(portee))
        base = portee[nom]
    return "%s_%03d" % (base, rang)


def _segment(forme: dict[str, Any], portee: dict[str, Any], ou: str) -> str:
    """La part de consigne qui revient à CE bloc.

    Un montage en blocs envoie la même consigne à chacun : personne ne sait où
    il en est, et « puis, plus tard, telle chose » ne peut pas être tenu. Ici la
    consigne se découpe sur les barres verticales, la PREMIÈRE part vaut pour
    toute la vidéo (le décor, le style), et les suivantes se répartissent sur
    les blocs dans l'ordre. Un bloc reçoit donc : la part globale, puis la sienne.

        "un tableau qui s'anime | la lance se lève | le cheval se cabre"

    Trois blocs, deux temps : le premier bloc lève la lance, les deux suivants
    cabrent le cheval. Le décor, lui, est rappelé à chaque fois.
    """
    nom = forme[SEGMENT]
    brut = portee.get(nom)
    if brut is None:
        raise WorkflowMappingError(
            f"{ou} : la consigne {nom!r} n'est pas connue au montage",
            available=sorted(k for k in portee if isinstance(portee[k], str)))
    parts = [p.strip() for p in str(brut).split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    global_, temps = parts[0], parts[1:]
    if not temps:
        return global_
    rang = int(_calculer(forme.get(RANG, "0"), portee, ou))
    total = max(1, int(_calculer(forme.get(TOTAL, "1"), portee, ou)))
    # Chaque temps est posé UNE fois, à sa place dans la durée. Les blocs entre
    # deux temps ne reçoivent que la part globale : ils PROLONGENT. Redonner la
    # même consigne à deux blocs de suite ferait rejouer l'action une seconde
    # fois au lieu de continuer le plan.
    # Premier arrivé sur une place, premier servi : quand plusieurs temps
    # tombent sur le même bloc (moins de blocs que de temps), c'est le PLUS TÔT
    # qui s'y joue. Écrasés dans l'autre sens, le premier bloc recevait le
    # dernier temps et la video commençait par sa fin.
    places: dict[int, int] = {}
    for i in range(len(temps)):
        places.setdefault((i * total) // len(temps), i)
    i = places.get(rang)
    if i is None:
        return global_
    return "%s %s" % (global_, temps[i])


def _constante(valeur: Any, constantes: dict[str, Any], ou: str) -> Any:
    """Une valeur de nœud écrite ``"$const.<nom>"`` vaut la constante du montage.

    Le nombre d'images d'un segment sert à DEUX endroits : le nœud qui fixe la
    longueur, et le compte de tours de la boucle. Écrit deux fois, il finit par
    diverger — et une boucle qui compte autrement que ce que le graphe produit
    livre une durée fausse sans que rien ne le signale.
    """
    if isinstance(valeur, dict) and TEXTE in valeur:
        return _texte(valeur, constantes, ou)
    if isinstance(valeur, dict) and SEGMENT in valeur:
        return _segment(valeur, constantes, ou)
    if isinstance(valeur, dict) and CALCUL in valeur:
        return _calculer(valeur[CALCUL], dict(constantes), ou)
    if not isinstance(valeur, str):
        return valeur
    if valeur == TOUR:
        return constantes.get("tour", 0)
    if not valeur.startswith(CONSTANTE):
        return valeur
    nom = valeur[len(CONSTANTE):]
    if nom not in constantes:
        raise WorkflowMappingError(
            f"{ou} : constante {nom!r} non déclarée dans ce montage",
            available=sorted(constantes))
    return constantes[nom]


def assembler(fragments: list[Fragment],
              constantes: dict[str, Any] | None = None,
              blocs: list[str] | None = None) -> dict[str, Any]:
    """Le graphe API que le moteur recevra.

    ``blocs`` nomme les fragments qui PRODUISENT un morceau de la vidéo (l'amorce
    et le segment répété, par exemple). Chaque instance de ces fragments voit
    alors son rang dans le montage et le nombre total de morceaux — ``bloc_rang``
    et ``blocs_total`` dans la portée des calculs. C'est ce qui permet à un bloc
    de savoir où il en est dans le récit : le tour de boucle ne le dit pas
    (l'amorce est hors boucle, et le premier tour vaut 0 alors qu'il est le
    deuxième morceau).
    """
    if not fragments:
        raise WorkflowMappingError("montage vide : aucun fragment à assembler")

    blocs = list(blocs or [])
    rangs: dict[tuple[str, int], int] = {}
    for f in fragments:
        if f.nom in blocs:
            rangs[(f.nom, f.tour)] = len(rangs)
    inconnus = [b for b in blocs if b not in {f.nom for f in fragments}]
    if inconnus:
        raise WorkflowMappingError(
            f"montage : « blocs » nomme {inconnus[0]!r}, qui n'est pas un fragment du montage",
            available=sorted({f.nom for f in fragments}))

    ordre = _instances(fragments)
    sorties = _sorties(fragments)
    doublons = [x for x in ordre if ordre.count(x) > 1]
    if doublons:
        raise WorkflowMappingError(
            f"montage : {doublons[0][0]!r} apparaît deux fois au même tour ; "
            f"un fragment posé plusieurs fois doit l'être par une boucle")
    table = _numeroter(fragments)

    graphe: dict[str, Any] = {}
    for f in fragments:
        moi = (f.nom, f.tour)
        for local, noeud in (f.contenu or {}).items():
            if not isinstance(noeud, dict) or "class_type" not in noeud:
                raise WorkflowMappingError(
                    f"fragment {f.nom!r} : le nœud {local!r} n'a pas de « class_type »")
            copie = {k: v for k, v in noeud.items() if k != "inputs"}
            entrees = {}
            for champ, valeur in (noeud.get("inputs") or {}).items():
                if _est_lien(valeur):
                    entrees[champ] = [_cible(valeur[0], moi, ordre, table, sorties), valeur[1]]
                else:
                    # Le numéro de tour est dans la portée : un bloc répété doit
                    # pouvoir placer quelque chose plus loin à chaque répétition.
                    portee = dict(constantes or {},
                                  tour=f.tour, tours_total=f.tours_total)
                    if moi in rangs:
                        portee["bloc_rang"] = rangs[moi]
                        portee["blocs_total"] = len(rangs)
                    entrees[champ] = _constante(
                        valeur, portee,
                        f"fragment {f.nom!r} (tour {f.tour}), nœud {local!r}, entrée {champ!r}")
            copie["inputs"] = entrees
            graphe[table[moi][str(local)]] = copie
    return graphe

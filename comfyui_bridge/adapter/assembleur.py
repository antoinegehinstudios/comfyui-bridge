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

from ..core.blocs import Fragment, evaluer
from ..core.errors import IntentValidationError, WorkflowMappingError

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


SI = "$si"
ALORS = "alors"
SINON = "sinon"


def _choisir(forme: dict[str, Any], portee: dict[str, Any], ou: str) -> Any:
    """Une entrée qui dépend d'un réglage se décide AU MONTAGE, pas dans le graphe.

    ``{"$si": {"parametre": "fps", "op": "ne", "valeur": 24}, "alors": ["5", 0],
    "sinon": ["3", 0]}`` : le même prédicat déclaratif que le « si » d'un montage
    (``core.blocs.evaluer``), et la branche retenue est une entrée ordinaire —
    un lien, une valeur, une constante, un calcul, ou un autre « $si ».

    Mesuré le 2026-09-18 : la même décision confiée à un commutateur PARESSEUX du
    moteur (`ComfySwitchNode`) lui faisait ré-exécuter, sous `--cache-none`, le
    modèle et l'échantillonneur d'un bloc entier — le producteur avait fini
    avant que la branche ne soit réclamée, et rien ne se souvenait de sa sortie
    (`tasks/enquete-double-echantillonnage-2026-09-18.md`). Décidée ici, la
    branche non prise n'est reliée à aucune sortie : le moteur ne la voit pas.
    """
    manque = [k for k in (ALORS, SINON) if k not in forme]
    if manque:
        raise WorkflowMappingError(
            f"{ou} : « $si » sans « {manque[0]} » — les deux branches doivent être écrites")
    try:
        vrai = evaluer(forme[SI], portee)
    except IntentValidationError as exc:
        raise WorkflowMappingError(f"{ou} : {exc.detail}", **exc.extensions) from None
    return forme[ALORS] if vrai else forme[SINON]


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


# -- un run par tour ----------------------------------------------------------
#
# Une boucle qui déclare « un_run_par_tour » (voir core.blocs) n'est pas
# envoyée entière au moteur : chaque tour part dans son propre run, et
# l'assembleur ne recoud alors que la FENÊTRE de ce run —
#
# * les fragments du tour demandé ;
# * ce qui est AVANT la boucle et que le tour cite par son nom (« $commun.x »),
#   de proche en proche : le modèle, l'encodeur, tout ce qui est posé une fois
#   et que chaque run doit reposer ;
# * ce qui est avant la boucle sans être cité par son nom, au premier run
#   seulement (une amorce hors boucle) ; ce qui est après, au dernier.
#
# Ce qui traverse la frontière entre deux runs est un RELAIS, déclaré sur la
# boucle : « relais »: { <port>: { "ecrire": <nœud>, "lire": <nœud> } }. Le
# nœud « ecrire » est posé en queue de chaque run sauf le dernier, branché sur
# ce port du dernier fragment du tour (marqueur « $relais.port ») et nommé
# d'après le run (« $relais.prefixe ») ; le nœud « lire » est posé au début du
# run suivant, sur le fichier que la passerelle lui a confié (« $relais.fichier »),
# et c'est lui que vise « $precedent.<port> » quand le précédent est dans
# l'autre run. Les nœuds eux-mêmes sont ceux de la recette : rien ici ne
# nomme un type de nœud.

RELAIS = "relais"
RELAIS_PORT = "$relais.port"
RELAIS_FICHIER = "$relais.fichier"
RELAIS_PREFIXE = "$relais.prefixe"


class _Fenetre:
    """Ce que le run du tour demandé garde du montage, et ses relais."""

    def __init__(self, fragments: list[Fragment], tour_seul: int,
                 relais_fichiers: dict[str, str] | None, prefixe_relais: str | None) -> None:
        from ..core.blocs import UN_RUN_PAR_TOUR
        dedans = [i for i, f in enumerate(fragments)
                  if isinstance(f.boucle, dict) and f.boucle.get(UN_RUN_PAR_TOUR)]
        if not dedans:
            raise WorkflowMappingError(
                "montage : « tour » demandé alors qu'aucune boucle ne déclare « un_run_par_tour »")
        self.pour = fragments[dedans[0]].boucle
        self.total = fragments[dedans[0]].tours_total
        if not 0 <= tour_seul < self.total:
            raise WorkflowMappingError(
                f"montage : tour {tour_seul} demandé, la boucle en compte {self.total} "
                f"(0 à {self.total - 1})")
        self.tour = tour_seul
        self.declares: dict[str, Any] = dict(self.pour.get(RELAIS) or {})
        self.fichiers = dict(relais_fichiers or {})
        self.prefixe = prefixe_relais or "cortex/relais"
        debut, fin = dedans[0], dedans[-1] + 1
        avant = [(f.nom, f.tour) for f in fragments[:debut]]
        apres = [(f.nom, f.tour) for f in fragments[fin:]]
        du_tour = [(f.nom, f.tour) for f in fragments[debut:fin] if f.tour == tour_seul]
        self.dernier = du_tour[-1]
        garde = set(du_tour)
        contenus = {(f.nom, f.tour): f.contenu for f in fragments}
        # Ce qui est avant la boucle et que PERSONNE ne cite par son nom est une
        # amorce : elle se joue au premier run. Ce qui est cité (« commun »,
        # « modele », « texte » — posé une fois, pour ceux qui le citent) ne se
        # pose que dans les runs qui le citent, de proche en proche : un run
        # d'encodage ne charge pas le modèle, un run de rendu pas l'encodeur.
        cites_quelque_part: set[str] = set()
        for contenu in contenus.values():
            cites_quelque_part |= _noms_cites(contenu or {})
        if tour_seul == 0:
            garde |= {a for a in avant if a[0] not in cites_quelque_part}
        if tour_seul == self.total - 1:
            garde |= set(apres)
        # Les fragments d'avant que le run cite par leur nom, de proche en
        # proche : ils sont posés dans CHAQUE run.
        par_nom = {f.nom: (f.nom, f.tour) for f in fragments[:debut]}
        a_voir = list(garde)
        while a_voir:
            for nom in _noms_cites(contenus.get(a_voir.pop()) or {}):
                cible = par_nom.get(nom)
                if cible is not None and cible not in garde:
                    garde.add(cible)
                    a_voir.append(cible)
        self.garde = garde
        self.lus: dict[str, tuple[str, int]] = {}      # port -> (numéro, sortie) du nœud « lire »
        self.a_ecrire = tour_seul < self.total - 1
        self.graphe: dict[str, Any] = {}               # le graphe du run, où les relais se posent

    def franchit(self, moi: tuple[str, int], vise: tuple[str, int]) -> bool:
        """« $precedent » qui, depuis le premier fragment du tour, vise le tour d'avant."""
        return moi[1] == self.tour and vise[1] == self.tour - 1 and vise not in self.garde


def _noms_cites(contenu: dict[str, Any]) -> set[str]:
    """Les fragments qu'un contenu vise par leur NOM (« $commun.x »), hors « $precedent ».

    Les deux branches d'un « $si » comptent : laquelle sera prise ne se sait
    qu'au montage, et un run doit garder ce que l'une ou l'autre cite.
    """
    noms: set[str] = set()

    def voir(valeur: Any) -> None:
        if isinstance(valeur, dict) and SI in valeur:
            voir(valeur.get(ALORS))
            voir(valeur.get(SINON))
            return
        if _est_lien(valeur) and valeur[0].startswith(PREFIXE):
            corps = valeur[0][len(PREFIXE):]
            nom = corps.split(".", 1)[0].partition("#")[0]
            if nom != PRECEDENT:
                noms.add(nom)

    for noeud in contenu.values():
        for valeur in ((noeud or {}).get("inputs") or {}).values():
            voir(valeur)
    return noms


def _recette_relais(fenetre: _Fenetre, port: str, role: str) -> dict[str, Any]:
    declare = fenetre.declares.get(port)
    if not isinstance(declare, dict) or not isinstance(declare.get(role), dict) \
            or "class_type" not in declare[role]:
        raise WorkflowMappingError(
            f"montage : le relais {port!r} n'a pas de nœud « {role} » "
            f"(attendu {{\"ecrire\": <nœud>, \"lire\": <nœud>}})",
            available=sorted(fenetre.declares))
    return declare[role]


def _noeud_lire(fenetre: _Fenetre, port: str, suivant: list[int]) -> tuple[str, int]:
    """Le nœud qui relit ce port au début du run — posé une fois par port."""
    if port in fenetre.lus:
        return fenetre.lus[port]
    if port not in fenetre.declares:
        raise WorkflowMappingError(
            f"montage : « $precedent.{port} » traverse un run, mais aucun relais "
            f"ne porte {port!r} — le déclarer dans « relais » de la boucle",
            available=sorted(fenetre.declares))
    recette = _recette_relais(fenetre, port, "lire")
    fichier = fenetre.fichiers.get(port)
    if not fichier:
        # Le numéro est celui du JOURNAL du parent (« tour 3/4 », 1-based) :
        # `fenetre.tour` compte depuis zéro, et « le tour 2 relit… » désignait
        # le troisième run (2026-09-19).
        raise WorkflowMappingError(
            f"montage : le tour {fenetre.tour + 1} relit le relais {port!r}, et aucun fichier "
            f"ne lui a été confié (« relais_{port} »)")
    entrees = {champ: (fichier if valeur == RELAIS_FICHIER else valeur)
               for champ, valeur in (recette.get("inputs") or {}).items()}
    numero = str(suivant[0])
    suivant[0] += 1
    fenetre.lus[port] = (numero, int(fenetre.declares[port].get("sortie", 0)))
    fenetre.graphe[numero] = {**{k: v for k, v in recette.items() if k != "inputs"},
                              "inputs": entrees}
    return fenetre.lus[port]


def _noeuds_ecrire(fenetre: _Fenetre, table: dict[tuple[str, int], dict[str, str]],
                   sorties: dict[tuple[str, int], dict[str, str]], suivant: list[int]) -> None:
    """Les nœuds qui écrivent les relais en queue du run — un par port déclaré
    que le dernier fragment du run OFFRE. Avec des phases, chaque run finit sur
    un fragment différent (l'encodage offre le conditionnement, la livraison la
    dernière image) : ce qu'il n'offre pas n'a pas à être écrit, et le run qui
    le lirait le dira si le fichier manque. Un run qui n'écrirait AUCUN relais
    déclaré est une faute : rien ne passerait au suivant."""
    offerts = sorties.get(fenetre.dernier) or {}
    ecrits = 0
    for port in fenetre.declares:
        if port not in offerts:
            continue
        recette = _recette_relais(fenetre, port, "ecrire")
        local = _resoudre_nom(port, fenetre.dernier, sorties)
        if local not in table[fenetre.dernier]:
            raise WorkflowMappingError(
                f"montage : le relais {port!r} doit être écrit depuis {fenetre.dernier[0]!r} "
                f"(tour {fenetre.dernier[1]}), dont la sortie {local!r} n'existe pas",
                available=sorted(table[fenetre.dernier]))
        ecrits += 1
        source = table[fenetre.dernier][local]
        entrees: dict[str, Any] = {}
        for champ, valeur in (recette.get("inputs") or {}).items():
            if valeur == RELAIS_PORT:
                entrees[champ] = [source, 0]
            elif _est_lien(valeur) and valeur[0] == RELAIS_PORT:
                entrees[champ] = [source, valeur[1]]
            elif valeur == RELAIS_PREFIXE:
                entrees[champ] = f"{fenetre.prefixe}_relais_{port}"
            else:
                entrees[champ] = valeur
        numero = str(suivant[0])
        suivant[0] += 1
        fenetre.graphe[numero] = {**{k: v for k, v in recette.items() if k != "inputs"},
                                  "inputs": entrees}
    if fenetre.declares and not ecrits:
        raise WorkflowMappingError(
            f"montage : le run du tour {fenetre.tour} finit sur {fenetre.dernier[0]!r}, qui "
            f"n'offre aucun des relais déclarés ({', '.join(sorted(fenetre.declares))}) — "
            f"rien ne passerait au run suivant",
            available=sorted(offerts))


def assembler(fragments: list[Fragment],
              constantes: dict[str, Any] | None = None,
              blocs: list[str] | None = None,
              tour_seul: int | None = None,
              relais_fichiers: dict[str, str] | None = None,
              prefixe_relais: str | None = None) -> dict[str, Any]:
    """Le graphe API que le moteur recevra.

    ``blocs`` nomme les fragments qui PRODUISENT un morceau de la vidéo (l'amorce
    et le segment répété, par exemple). Chaque instance de ces fragments voit
    alors son rang dans le montage et le nombre total de morceaux — ``bloc_rang``
    et ``blocs_total`` dans la portée des calculs. C'est ce qui permet à un bloc
    de savoir où il en est dans le récit : le tour de boucle ne le dit pas
    (l'amorce est hors boucle, et le premier tour vaut 0 alors qu'il est le
    deuxième morceau).

    ``tour_seul`` ne recoud que la fenêtre d'UN run d'une boucle à un run par
    tour (voir en tête de section) ; les rangs, les totaux et les numéros de
    nœuds restent ceux du montage entier, pour que chaque run dise la même
    chose que le montage d'un seul tenant. ``relais_fichiers`` donne, par port,
    le fichier que le run relit ; ``prefixe_relais`` nomme ceux qu'il écrit.
    """
    if not fragments:
        raise WorkflowMappingError("montage vide : aucun fragment à assembler")

    blocs = list(blocs or [])
    rangs: dict[tuple[str, int], int] = {}
    for f in fragments:
        if f.nom in blocs:
            rangs[(f.nom, f.tour)] = len(rangs)
    # Un nom absent n'est une faute que s'ils le sont TOUS : un bloc de boucle
    # nommé ici n'existe pas quand la durée demandée tient dans l'amorce (zéro
    # tour), et la recette n'a pas à s'écrire autrement pour ce cas-là.
    presents = {f.nom for f in fragments}
    inconnus = [b for b in blocs if b not in presents]
    if blocs and len(inconnus) == len(blocs):
        raise WorkflowMappingError(
            f"montage : « blocs » nomme {inconnus[0]!r}, qui n'est pas un fragment du montage",
            available=sorted(presents))

    ordre = _instances(fragments)
    sorties = _sorties(fragments)
    doublons = [x for x in ordre if ordre.count(x) > 1]
    if doublons:
        raise WorkflowMappingError(
            f"montage : {doublons[0][0]!r} apparaît deux fois au même tour ; "
            f"un fragment posé plusieurs fois doit l'être par une boucle")
    table = _numeroter(fragments)
    fenetre = (_Fenetre(fragments, int(tour_seul), relais_fichiers, prefixe_relais)
               if tour_seul is not None else None)
    # Les numéros libres après ceux du montage entier : les nœuds de relais
    # s'y posent sans jamais heurter un numéro d'un autre run.
    suivant = [1 + sum(len(f.contenu or {}) for f in fragments)]

    graphe: dict[str, Any] = fenetre.graphe if fenetre is not None else {}
    for f in fragments:
        moi = (f.nom, f.tour)
        if fenetre is not None and moi not in fenetre.garde:
            continue
        for local, noeud in (f.contenu or {}).items():
            if not isinstance(noeud, dict) or "class_type" not in noeud:
                raise WorkflowMappingError(
                    f"fragment {f.nom!r} : le nœud {local!r} n'a pas de « class_type »")
            copie = {k: v for k, v in noeud.items() if k != "inputs"}
            # Le numéro de tour est dans la portée : un bloc répété doit
            # pouvoir placer quelque chose plus loin à chaque répétition.
            portee = dict(constantes or {}, tour=f.tour, tours_total=f.tours_total)
            # Avec des phases, le bloc (le tour de boucle proprement dit)
            # et la phase : « rang = bloc » pour la direction du bloc.
            phases = int((f.boucle or {}).get("phases", 1) or 1) if isinstance(f.boucle, dict) else 1
            portee["bloc"] = f.tour // phases
            portee["phase"] = f.tour % phases
            if moi in rangs:
                portee["bloc_rang"] = rangs[moi]
                portee["blocs_total"] = len(rangs)
            entrees = {}
            for champ, valeur in (noeud.get("inputs") or {}).items():
                ou = f"fragment {f.nom!r} (tour {f.tour}), nœud {local!r}, entrée {champ!r}"
                # Une entrée décidée au montage : la branche retenue est une
                # entrée ordinaire, et peut elle-même se décider.
                while isinstance(valeur, dict) and SI in valeur:
                    valeur = _choisir(valeur, portee, ou)
                if _est_lien(valeur):
                    entrees[champ] = _lien(valeur, moi, ordre, table, sorties, fenetre, suivant)
                else:
                    entrees[champ] = _constante(valeur, portee, ou)
            copie["inputs"] = entrees
            graphe[table[moi][str(local)]] = copie
    if fenetre is not None and fenetre.a_ecrire:
        _noeuds_ecrire(fenetre, table, sorties, suivant)
    return graphe


def _lien(valeur: list, moi: tuple[str, int], ordre: list[tuple[str, int]],
          table: dict[tuple[str, int], dict[str, str]],
          sorties: dict[tuple[str, int], dict[str, str]],
          fenetre: _Fenetre | None, suivant: list[int]) -> list:
    """Un lien recousu — vers le montage entier, ou, dans la fenêtre d'un run,
    vers ce que le run garde, ou vers le relais quand il franchit un run."""
    reference = valeur[0]
    if fenetre is None or not reference.startswith(PREFIXE):
        return [_cible(reference, moi, ordre, table, sorties), valeur[1]]
    corps = reference[len(PREFIXE):]
    nom, _, local = corps.partition(".")
    if nom == PRECEDENT:
        position = ordre.index(moi)
        vise = ordre[position - 1] if position > 0 else None
        if vise is not None and vise not in fenetre.garde:
            if fenetre.franchit(moi, vise):
                numero, sortie = _noeud_lire(fenetre, local, suivant)
                return [numero, sortie]
            raise WorkflowMappingError(
                f"fragment {moi[0]!r} (tour {moi[1]}) : « $precedent » vise {vise[0]!r} "
                f"(tour {vise[1]}), qui n'est pas dans le run du tour {fenetre.tour}")
    elif (nom.partition("#")[0], 0) not in fenetre.garde and (nom, 0) in table:
        raise WorkflowMappingError(
            f"fragment {moi[0]!r} (tour {moi[1]}) : référence à {nom!r}, qui n'est pas "
            f"dans le run du tour {fenetre.tour}",
            available=sorted({n for n, _ in fenetre.garde}))
    return [_cible(reference, moi, ordre, table, sorties), valeur[1]]

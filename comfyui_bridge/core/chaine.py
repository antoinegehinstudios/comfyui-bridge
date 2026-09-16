"""Une chaîne : plusieurs workflows enchaînés, et ce qui les relie.

Un workflow répond à une demande d'un seul tenant. Beaucoup de livrables n'en
sont pas un : une révélation PUIS sa fermeture, un plan PUIS son prolongement.
Écrire cet enchaînement chez l'appelant lui redonnait la logique métier —
l'ordre des étapes, la reprise des fichiers, les contrôles — que la passerelle
existe justement pour tenir.

Ce module est le CŒUR de la chose, et il ne connaît rien du dehors : ni moteur,
ni ffmpeg, ni HTTP. Il sait lire une définition, refuser ce qui ne tient pas,
résoudre les renvois d'une étape à l'autre, et dire si un contrôle passe. Ce
qui EXÉCUTE vit dans l'adaptateur.

Les renvois s'écrivent ``$champ`` (une valeur exposée) ou ``$etape.cle`` (un
résultat d'étape PRÉCÉDENTE). Un renvoi vers l'aval, ou vers un nom qui
n'existe pas, est refusé À LA LECTURE, avec le nom : découvert à l'exécution,
il faisait échouer la chaîne après avoir dépensé les étapes d'avant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .errors import InputValueRefusedError, UnknownWorkflowInputError, WorkflowMappingError
from .intention import RenderIntent, media_category

# Ce qu'une étape sait faire. Chaque genre est une opération que la passerelle
# tient déjà ou qu'elle porte pour la chaîne ; il n'y a pas de genre « exécuter
# n'importe quoi » — une chaîne décrit un enchaînement, pas un script.
GENRES: tuple[str, ...] = (
    "rendre",            # un run de workflow, par les moyens ordinaires
    "extraire_queue",    # les N dernières images d'une vidéo, en clip
    "extraire_image",    # une image d'une vidéo
    "recoller",          # joindre des parts en un livrable
    "mesurer_raccords",  # la ressemblance de part en part, aux frontières
    "verifier",          # des contrôles sur ce qui a été mesuré
)

# Les clés qu'un genre accepte. Un paramètre inconnu est une faute de la
# définition, pas une option ignorée : le taire laissait une étape tourner sans
# ce que son auteur croyait avoir demandé.
_CLES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # genre -> (clés admises, clés requises)
    # « rendre » nomme SOIT un workflow, SOIT un rôle + la technique qui le
    # tient : le couple exact est vérifié à part (voir `_etape`), parce que
    # « l'un ou l'autre » ne s'écrit pas dans une liste de clés requises.
    "rendre": (frozenset({"workflow", "media", "role", "technique"})
               | frozenset(RenderIntent.__dataclass_fields__), frozenset()),
    "extraire_queue": (frozenset({"video", "images"}), frozenset({"video", "images"})),
    "extraire_image": (frozenset({"video", "position"}), frozenset({"video"})),
    "recoller": (frozenset({"parts", "fps", "largeur", "hauteur", "chevauchement"}),
                 frozenset({"parts"})),
    "mesurer_raccords": (frozenset({"parts", "chevauchement"}), frozenset({"parts"})),
}

_OPS: tuple[str, ...] = ("eq", "ne", "lte", "gte", "between", "exists")

_TYPES: tuple[str, ...] = ("INT", "FLOAT", "STRING", "COMBO", "BOOLEAN")

# D'où une liste de choix peut venir quand la chaîne ne l'écrit pas. Une chaîne
# qui recopie une liste la fige au jour où on l'a écrite : elle dit d'où elle
# vient, et le fournisseur reste la source.
_SOURCES_D_OPTIONS: tuple[str, ...] = (
    "catalogue",   # les entrées PUBLIÉES du catalogue, filtrées
    "menu",        # les valeurs d'un menu déclaré (table écrite, ou fichier projeté)
    "techniques",  # les TECHNIQUES déclarées : une de plus est un fichier de plus
)


@dataclass(frozen=True)
class Champ:
    """Un réglage que la chaîne expose à l'appelant.

    C'est le contrat du formulaire : ce qu'on peut envoyer à la racine du corps
    d'une intention, avec ses bornes. Une pièce jointe (``media``) est un champ
    comme un autre — elle porte un nom de fichier déposé chez le moteur.
    """

    nom: str
    media: str | None = None
    type: str = "STRING"
    defaut: Any = None
    minimum: float | None = None
    maximum: float | None = None
    pas: float | None = None
    options: tuple[Any, ...] | None = None
    # D'où viennent les options quand ce n'est pas une liste écrite : « catalogue »
    # laisse la passerelle les remplir depuis ses propres entrées publiées,
    # « menu » depuis la table d'un menu déclaré (qui peut elle-même être la
    # projection d'un fichier que tient un fournisseur).
    options_depuis: Any = None
    libelle: str = ""
    unite: str | None = None
    requis: bool = False


@dataclass(frozen=True)
class Etape:
    id: str
    genre: str
    params: Any                       # dict, ou liste de contrôles pour « verifier »
    # « quand » : un RENVOI (« $cta ») ; l'étape n'est jouée que si ce qu'il
    # désigne n'est pas vide. Sautée, elle rend son média tel quel en livrable,
    # pour que l'aval qui la nomme continue de tenir (voir l'adaptateur).
    quand: str = ""
    # « sinon » : ce que l'étape rend QUAND ELLE EST SAUTÉE, par-dessus ce
    # passe-plat. Un aval qui lit « $appel.recit.images_reprises » doit trouver
    # un nombre même si l'appel n'a pas eu lieu — sans cela, le renvoi restait
    # non résolu et le montage échouait à cause d'une étape qu'on avait
    # justement choisi de ne pas jouer.
    sinon: dict[str, Any] = field(default_factory=dict)

    @property
    def workflow(self) -> str | None:
        """Le graphe que cette étape lance, quand la chaîne le NOMME.

        ``None`` pour une étape à ``role`` : ce n'est pas un oubli, c'est le
        sujet — la chaîne ne connaît pas le graphe, c'est la technique choisie
        à l'appel qui dit lequel tient ce rôle.
        """
        if self.genre == "rendre" and isinstance(self.params, dict):
            valeur = self.params.get("workflow")
            return str(valeur) if valeur else None
        return None

    @property
    def role(self) -> str | None:
        """Le rôle que cette étape fait tenir (« deroulement »), s'il y en a un."""
        if self.genre == "rendre" and isinstance(self.params, dict):
            valeur = self.params.get("role")
            return str(valeur) if valeur else None
        return None

    @property
    def technique(self) -> str | None:
        """Le RENVOI qui désigne la technique à employer (« $technique »).

        Porté aussi bien par un « rendre » à rôle que par un « verifier » dont
        les contrôles appartiennent à la technique.
        """
        if isinstance(self.params, dict):
            valeur = self.params.get("technique")
            return str(valeur) if valeur else None
        return None

    @property
    def controles_nommes(self) -> str | None:
        """Le NOM de la liste de contrôles à prendre chez la technique."""
        if self.genre == "verifier" and isinstance(self.params, dict):
            valeur = self.params.get("controles")
            return str(valeur) if valeur else None
        return None


@dataclass(frozen=True)
class Role:
    """Ce qu'une technique met derrière un rôle de la chaîne : un graphe, et ce
    qu'il reçoit."""

    nom: str
    workflow: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Technique:
    """UNE façon de tenir les rôles d'une chaîne — la peinture, ici.

    Une technique de plus est un FICHIER de plus : ni la chaîne ni le code ne
    la nomment. C'est ce qui évite qu'une chaîne se fasse doublon pour peindre
    autrement (Antoine, 2026-09-16).
    """

    nom: str
    version: int = 1
    libelle: str = ""
    resume: str = ""
    champs: dict[str, Champ] = field(default_factory=dict)
    roles: dict[str, Role] = field(default_factory=dict)
    controles: dict[str, tuple] = field(default_factory=dict)
    # La technique employée quand l'appelant n'en nomme aucune. C'est la
    # TECHNIQUE qui se dit par défaut, pas la chaîne qui la nomme : une chaîne
    # qui écrirait « encre » dans son champ porterait la dépendance qu'on lui
    # refuse (Antoine, 2026-09-16 : les techniques ne vivent pas dans le
    # workflow, elles s'y réconcilient quand le paramètre les appelle).
    par_defaut: bool = False


@dataclass(frozen=True)
class Chaine:
    nom: str
    version: int = 1
    resume: str = ""
    champs: dict[str, Champ] = field(default_factory=dict)
    etapes: tuple[Etape, ...] = ()
    livrable: str = ""

    @property
    def rendus(self) -> tuple[Etape, ...]:
        return tuple(e for e in self.etapes if e.genre == "rendre")

    @property
    def champ_de_technique(self) -> str | None:
        """Le champ par lequel l'appelant CHOISIT la technique, s'il y en a un.

        Reconnu à sa source d'options (« techniques »), pas à son nom : c'est
        la déclaration qui fait foi, et une chaîne qui appellerait ce champ
        autrement resterait comprise.
        """
        for nom, champ in self.champs.items():
            if isinstance(champ.options_depuis, dict) and champ.options_depuis.get("techniques"):
                return nom
        return None


# -- lecture ------------------------------------------------------------------


def _options_depuis(nom: str, brut: Any, contexte: str) -> Any:
    """D'où la liste d'un menu vient, quand la chaîne ne l'écrit pas.

    ``{"catalogue": {…}}`` : les entrées publiées du catalogue, filtrées.
    ``{"menu": "<nom>"}`` : les valeurs du menu déclaré à la passerelle — qui
    peut lui-même n'être que la projection d'un fichier tenu par un fournisseur
    (les structures de récit vivent dans le paquet qui les sert). Un objet qui
    ne nomme aucune source reste un filtre de catalogue, la forme d'origine.

    Ce qui est refusé ici est ce qu'une lecture peut prouver : deux sources à
    la fois (laquelle l'emporterait ?), un menu sans nom. Découvert plus tard,
    ce serait un formulaire aux choix silencieusement vides.
    """
    if brut is None:
        return None
    if not isinstance(brut, dict):
        raise WorkflowMappingError(
            f"{contexte} : « options_depuis » de {nom!r} doit être un objet "
            f"({', '.join(_SOURCES_D_OPTIONS)})")
    nommees = [source for source in _SOURCES_D_OPTIONS if source in brut]
    if len(nommees) > 1:
        raise WorkflowMappingError(
            f"{contexte} : {nom!r} nomme deux sources d'options à la fois "
            f"({', '.join(nommees)}) — une seule peut faire la liste")
    if "menu" in brut and not str(brut["menu"]).strip():
        raise WorkflowMappingError(
            f"{contexte} : {nom!r} tire ses options d'un « menu » sans le nommer")
    return brut


def _champ(nom: str, brut: Any, contexte: str) -> Champ:
    if not isinstance(brut, dict):
        raise WorkflowMappingError(f"{contexte} : le champ exposé {nom!r} doit être un objet")
    categorie = brut.get("media")
    if categorie is not None:
        if not media_category(str(categorie)):
            raise WorkflowMappingError(
                f"{contexte} : {nom!r} déclare une catégorie de média inconnue "
                f"({categorie!r})")
        return Champ(nom=nom, media=str(categorie), type="STRING",
                     libelle=str(brut.get("libelle") or nom),
                     requis=bool(brut.get("requis", False)))
    genre = str(brut.get("type", "STRING")).upper()
    if genre not in _TYPES:
        raise WorkflowMappingError(
            f"{contexte} : {nom!r} déclare le type {genre!r}, hors de "
            f"{', '.join(_TYPES)}")
    options = brut.get("options")
    if options is not None and not isinstance(options, list):
        raise WorkflowMappingError(f"{contexte} : « options » de {nom!r} doit être une liste")
    return Champ(
        nom=nom, type=genre, defaut=brut.get("defaut"),
        minimum=brut.get("min"), maximum=brut.get("max"), pas=brut.get("step"),
        options=tuple(options) if options is not None else None,
        options_depuis=_options_depuis(nom, brut.get("options_depuis"), contexte),
        libelle=str(brut.get("libelle") or nom),
        unite=brut.get("unite"),
        requis=bool(brut.get("requis", False)),
    )


def _renvoi(valeur: Any) -> bool:
    return isinstance(valeur, str) and valeur.startswith("$")


def _controles(params: Any, contexte: str, ident: str) -> tuple:
    """Une liste de contrôles, vérifiée : chacun dit son opérateur et ce qu'il
    mesure. Un contrôle sans opérateur ne juge rien, et le taire faisait passer
    une étape « verifier » pour un feu vert."""
    if not isinstance(params, list) or not params:
        raise WorkflowMappingError(f"{contexte} : {ident!r} attend une liste de contrôles")
    for controle in params:
        if not isinstance(controle, dict):
            raise WorkflowMappingError(f"{contexte} : un contrôle de {ident!r} doit être un objet")
        op = str(controle.get("op") or "")
        if op not in _OPS:
            raise WorkflowMappingError(
                f"{contexte} : contrôle {controle.get('id')!r} de {ident!r} : "
                f"l'opérateur {op!r} est hors de {', '.join(_OPS)}")
        if "valeur" not in controle:
            raise WorkflowMappingError(
                f"{contexte} : contrôle {controle.get('id')!r} de {ident!r} : "
                f"« valeur » manquante")
    return tuple(params)


def _controles_de_technique(params: dict[str, Any], ident: str, chaine: str) -> None:
    """« verifier » qui emprunte sa liste à la technique choisie."""
    inconnues = sorted(set(params) - {"technique", "controles"})
    if inconnues:
        raise WorkflowMappingError(
            f"{contexte} : « verifier » de {ident!r} — clé(s) inconnue(s) : "
            f"{', '.join(inconnues)} (attendu : « technique » et « controles »)")
    if not _renvoi(params.get("technique")):
        raise WorkflowMappingError(
            f"{contexte} : « verifier » de {ident!r} — « technique » attend un renvoi "
            f"(« $technique »), pas {params.get('technique')!r}")
    if not str(params.get("controles") or "").strip():
        raise WorkflowMappingError(
            f"{contexte} : « verifier » de {ident!r} — « controles » doit nommer la "
            f"liste à prendre chez la technique")


def _rendre_nomme_sa_cible(params: dict[str, Any], ident: str, chaine: str) -> None:
    """Un « rendre » nomme un WORKFLOW, ou un RÔLE et la technique qui le tient.

    Jamais les deux : lequel l'emporterait ? Et jamais un rôle sans technique —
    un rôle seul ne désigne aucun graphe, et l'étape n'aurait rien à lancer.
    """
    par_role = {"role", "technique"} & set(params)
    if "workflow" in params and par_role:
        raise WorkflowMappingError(
            f"{contexte} : étape {ident!r} nomme à la fois un « workflow » et "
            f"{' et '.join(sorted(par_role))} — l'un OU l'autre, jamais les deux")
    if not par_role:
        if not str(params.get("workflow") or "").strip():
            raise WorkflowMappingError(
                f"{contexte} : étape {ident!r} (rendre) — il manque « workflow », "
                f"ou « role » + « technique »")
        return
    manquantes = sorted({"role", "technique"} - set(params))
    if manquantes:
        raise WorkflowMappingError(
            f"{contexte} : étape {ident!r} (rendre) — {', '.join(manquantes)} "
            f"manque : un rôle ne désigne un graphe qu'avec la technique qui le tient")
    if not str(params.get("role") or "").strip():
        raise WorkflowMappingError(
            f"{contexte} : étape {ident!r} (rendre) — « role » ne nomme rien")
    if not _renvoi(params.get("technique")):
        raise WorkflowMappingError(
            f"{contexte} : étape {ident!r} (rendre) — « technique » attend un renvoi "
            f"(« $technique »), pas {params.get('technique')!r} : la technique est CHOISIE "
            f"à l'appel, jamais écrite dans le plan")


def _etape(brut: Any, rang: int, chaine: str) -> Etape:
    if not isinstance(brut, dict):
        raise WorkflowMappingError(f"chaîne {chaine!r} : l'étape n°{rang} doit être un objet")
    ident = str(brut.get("id") or "").strip()
    if not ident:
        raise WorkflowMappingError(f"chaîne {chaine!r} : l'étape n°{rang} n'a pas d'« id »")
    genres = [g for g in GENRES if g in brut]
    if len(genres) != 1:
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : l'étape {ident!r} doit porter exactement un genre parmi "
            f"{', '.join(GENRES)} (trouvé : {', '.join(genres) or 'aucun'})")
    genre = genres[0]
    params = brut[genre]
    # UNE ÉTAPE FACULTATIVE DIT DE QUOI ELLE DÉPEND : « quand » est un renvoi,
    # jamais une valeur écrite en dur (une étape qu'on veut toujours sauter
    # n'a pas à exister). Demandé le 2026-09-15 : « si pas de CTA spécifié, on
    # ne met pas de CTA — on saute l'étape ».
    quand = brut.get("quand", "")
    if quand is not None and quand != "":
        if not isinstance(quand, str) or not quand.startswith("$"):
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : étape {ident!r} — « quand » attend un renvoi "
                f"(« $champ » ou « $etape.cle »), pas {quand!r}")
    else:
        quand = ""
    sinon = brut.get("sinon")
    if sinon is None:
        sinon = {}
    elif not isinstance(sinon, dict):
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : étape {ident!r} — « sinon » décrit ce que l'étape rend "
            f"quand elle est sautée : un objet, pas {sinon!r}")
    if genre == "verifier":
        # DEUX FORMES : la liste écrite ici, ou le NOM d'une liste que la
        # technique choisie porte. Sans la seconde, une chaîne qui veut juger
        # deux peintures différentes devait se dédoubler pour porter les deux
        # listes (Antoine, 2026-09-16).
        if isinstance(params, dict):
            _controles_de_technique(params, ident, chaine)
            return Etape(id=ident, genre=genre, params=params, quand=quand,
                 sinon=dict(sinon))
        if not isinstance(params, list) or not params:
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : « verifier » de {ident!r} attend une liste de contrôles, "
                f"ou {{\"technique\": \"$…\", \"controles\": \"<nom>\"}}")
        _controles(params, f"chaîne {chaine!r}", ident)
        return Etape(id=ident, genre=genre, params=params, quand=quand,
                 sinon=dict(sinon))
    if not isinstance(params, dict):
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : « {genre} » de {ident!r} attend un objet de paramètres")
    if genre == "rendre":
        _rendre_nomme_sa_cible(params, ident, chaine)
    admises, requises = _CLES[genre]
    inconnues = sorted(set(params) - admises)
    if inconnues:
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : étape {ident!r} ({genre}) — paramètre(s) inconnu(s) : "
            f"{', '.join(inconnues)}")
    manquantes = sorted(requises - set(params))
    if manquantes:
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : étape {ident!r} ({genre}) — paramètre(s) requis absent(s) : "
            f"{', '.join(manquantes)}")
    return Etape(id=ident, genre=genre, params=params, quand=quand,
                 sinon=dict(sinon))


def lire(brut: Any, nom_declare: str | None = None) -> Chaine:
    """Lire une définition de chaîne, et refuser tout ce qui ne tient pas."""
    if not isinstance(brut, dict):
        raise WorkflowMappingError("une chaîne est un objet JSON")
    nom = str(brut.get("chaine") or nom_declare or "").strip()
    if not nom:
        raise WorkflowMappingError("cette chaîne ne se nomme pas (clé « chaine »)")
    champs = {str(k): _champ(str(k), v, f"chaîne {nom!r}")
              for k, v in (brut.get("expose") or {}).items()}
    brutes = brut.get("etapes")
    if not isinstance(brutes, list) or not brutes:
        raise WorkflowMappingError(f"chaîne {nom!r} : « etapes » doit être une liste non vide")
    etapes: list[Etape] = []
    vus: set[str] = set()
    for rang, e in enumerate(brutes, start=1):
        etape = _etape(e, rang, nom)
        if etape.id in vus:
            raise WorkflowMappingError(f"chaîne {nom!r} : deux étapes portent l'id {etape.id!r}")
        if etape.id in champs:
            raise WorkflowMappingError(
                f"chaîne {nom!r} : l'étape {etape.id!r} porte le nom d'un champ exposé — "
                f"un renvoi « ${etape.id} » ne saurait plus de quoi il parle")
        vus.add(etape.id)
        etapes.append(etape)
    chaine = Chaine(nom=nom, version=int(brut.get("version", 1)),
                    resume=str(brut.get("resume") or ""), champs=champs,
                    etapes=tuple(etapes), livrable=str(brut.get("livrable") or ""))
    _verifier_renvois(chaine)
    return chaine


def _verifier_renvois(chaine: Chaine) -> None:
    """Aucun renvoi ne vise l'aval, ni un nom qui n'existe pas.

    C'est la seule chose qu'une lecture peut prouver sans rien exécuter, et
    c'est celle qui coûte le plus cher à découvrir en route : une chaîne de
    cinq étapes qui échoue à la quatrième a déjà dépensé les trois premières.
    """
    amont: set[str] = set()
    for etape in chaine.etapes:
        for renvoi in list(renvois(etape.params)) + list(renvois(etape.quand)):
            tete = renvoi.split(".", 1)[0]
            if tete in chaine.champs or tete in amont:
                continue
            connus = sorted(set(chaine.champs) | amont)
            aval = tete in {e.id for e in chaine.etapes}
            raison = ("désigne une étape qui vient APRÈS" if aval
                      else "ne désigne ni un champ exposé ni une étape précédente")
            raise WorkflowMappingError(
                f"chaîne {chaine.nom!r} : étape {etape.id!r} — « ${renvoi} » {raison} "
                f"(connus ici : {', '.join(connus) or 'rien'})")
        amont.add(etape.id)
    if chaine.livrable:
        tete = chaine.livrable.lstrip("$").split(".", 1)[0]
        if tete not in amont and tete not in chaine.champs:
            raise WorkflowMappingError(
                f"chaîne {chaine.nom!r} : le livrable « {chaine.livrable} » ne désigne "
                f"aucune étape")


# -- techniques ---------------------------------------------------------------
#
# Une chaîne est un PLAN : ses étapes nomment des rôles (« deroulement »,
# « conclusion »), jamais un graphe de peinture. Une technique dit quel graphe
# tient chaque rôle, avec quelles entrées, quels réglages elle ajoute et quels
# contrôles elle porte. Décidé le 2026-09-16 : une chaîne qui nomme une façon de
# peindre en porte la DÉPENDANCE, et doit se faire doublon pour peindre
# autrement — deux chaînes jumelles se recopiaient alors à onze étapes près deux.
# La décision est citée en entier dans `adapter/techniques.py`, où elle n'est
# écrite qu'une fois.


def lire_technique(brut: Any, nom_declare: str | None = None) -> Technique:
    """Lire une technique, et refuser tout ce qu'une lecture peut prouver faux.

    Ce qui ne peut PAS se prouver ici : qu'un renvoi désigne quelque chose —
    une technique ignore quelle chaîne l'emploie et quelles étapes la
    précèdent. Cette vérification-là attend `verifier_techniques`, qui voit les
    deux ensemble.
    """
    if not isinstance(brut, dict):
        raise WorkflowMappingError("une technique est un objet JSON")
    nom = str(brut.get("technique") or nom_declare or "").strip()
    if not nom:
        raise WorkflowMappingError("cette technique ne se nomme pas (clé « technique »)")
    contexte = f"technique {nom!r}"
    champs = {str(k): _champ(str(k), v, contexte)
              for k, v in (brut.get("expose") or {}).items()}
    brutes = brut.get("roles")
    if not isinstance(brutes, dict) or not brutes:
        raise WorkflowMappingError(
            f"{contexte} : « roles » doit dire quel graphe tient chaque rôle de la chaîne")
    roles: dict[str, Role] = {}
    for cle, valeur in brutes.items():
        if not isinstance(valeur, dict):
            raise WorkflowMappingError(f"{contexte} : le rôle {str(cle)!r} doit être un objet")
        workflow = str(valeur.get("workflow") or "").strip()
        if not workflow:
            raise WorkflowMappingError(
                f"{contexte} : le rôle {str(cle)!r} ne nomme aucun « workflow » — c'est "
                f"pourtant la seule chose qu'une technique ait à dire d'un rôle")
        entrees = valeur.get("inputs") or {}
        if not isinstance(entrees, dict):
            raise WorkflowMappingError(
                f"{contexte} : « inputs » du rôle {str(cle)!r} doit être un objet "
                f"« <nœud>.<entrée> » → valeur")
        inconnues = sorted(set(valeur) - {"workflow", "inputs"})
        if inconnues:
            raise WorkflowMappingError(
                f"{contexte} : rôle {str(cle)!r} — clé(s) inconnue(s) : {', '.join(inconnues)}")
        roles[str(cle)] = Role(nom=str(cle), workflow=workflow, inputs=dict(entrees))
    listes = brut.get("controles") or {}
    if not isinstance(listes, dict):
        raise WorkflowMappingError(f"{contexte} : « controles » doit être un objet nom → liste")
    controles = {str(cle): _controles(valeur, contexte, str(cle))
                 for cle, valeur in listes.items()}
    par_defaut = brut.get("par_defaut", False)
    if not isinstance(par_defaut, bool):
        raise WorkflowMappingError(
            f"{contexte} : « par_defaut » est vrai ou faux, reçu {par_defaut!r}")
    return Technique(nom=nom, version=int(brut.get("version", 1)),
                     libelle=str(brut.get("libelle") or nom),
                     resume=str(brut.get("resume") or ""),
                     champs=champs, roles=roles, controles=controles,
                     par_defaut=par_defaut)


def verifier_techniques(chaine: Chaine, techniques: dict[str, Technique]) -> None:
    """Chaque technique tient-elle ce que CETTE chaîne lui demande ?

    Un rôle que la chaîne nomme et qu'une technique n'a pas, une liste de
    contrôles absente, un renvoi qui ne désigne rien à cet endroit du plan :
    tout cela se prouve dès que la chaîne et ses techniques sont lues ensemble,
    et coûte cher à découvrir en route — la peinture est la cinquième étape,
    les quatre premières sont déjà dépensées.
    """
    par_defaut = sorted(nom for nom, t in (techniques or {}).items() if t.par_defaut)
    if len(par_defaut) > 1:
        raise WorkflowMappingError(
            f"chaîne {chaine.nom!r} : {len(par_defaut)} techniques se disent par défaut "
            f"({', '.join(par_defaut)}) — une seule peut l'être")
    amont: set[str] = set()
    for etape in chaine.etapes:
        for nom, technique in (techniques or {}).items():
            connus = set(chaine.champs) | set(technique.champs) | amont
            if etape.role is not None:
                role = technique.roles.get(etape.role)
                if role is None:
                    raise WorkflowMappingError(
                        f"chaîne {chaine.nom!r} : l'étape {etape.id!r} demande le rôle "
                        f"{etape.role!r}, que la technique {nom!r} ne tient pas "
                        f"(elle tient : {', '.join(sorted(technique.roles)) or 'rien'})")
                _renvois_tiennent(role.inputs, connus,
                                  f"technique {nom!r}, rôle {etape.role!r}", chaine, etape)
            nommes = etape.controles_nommes
            if nommes is not None:
                liste = technique.controles.get(nommes)
                if liste is None:
                    raise WorkflowMappingError(
                        f"chaîne {chaine.nom!r} : l'étape {etape.id!r} demande les contrôles "
                        f"{nommes!r}, que la technique {nom!r} ne porte pas "
                        f"(elle porte : {', '.join(sorted(technique.controles)) or 'rien'})")
                _renvois_tiennent(liste, connus,
                                  f"technique {nom!r}, contrôles {nommes!r}", chaine, etape)
        amont.add(etape.id)


def _renvois_tiennent(valeur: Any, connus: set[str], contexte: str,
                      chaine: Chaine, etape: Etape) -> None:
    for renvoi in renvois(valeur):
        tete = renvoi.split(".", 1)[0]
        if tete in connus:
            continue
        aval = tete in {e.id for e in chaine.etapes}
        raison = ("désigne une étape qui vient APRÈS" if aval
                  else "ne désigne ni un champ de la technique, ni un champ de la chaîne, "
                       "ni une étape précédente")
        raise WorkflowMappingError(
            f"{contexte} : « ${renvoi} » {raison} à l'étape {etape.id!r} de "
            f"{chaine.nom!r} (connus ici : {', '.join(sorted(connus)) or 'rien'})")


def champs_admis(chaine: Chaine, techniques: dict[str, Technique] | None = None
                 ) -> dict[str, Champ]:
    """Tous les champs qu'un appelant peut nommer : ceux de la chaîne, plus ceux
    de TOUTES les techniques.

    Toutes, et pas seulement la technique choisie : un raccourci enregistré
    sous une technique doit pouvoir se rejouer sous une autre sans être refusé
    champ par champ. Ce qui n'appartient pas à la technique choisie est ÉCARTÉ et dit,
    jamais refusé (voir `valeurs`).
    """
    tous = dict(chaine.champs)
    for technique in (techniques or {}).values():
        for nom, champ in technique.champs.items():
            tous.setdefault(nom, champ)
    return tous


def champs_retenus(chaine: Chaine, technique: Technique | None = None) -> dict[str, Champ]:
    """Les champs qui s'APPLIQUENT vraiment : la chaîne, et la technique choisie.

    La technique l'emporte sur la chaîne à nom égal : deux techniques peuvent
    exposer « fond » avec des options différentes, et c'est celle qu'on emploie
    qui dit ce que « fond » accepte.
    """
    retenus = dict(chaine.champs)
    if technique is not None:
        retenus.update(technique.champs)
    return retenus


def technique_par_defaut(techniques: dict[str, Technique] | None) -> Technique | None:
    """La technique qui se dit par défaut — sinon la première, par son nom.

    Le défaut est DÉCLARÉ par une technique (« par_defaut »), jamais écrit dans
    la chaîne : la chaîne ne nomme aucune technique, pas même celle qu'on
    emploie quand on ne choisit pas. Deux qui se le disent : refusées à la
    lecture (`verifier_techniques`).
    """
    if not techniques:
        return None
    for technique in techniques.values():
        if technique.par_defaut:
            return technique
    return techniques[sorted(techniques)[0]]


def technique_choisie(chaine: Chaine, demande: dict[str, Any],
                      techniques: dict[str, Technique] | None = None) -> Technique | None:
    """La technique que cette demande emploie : celle qu'elle nomme, sinon le
    défaut que le champ porterait encore, sinon celle qui se dit par défaut."""
    techniques = techniques or {}
    champ = chaine.champ_de_technique
    if champ is None or not techniques:
        return None
    voulue = demande.get(champ)
    if voulue is None or voulue == "":
        voulue = chaine.champs[champ].defaut
    if voulue is None or voulue == "":
        return technique_par_defaut(techniques)
    return techniques.get(str(voulue))


# -- renvois ------------------------------------------------------------------


def renvois(valeur: Any) -> Iterator[str]:
    """Tous les « $… » d'une valeur, aussi profond qu'elle aille."""
    if isinstance(valeur, str):
        if valeur.startswith("$"):
            yield valeur[1:]
    elif isinstance(valeur, dict):
        for v in valeur.values():
            yield from renvois(v)
    elif isinstance(valeur, (list, tuple)):
        for v in valeur:
            yield from renvois(v)


_ABSENT = object()


def _lire_chemin(source: Any, chemin: list[str]) -> Any:
    for cle in chemin:
        if isinstance(source, dict) and cle in source:
            source = source[cle]
        else:
            return _ABSENT
    return source


def resoudre(valeur: Any, valeurs: dict[str, Any], resultats: dict[str, Any],
             strict: bool = True) -> Any:
    """Remplacer les renvois par ce qu'ils désignent.

    ``strict=False`` laisse tel quel ce qui n'est pas encore connu : c'est ce
    qu'il faut pour DÉCRIRE une chaîne avant de la lancer (aperçu, estimation),
    où les résultats d'étapes n'existent pas encore. Inventer une valeur là
    ferait mentir l'aperçu.
    """
    if isinstance(valeur, str) and valeur.startswith("$"):
        renvoi = valeur[1:]
        tete, _, reste = renvoi.partition(".")
        if not reste:
            if tete in valeurs:
                return valeurs[tete]
            trouve = resultats.get(tete, _ABSENT)
        else:
            source = resultats.get(tete, _ABSENT)
            trouve = _ABSENT if source is _ABSENT else _lire_chemin(source, reste.split("."))
        if trouve is _ABSENT:
            if strict:
                raise WorkflowMappingError(f"« {valeur} » n'a rien à désigner à ce moment")
            return valeur
        return trouve
    if isinstance(valeur, dict):
        return {k: resoudre(v, valeurs, resultats, strict) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [resoudre(v, valeurs, resultats, strict) for v in valeur]
    return valeur


# -- valeurs exposées ---------------------------------------------------------


def _nomme(champ: Champ) -> str:
    """Comment NOMMER un champ dans un refus : son libellé, puis sa clé.

    La clé seule (« duration_s ») ne dit pas quel champ du formulaire a été
    refusé ; le libellé seul (« Durée ») ne dit pas quoi corriger dans le corps
    envoyé. Les deux, une fois : « Durée (duration_s) ».
    """
    return f"{champ.libelle} ({champ.nom})" if champ.libelle and champ.libelle != champ.nom \
        else champ.nom


def _refus(champ: Champ, dit: str, **extras: Any) -> InputValueRefusedError:
    """Un refus qui porte la CLÉ refusée, et son libellé quand il en existe un.

    Sans ces extensions, un client devait relire la phrase pour savoir quel
    champ de son formulaire mettre en rouge.
    """
    if champ.libelle and champ.libelle != champ.nom:
        extras["libelle"] = champ.libelle
    return InputValueRefusedError(f"{_nomme(champ)} : {dit}", field=champ.nom, **extras)


def _nombre(champ: Champ, valeur: Any) -> Any:
    try:
        nombre = int(valeur) if champ.type == "INT" else float(valeur)
    except (TypeError, ValueError):
        raise _refus(champ, f"attend un nombre ({champ.type}), reçu {valeur!r}") from None
    if champ.minimum is not None and nombre < champ.minimum:
        raise _refus(champ, f"{nombre} est sous le minimum {champ.minimum}",
                     min=champ.minimum, max=champ.maximum)
    if champ.maximum is not None and nombre > champ.maximum:
        raise _refus(champ, f"{nombre} dépasse le maximum {champ.maximum}",
                     min=champ.minimum, max=champ.maximum)
    return nombre


def valeur_de(champ: Champ, brute: Any, options: tuple[Any, ...] | None = None) -> Any:
    """Une valeur reçue, ramenée à ce que le champ déclare accepter."""
    if champ.media is not None:
        return str(brute)
    if champ.type == "BOOLEAN":
        if isinstance(brute, bool):
            return brute
        if str(brute).strip().lower() in ("1", "true", "vrai", "oui", "on"):
            return True
        if str(brute).strip().lower() in ("0", "false", "faux", "non", "off"):
            return False
        raise _refus(champ, f"attend un booléen, reçu {brute!r}")
    if champ.type in ("INT", "FLOAT"):
        return _nombre(champ, brute)
    texte = str(brute)
    permises = options if options is not None else champ.options
    if champ.type == "COMBO" and permises and texte not in [str(o) for o in permises]:
        raise _refus(champ, f"{texte!r} n'est pas au menu", options=list(permises))
    return texte


def valeurs(chaine: Chaine, demande: dict[str, Any],
            options: dict[str, tuple[Any, ...]] | None = None,
            techniques: dict[str, Technique] | None = None
            ) -> tuple[dict[str, Any], list[str]]:
    """Ce que la chaîne va employer, à partir de ce que l'appelant a envoyé.

    Rend ``(valeurs, non_appliqués)``. Le défaut déclaré comble un champ absent ;
    un champ requis absent, une valeur hors bornes ou hors menu sont refusés ici
    — la passerelle fait autorité sur les valeurs, quoi qu'un client ait laissé
    passer.

    Les champs admis sont ceux de la chaîne ET de toutes les techniques, mais
    seuls ceux de la technique CHOISIE s'appliquent : un réglage d'une autre
    technique est écarté et RENDU À PART, jamais refusé. Sans cela, un raccourci
    enregistré sous une technique cassait dès qu'on le rejouait sous une autre,
    alors qu'il n'y a rien de faux à ce qu'il porte un réglage sans emploi ici.
    """
    options = options or {}
    techniques = techniques or {}
    admis = champs_admis(chaine, techniques)
    inconnus = sorted(k for k in demande if k not in admis)
    if inconnus:
        raise UnknownWorkflowInputError(
            f"chaîne {chaine.nom!r} : champ(s) qu'elle n'expose pas : {', '.join(inconnus)}",
            workflow=chaine.nom, fields=inconnus, accepts=sorted(admis))
    technique = technique_choisie(chaine, demande, techniques)
    retenus = champs_retenus(chaine, technique)
    non_appliques = sorted(k for k in demande if k not in retenus)
    sorties: dict[str, Any] = {}
    for nom, champ in retenus.items():
        brute = demande.get(nom)
        if brute is None or brute == "":
            if champ.requis:
                raise _refus(champ, f"est requis par la chaîne {chaine.nom!r}",
                             workflow=chaine.nom)
            if champ.defaut is None:
                continue
            brute = champ.defaut
        sorties[nom] = valeur_de(champ, brute, options.get(nom))
    # Le champ de technique n'a pas de défaut écrit dans la chaîne : c'est la
    # technique qui se dit par défaut qui le comble, ici, pour que les étapes
    # à rôle sachent laquelle elles emploient.
    champ_t = chaine.champ_de_technique
    if champ_t is not None and technique is not None and champ_t not in sorties:
        sorties[champ_t] = technique.nom
    return sorties, non_appliques


def defauts(chaine: Chaine, technique: Technique | None = None) -> dict[str, Any]:
    """Les valeurs par défaut de ce qui s'applique — la chaîne, et la technique
    choisie quand il y en a une (son « fond » n'est pas celui d'une autre). Le
    champ de technique prend pour défaut la technique employée : c'est elle
    qui se dit par défaut, la chaîne ne la nomme pas."""
    sorties = {nom: c.defaut for nom, c in champs_retenus(chaine, technique).items()
               if c.defaut is not None}
    champ_t = chaine.champ_de_technique
    if champ_t is not None and technique is not None and champ_t not in sorties:
        sorties[champ_t] = technique.nom
    return sorties


# -- contrôles ----------------------------------------------------------------


def evaluer(op: str, valeur: Any, attendu: Any) -> bool:
    """Le verdict d'UN contrôle. Rien de flou : ce qu'on ne sait pas comparer
    est faux, jamais « probablement bon »."""
    if op == "exists":
        return valeur is not None and valeur != "" and valeur is not _ABSENT
    if valeur is None:
        return False
    try:
        if op == "eq":
            return valeur == attendu
        if op == "ne":
            return valeur != attendu
        if op == "lte":
            return float(valeur) <= float(attendu)
        if op == "gte":
            return float(valeur) >= float(attendu)
        if op == "between":
            bas, haut = float(attendu[0]), float(attendu[1])
            return bas <= float(valeur) <= haut
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    return False


def controler(controles: list[dict[str, Any]], valeurs_exposees: dict[str, Any],
              resultats: dict[str, Any]) -> list[dict[str, Any]]:
    """Passer les contrôles d'une étape « verifier ».

    Rend une ligne par contrôle : ce qui était attendu, ce qui a été MESURÉ, et
    le verdict. Un contrôle qui ne dit pas la valeur mesurée oblige à refaire
    le run pour savoir de combien on a raté.
    """
    lignes: list[dict[str, Any]] = []
    for controle in controles:
        mesure = resoudre(controle.get("valeur"), valeurs_exposees, resultats, strict=False)
        if isinstance(mesure, str) and mesure.startswith("$"):
            mesure = None                       # rien à ce nom : le contrôle échoue, et le dit
        attendu = resoudre(controle.get("attendu"), valeurs_exposees, resultats, strict=False)
        op = str(controle.get("op"))
        lignes.append({"id": str(controle.get("id") or controle.get("valeur")),
                       "op": op, "attendu": attendu, "mesure": mesure,
                       "ok": evaluer(op, mesure, attendu)})
    return lignes

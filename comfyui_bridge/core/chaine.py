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

Les renvois s'écrivent ``$champ`` (une valeur exposée), ``$etape.cle`` (un
résultat d'étape PRÉCÉDENTE) ou ``$<champ de technique>.<chemin>`` (une
section du fichier de la technique CHOISIE — ce que son nœud fait, déclaré
là où elle déclare ses entrées). Un renvoi vers l'aval, ou vers un nom qui
n'existe pas, est refusé À LA LECTURE, avec le nom : découvert à l'exécution,
il faisait échouer la chaîne après avoir dépensé les étapes d'avant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .errors import InputValueRefusedError, UnknownWorkflowInputError, WorkflowMappingError
from .intention import RenderIntent, media_category
from .pieces_jointes import refus_par_le_nom

# Ce qu'une étape sait faire. Chaque genre est une opération que la passerelle
# tient déjà ou qu'elle porte pour la chaîne ; il n'y a pas de genre « exécuter
# n'importe quoi » — une chaîne décrit un enchaînement, pas un script.
GENRES: tuple[str, ...] = (
    "rendre",            # un run de workflow, par les moyens ordinaires
    "extraire_queue",    # les N dernières images d'une vidéo, en clip
    "extraire_image",    # une image d'une vidéo
    "recoller",          # joindre des parts en un livrable
    "composer",          # une IMAGE FIXE à sa taille exacte, avec textes, logos, texture
    "mesurer_raccords",  # la ressemblance de part en part, aux frontières
    "verifier",          # des contrôles sur ce qui a été mesuré : non tenus, ils ARRÊTENT
    "constater",         # les mêmes contrôles, CONSTATÉS : écrits au récit, jamais bloquants
                         # (Antoine, 2026-09-17 : « il ne faut plus que maestro annonce des
                         # erreurs quand la vidéo est très bien, c'est l'utilisateur qui juge »)
)

# Les deux genres qui portent des contrôles : la même écriture, deux suites —
# « verifier » arrête, « constater » écrit et continue.
CONTROLENT: tuple[str, ...] = ("verifier", "constater")

# Les clés qu'un genre accepte. Un paramètre inconnu est une faute de la
# définition, pas une option ignorée : le taire laissait une étape tourner sans
# ce que son auteur croyait avoir demandé.
_CLES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # genre -> (clés admises, clés requises)
    # « rendre » nomme SOIT un workflow, SOIT un rôle + la technique qui le
    # tient : le couple exact est vérifié à part (voir `_etape`), parce que
    # « l'un ou l'autre » ne s'écrit pas dans une liste de clés requises.
    # « memoire » : la CLÉ sous laquelle le résultat de l'étape est gardé, et
    # repris sans run quand elle revient (voir `_memoire`).
    "rendre": (frozenset({"workflow", "media", "role", "technique", "memoire"})
               | frozenset(RenderIntent.__dataclass_fields__), frozenset()),
    "extraire_queue": (frozenset({"video", "images"}), frozenset({"video", "images"})),
    "extraire_image": (frozenset({"video", "position"}), frozenset({"video"})),
    "recoller": (frozenset({"parts", "fps", "largeur", "hauteur", "chevauchement", "textes", "images", "texture"}),
                 frozenset({"parts"})),
    "mesurer_raccords": (frozenset({"parts", "chevauchement"}), frozenset({"parts"})),
    # « composer » est au livrable IMAGE ce que « recoller » est au livrable
    # vidéo : la taille demandée est atteinte ici (rééchantillonnage, jamais de
    # bandes), et c'est ici que se posent les textes, les images (un logo) et
    # une texture — les mêmes objets, les mêmes filtres que le recollage.
    "composer": (frozenset({"image", "largeur", "hauteur", "textes", "images", "texture"}),
                 frozenset({"image"})),
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
    # LA RÉCONCILIATION CONCRÈTE D'UN CHAMP, portée par son propriétaire (la
    # chaîne pour les réglages du plan, la technique pour les siens) : la
    # CATÉGORIE qui le range, dans le vocabulaire déclaré une fois à la
    # passerelle (`categories_de_champs`), et l'AIDE qui dit ce qu'il fait.
    # Antoine, 2026-09-17 : « chacun porte une réconciliation concrète, en
    # standardisant par catégorie ». Un lanceur regroupe par catégorie sans
    # rien connaître des champs.
    categorie: str | None = None
    aide: str | None = None
    # « selon » : le champ n'a de sens que sous certaines valeurs d'un AUTRE
    # champ de la chaîne — `{"champ": "charte", "valeurs": ["aucune"]}` : la
    # palette qu'on choisit s'efface devant celle qu'une charte impose, et un
    # lanceur ne la montre pas sous une charte (2026-09-24). La même clé que
    # celle qu'une technique fait naître sur ses réglages : un lanceur n'a
    # qu'une règle à connaître.
    selon: dict[str, Any] | None = None
    # « impose_par » : la valeur de ce champ est IMPOSÉE par un autre champ dès
    # que celui-ci n'est pas à l'une des valeurs « sauf » — la palette, la police
    # d'un visuel sous une charte. Le champ existe toujours : un lanceur le GRISE
    # (la valeur imposée se lit, au récit ou dans « impose » du choix maître) et
    # ne l'envoie pas. Antoine, 2026-09-24 : « grise les champs imposés par la
    # charte au lieu de les cacher ; fait-en un standard ». « selon », lui, dit
    # qu'un champ n'EXISTE pas sous une autre valeur (un réglage d'une autre
    # technique) : caché.
    impose_par: dict[str, Any] | None = None


@dataclass(frozen=True)
class Etape:
    id: str
    genre: str
    params: Any                       # dict, ou liste de contrôles pour « verifier »
    # « quand » : un RENVOI (« $cta ») ; l'étape n'est jouée que si ce qu'il
    # désigne n'est pas vide. Sautée, elle rend son média tel quel en livrable,
    # pour que l'aval qui la nomme continue de tenir (voir l'adaptateur).
    # Il prend aussi la forme NOMMÉE d'un contrôle — « valeur », « op »,
    # « attendu » —, pour ce qu'un renvoi seul ne sait pas dire : « sauf quand
    # ce champ vaut ceci ». Une valeur de menu qui signifie « rien à faire »
    # est un texte comme un autre, donc jamais vide, donc toujours vraie pour
    # un renvoi seul — et l'étape tournait pour rien. Mesuré le 2026-09-22 :
    # une étape qui n'avait rien à faire a tué la production sur un moteur mort.
    quand: str | dict[str, Any] = ""
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
    def memoire(self) -> tuple[str, ...] | None:
        """Les renvois qui font la CLÉ de mémoire d'un « rendre » — None quand
        l'étape ne se souvient de rien.

        Même image, mêmes réglages, même graine : le même résultat, repris sans
        run. C'est la définition qui dit de quoi la clé est faite ; ce module
        ne sait pas ce qu'elle nomme.
        """
        if self.genre == "rendre" and isinstance(self.params, dict):
            memoire = self.params.get("memoire")
            if isinstance(memoire, dict):
                return tuple(str(r) for r in memoire.get("cle") or ())
        return None

    @property
    def controles_nommes(self) -> str | None:
        """Le NOM de la liste de contrôles à prendre chez la technique : ce que
        « controles » porte quand l'étape n'écrit rien elle-même, ou
        « controles_de_la_technique » quand elle joint sa propre liste."""
        if self.genre in CONTROLENT and isinstance(self.params, dict):
            valeur = self.params.get("controles_de_la_technique")
            if valeur is None and not isinstance(self.params.get("controles"), list):
                valeur = self.params.get("controles")
            return str(valeur) if valeur else None
        return None

    @property
    def controles_propres(self) -> tuple:
        """Les contrôles que l'étape écrit ELLE-MÊME : la liste nue, ou la
        liste sous « controles » de la forme mixte — vide quand tout est
        emprunté à la technique."""
        if self.genre not in CONTROLENT:
            return ()
        if isinstance(self.params, list):
            return tuple(self.params)
        if isinstance(self.params, dict) and isinstance(self.params.get("controles"), list):
            return tuple(self.params["controles"])
        return ()


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
    # LE FICHIER TEL QUEL : ce qu'un renvoi « $<technique>.<chemin> » lit
    # (« $technique.budget.queue_s » : la fin fixe que le nœud de cette
    # technique impose, déclarée là où elle déclare ses entrées). Une section
    # de plus est une clé de plus dans le fichier, jamais un attribut ici.
    donnees: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chaine:
    nom: str
    version: int = 1
    resume: str = ""
    champs: dict[str, Champ] = field(default_factory=dict)
    etapes: tuple[Etape, ...] = ()
    livrable: str = ""
    # Le GABARIT que la chaîne manifeste suivre (« creation ») : vérifié à la
    # lecture du catalogue contre `resources/gabarits/<nom>.json` — voir
    # `core/gabarit.py`. Rien sans déclaration.
    gabarit: str | None = None

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
    # « requiert » : ne garder du menu que les lignes qui portent ce que la
    # chaîne exige — un plan de révélation veut une structure qui a une
    # accroche, pas les vingt structures du catalogue. Un objet, et rien d'autre.
    if "requiert" in brut:
        if "menu" not in brut:
            raise WorkflowMappingError(
                f"{contexte} : {nom!r} pose « requiert » sans « menu » — seule la "
                f"liste d'un menu se filtre")
        if not isinstance(brut["requiert"], dict) or not brut["requiert"]:
            raise WorkflowMappingError(
                f"{contexte} : « requiert » de {nom!r} doit être un objet "
                f"{{champ de la ligne: valeur exigée}}")
    return brut


def _texte_ou_rien(nom: str, brut: Any, cle: str, contexte: str) -> str | None:
    valeur = brut.get(cle)
    if valeur is None:
        return None
    if not isinstance(valeur, str) or not valeur.strip():
        raise WorkflowMappingError(
            f"{contexte} : « {cle} » de {nom!r} doit être un texte non vide")
    return valeur.strip()


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
                     requis=bool(brut.get("requis", False)),
                     categorie=_texte_ou_rien(nom, brut, "categorie", contexte),
                     aide=_texte_ou_rien(nom, brut, "aide", contexte))
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
        categorie=_texte_ou_rien(nom, brut, "categorie", contexte),
        aide=_texte_ou_rien(nom, brut, "aide", contexte),
        selon=_selon(nom, brut.get("selon"), contexte),
        impose_par=_impose_par(nom, brut.get("impose_par"), contexte),
    )


def _impose_par(nom: str, brut: Any, contexte: str) -> dict[str, Any] | None:
    """`{"champ": …, "sauf": [...]}` — ou rien. Le champ maître et les valeurs
    sous lesquelles ce champ reste LIBRE (« aucune » pour une charte)."""
    if brut is None:
        return None
    if (not isinstance(brut, dict) or not str(brut.get("champ") or "").strip()
            or not isinstance(brut.get("sauf"), list)):
        raise WorkflowMappingError(
            f"{contexte} : « impose_par » de {nom!r} attend {{\"champ\": \"<nom>\", \"sauf\": [...]}}")
    if str(brut["champ"]) == nom:
        raise WorkflowMappingError(f"{contexte} : « impose_par » de {nom!r} ne peut pas le désigner lui-même")
    return {"champ": str(brut["champ"]), "sauf": [str(v) for v in brut["sauf"]]}


def _selon(nom: str, brut: Any, contexte: str) -> dict[str, Any] | None:
    """`{"champ": …, "valeurs": [...]}` — le champ n'existe que sous ces valeurs
    du maître —, ou `{"champ": …, "sauf": [...]}` — il n'existe que HORS de
    celles-là (le logo d'une charte n'existe que sous une charte, quelle qu'elle
    soit : la liste des chartes n'est pas écrite dans la chaîne) —, ou rien.
    Une forme fausse est refusée : un lanceur qui lirait une condition boiteuse
    cacherait ou montrerait un champ au hasard."""
    if brut is None:
        return None
    forme = ("valeurs" if isinstance(brut, dict) and "valeurs" in brut and "sauf" not in brut
             else "sauf" if isinstance(brut, dict) and "sauf" in brut and "valeurs" not in brut else None)
    if (forme is None or not str(brut.get("champ") or "").strip()
            or not isinstance(brut.get(forme), list) or not brut[forme]):
        raise WorkflowMappingError(
            f"{contexte} : « selon » de {nom!r} attend {{\"champ\": \"<nom>\", \"valeurs\": [...]}} "
            f"ou {{\"champ\": \"<nom>\", \"sauf\": [...]}}")
    if str(brut["champ"]) == nom:
        raise WorkflowMappingError(f"{contexte} : « selon » de {nom!r} ne peut pas le désigner lui-même")
    return {"champ": str(brut["champ"]), forme: [str(v) for v in brut[forme]]}


# Une VALEUR CONDITIONNELLE : {"si": "$renvoi", "alors": x, "sinon": y} — x quand ce
# que « si » désigne n'est pas vide (la règle de « quand », `_vide`), y sinon. C'est
# ainsi qu'une chaîne écrit l'USAGE d'un fait qu'une étape lui livre (Antoine,
# 2026-09-24 : « l'usage qui en est fait est propre au réalisateur ») — la zone calme
# que la direction demande au modèle n'existe que si le logo est posé pendant l'image.
_CONDITIONNELLE = frozenset({"si", "alors", "sinon"})


def est_conditionnelle(valeur: Any) -> bool:
    return isinstance(valeur, dict) and "si" in valeur


def _conditionnelles_tiennent(valeur: Any, contexte: str) -> None:
    """Toute valeur conditionnelle, aussi profond qu'elle soit, a sa forme ; sinon refusée ici."""
    if isinstance(valeur, dict):
        if "si" in valeur:
            if set(valeur) != _CONDITIONNELLE:
                raise WorkflowMappingError(
                    f"{contexte} : une valeur conditionnelle s'écrit {{\"si\": \"$…\", \"alors\": …, \"sinon\": …}} "
                    f"(trouvé : {', '.join(sorted(map(str, valeur)))})")
            if not (isinstance(valeur["si"], str) and valeur["si"].startswith("$")):
                raise WorkflowMappingError(
                    f"{contexte} : « si » d'une valeur conditionnelle attend un renvoi (« $champ » ou "
                    f"« $etape.cle »), pas {valeur['si']!r}")
        for v in valeur.values():
            _conditionnelles_tiennent(v, contexte)
    elif isinstance(valeur, (list, tuple)):
        for v in valeur:
            _conditionnelles_tiennent(v, contexte)


def _renvoi(valeur: Any) -> bool:
    return isinstance(valeur, str) and valeur.startswith("$")


def _controles(params: Any, contexte: str, ident: str, vide_permise: bool = False) -> tuple:
    """Une liste de contrôles, vérifiée : chacun dit son opérateur et ce qu'il
    mesure. Un contrôle sans opérateur ne juge rien, et le taire faisait passer
    une étape « verifier » pour un feu vert. Une liste VIDE n'est permise qu'à
    une technique qui dit ainsi n'exiger rien sous ce nom (une étape, elle,
    n'a pas à exister pour ne rien juger)."""
    if not isinstance(params, list) or (not params and not vide_permise):
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


def _controles_de_technique(params: dict[str, Any], ident: str, chaine: str,
                            genre: str = "verifier") -> None:
    """« verifier » qui emprunte sa liste à la technique choisie — seule
    (« controles » NOMME la liste), ou jointe à la sienne (« controles » est
    la liste de l'étape, « controles_de_la_technique » nomme celle de la
    technique). La seconde forme est celle d'un plan qui se juge AVANT de
    peindre aussi sur ce que la technique exige de lui (2026-09-17)."""
    contexte = f"chaîne {chaine!r}"
    inconnues = sorted(set(params) - {"technique", "controles", "controles_de_la_technique"})
    if inconnues:
        raise WorkflowMappingError(
            f"{contexte} : « {genre} » de {ident!r} — clé(s) inconnue(s) : "
            f"{', '.join(inconnues)} (attendu : « technique », « controles » et, "
            f"quand « controles » est une liste, « controles_de_la_technique »)")
    if not _renvoi(params.get("technique")):
        raise WorkflowMappingError(
            f"{contexte} : « {genre} » de {ident!r} — « technique » attend un renvoi "
            f"(« $technique »), pas {params.get('technique')!r}")
    controles = params.get("controles")
    if isinstance(controles, list):
        _controles(controles, contexte, ident)
        if not str(params.get("controles_de_la_technique") or "").strip():
            raise WorkflowMappingError(
                f"{contexte} : « {genre} » de {ident!r} — avec sa propre liste sous "
                f"« controles », « controles_de_la_technique » doit nommer celle à "
                f"prendre chez la technique")
        return
    if "controles_de_la_technique" in params:
        raise WorkflowMappingError(
            f"{contexte} : « {genre} » de {ident!r} — « controles_de_la_technique » ne "
            f"va qu'avec une liste de contrôles sous « controles »")
    if not str(controles or "").strip():
        raise WorkflowMappingError(
            f"{contexte} : « {genre} » de {ident!r} — « controles » doit nommer la "
            f"liste à prendre chez la technique")


def _rendre_nomme_sa_cible(params: dict[str, Any], ident: str, chaine: str) -> None:
    """Un « rendre » nomme un WORKFLOW, ou un RÔLE et la technique qui le tient.

    Jamais les deux : lequel l'emporterait ? Et jamais un rôle sans technique —
    un rôle seul ne désigne aucun graphe, et l'étape n'aurait rien à lancer.
    """
    contexte = f"chaîne {chaine!r}"
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


def _memoire(params: dict[str, Any], ident: str, chaine: str) -> None:
    """« memoire » : ``{"cle": ["$…", …]}`` — une liste NON VIDE de renvois.

    La clé dit de quoi dépend le résultat (l'image, les réglages, la graine) :
    une clé vide dirait « toujours le même », et une valeur écrite en dur n'y
    changerait rien d'un appel à l'autre. Les renvois eux-mêmes sont vérifiés
    comme les autres (`_verifier_renvois`) : vers l'amont ou un champ exposé.
    """
    memoire = params.get("memoire")
    if memoire is None:
        return
    contexte = f"chaîne {chaine!r} : étape {ident!r} (rendre)"
    if not isinstance(memoire, dict) or set(memoire) != {"cle"}:
        raise WorkflowMappingError(
            f"{contexte} — « memoire » attend un objet {{\"cle\": [\"$…\", …]}}, "
            f"pas {memoire!r}")
    cle = memoire.get("cle")
    if not isinstance(cle, list) or not cle:
        raise WorkflowMappingError(
            f"{contexte} — « memoire.cle » attend une liste non vide de renvois")
    for renvoi in cle:
        if not _renvoi(renvoi):
            raise WorkflowMappingError(
                f"{contexte} — « memoire.cle » ne prend que des renvois "
                f"(« $champ », « $etape.cle »), pas {renvoi!r} : une valeur écrite "
                f"en dur ne distingue rien d'un appel à l'autre")


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
    _conditionnelles_tiennent(params, f"chaîne {chaine!r} : étape {ident!r}")
    # UNE ÉTAPE FACULTATIVE DIT DE QUOI ELLE DÉPEND : « quand » est un renvoi,
    # jamais une valeur écrite en dur (une étape qu'on veut toujours sauter
    # n'a pas à exister). Demandé le 2026-09-15 : « si pas de CTA spécifié, on
    # ne met pas de CTA — on saute l'étape ».
    quand = brut.get("quand", "")
    if isinstance(quand, dict):
        # La forme NOMMÉE, mot pour mot celle d'un contrôle : une chaîne ne
        # parle qu'une langue, et l'opérateur est le même que celui qui juge.
        renvoi = quand.get("valeur")
        op = str(quand.get("op") or "")
        if not isinstance(renvoi, str) or not renvoi.startswith("$"):
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : étape {ident!r} — « quand.valeur » attend un renvoi "
                f"(« $champ » ou « $etape.cle »), pas {renvoi!r}")
        if op not in _OPS:
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : étape {ident!r} — « quand.op » {op!r} est inconnu "
                f"(connus : {', '.join(_OPS)})")
        if op != "exists" and "attendu" not in quand:
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : étape {ident!r} — « quand » en {op!r} attend « attendu » "
                f"(la valeur à laquelle comparer)")
    elif quand is not None and quand != "":
        if not isinstance(quand, str) or not quand.startswith("$"):
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : étape {ident!r} — « quand » attend un renvoi "
                f"(« $champ » ou « $etape.cle »), ou une condition nommée "
                f"(« valeur », « op », « attendu »), pas {quand!r}")
    else:
        quand = ""
    sinon = brut.get("sinon")
    if sinon is None:
        sinon = {}
    elif not isinstance(sinon, dict):
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : étape {ident!r} — « sinon » décrit ce que l'étape rend "
            f"quand elle est sautée : un objet, pas {sinon!r}")
    if genre in CONTROLENT:
        # TROIS FORMES : la liste écrite ici ; le NOM d'une liste que la
        # technique choisie porte (sans quoi une chaîne qui veut juger deux
        # peintures différentes devait se dédoubler pour porter les deux listes
        # — Antoine, 2026-09-16) ; ou la liste d'ici JOINTE à une liste nommée
        # chez la technique (le plan se juge avant de peindre aussi sur ce que
        # la technique exige de lui — 2026-09-17).
        if isinstance(params, dict):
            _controles_de_technique(params, ident, chaine, genre)
            return Etape(id=ident, genre=genre, params=params, quand=quand,
                 sinon=dict(sinon))
        if not isinstance(params, list) or not params:
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : « {genre} » de {ident!r} attend une liste de contrôles, "
                f"{{\"technique\": \"$…\", \"controles\": \"<nom>\"}}, ou {{\"controles\": [...], "
                f"\"technique\": \"$…\", \"controles_de_la_technique\": \"<nom>\"}}")
        _controles(params, f"chaîne {chaine!r}", ident)
        return Etape(id=ident, genre=genre, params=params, quand=quand,
                 sinon=dict(sinon))
    if not isinstance(params, dict):
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : « {genre} » de {ident!r} attend un objet de paramètres")
    if genre == "rendre":
        _rendre_nomme_sa_cible(params, ident, chaine)
        _memoire(params, ident, chaine)
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
    for champ in champs.values():
        if champ.selon and champ.selon["champ"] not in champs:
            raise WorkflowMappingError(
                f"chaîne {nom!r} : « selon » de {champ.nom!r} désigne un champ que la chaîne "
                f"n'expose pas ({champ.selon['champ']!r})")
        if champ.impose_par and champ.impose_par["champ"] not in champs:
            raise WorkflowMappingError(
                f"chaîne {nom!r} : « impose_par » de {champ.nom!r} désigne un champ que la chaîne "
                f"n'expose pas ({champ.impose_par['champ']!r})")
    gabarit = brut.get("gabarit")
    if gabarit is not None and (not isinstance(gabarit, str) or not gabarit.strip()):
        raise WorkflowMappingError(f"chaîne {nom!r} : « gabarit » nomme un gabarit, ou ne s'écrit pas")
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
    chaine = Chaine(nom=nom, version=int(brut.get("version", 1)), gabarit=(gabarit or None),
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
        _conditionnelles_tiennent(entrees, f"{contexte} : rôle {str(cle)!r}")
        roles[str(cle)] = Role(nom=str(cle), workflow=workflow, inputs=dict(entrees))
    listes = brut.get("controles") or {}
    if not isinstance(listes, dict):
        raise WorkflowMappingError(f"{contexte} : « controles » doit être un objet nom → liste")
    controles = {str(cle): _controles(valeur, contexte, str(cle), vide_permise=True)
                 for cle, valeur in listes.items()}
    par_defaut = brut.get("par_defaut", False)
    if not isinstance(par_defaut, bool):
        raise WorkflowMappingError(
            f"{contexte} : « par_defaut » est vrai ou faux, reçu {par_defaut!r}")
    return Technique(nom=nom, version=int(brut.get("version", 1)),
                     libelle=str(brut.get("libelle") or nom),
                     resume=str(brut.get("resume") or ""),
                     champs=champs, roles=roles, controles=controles,
                     par_defaut=par_defaut, donnees=dict(brut))


def techniques_pour(chaine: Chaine, techniques: dict[str, Technique] | None
                    ) -> dict[str, Technique]:
    """Les techniques qui tiennent CETTE chaîne : celles qui portent au moins un
    des rôles qu'elle nomme.

    Les techniques vivent dans un seul dossier, pour toutes les chaînes : une
    façon de peindre une révélation (« encre », « brume ») n'a rien à faire dans
    le menu d'une chaîne qui crée une image, et une façon de créer une image
    (le rôle « image ») ne tient aucun rôle d'une révélation. Avant le
    2026-09-24, une seule chaîne nommait des rôles, et la question ne se posait
    pas : toute technique déclarée était jugée contre toute chaîne, et une
    technique d'un autre plan l'aurait fait refuser au démarrage.

    Une technique qui ne tient AUCUN rôle de la chaîne est une technique d'un
    autre plan : écartée sans bruit. Une technique qui en tient une PART
    seulement n'est pas écartée : elle est gardée, et `verifier_techniques` la
    refuse en nommant le rôle qui manque — c'est une faute d'écriture, pas une
    autre chaîne. Une chaîne qui ne nomme AUCUN rôle n'en a aucune : elle ne
    choisit pas de technique (voir `champs_admis`), et rien ne doit la juger —
    mesuré avant la première relance : « video-prolongement », sans rôle,
    recevait toutes les techniques et voyait deux « par défaut » (l'encre de la
    révélation, la rapide de la création), et le catalogue refusait de charger.
    """
    roles = {e.role for e in chaine.etapes if e.role is not None}
    if not roles:
        return {}
    return {nom: t for nom, t in (techniques or {}).items() if roles & set(t.roles)}


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
    for nom, technique in (techniques or {}).items():
        # Une section « applique: false » dit ce que la technique NE LIT PAS ;
        # sur un champ qu'elle expose pourtant, c'est une contradiction.
        for cle, section in (technique.donnees or {}).items():
            if isinstance(section, dict) and section.get("applique") is False and str(cle) in technique.champs:
                raise WorkflowMappingError(
                    f"technique {nom!r} : déclare ne pas lire « {cle} » (applique: false) et l'expose pourtant "
                    f"— l'un des deux ment")
        for champ in technique.champs.values():
            if champ.impose_par and champ.impose_par["champ"] not in chaine.champs:
                raise WorkflowMappingError(
                    f"technique {nom!r} : « impose_par » de {champ.nom!r} désigne un champ que la "
                    f"chaîne {chaine.nom!r} n'expose pas ({champ.impose_par['champ']!r})")
    amont: set[str] = set()
    for etape in chaine.etapes:
        for nom, technique in (techniques or {}).items():
            connus = set(chaine.champs) | set(technique.champs) | amont
            # Ce que le PLAN lit dans le fichier de la technique
            # (« $technique.budget.queue_s ») doit s'y trouver, chez CHACUNE :
            # la technique est choisie à l'appel, et celle qui ne porterait pas
            # la section ferait échouer l'étape sous elle seule.
            _chemins_tiennent(list(renvois(etape.params)) + list(renvois(etape.quand)),
                              chaine, technique, f"technique {nom!r}", etape)
            if etape.role is not None:
                role = technique.roles.get(etape.role)
                if role is None:
                    raise WorkflowMappingError(
                        f"chaîne {chaine.nom!r} : l'étape {etape.id!r} demande le rôle "
                        f"{etape.role!r}, que la technique {nom!r} ne tient pas "
                        f"(elle tient : {', '.join(sorted(technique.roles)) or 'rien'})")
                _renvois_tiennent(role.inputs, connus,
                                  f"technique {nom!r}, rôle {etape.role!r}", chaine, etape)
                _chemins_tiennent(list(renvois(role.inputs)), chaine, technique,
                                  f"technique {nom!r}, rôle {etape.role!r}", etape)
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
                _chemins_tiennent(list(renvois(liste)), chaine, technique,
                                  f"technique {nom!r}, contrôles {nommes!r}", etape)
        amont.add(etape.id)
    # Rien de fantôme, en dernier (les rôles et les renvois d'abord : un champ
    # que rien ne lit se juge sur une chaîne dont tout le reste tient) : un
    # champ exposé que rien ne lit est refusé ici, pour toute chaîne et toute
    # technique — la règle compte partout (2026-09-24).
    fantomes = champs_que_rien_ne_lit(chaine, techniques)
    if fantomes:
        dits = ", ".join(f"« {champ} » ({'de la chaîne' if qui == 'chaine' else 'de la technique ' + repr(qui)})"
                         for qui, champ in fantomes)
        raise WorkflowMappingError(
            f"chaîne {chaine.nom!r} : {dits} — exposé mais lu par aucune étape, aucun rôle ni aucun "
            f"contrôle : un champ que rien ne lit serait un faux champ ; le brancher, ou le retirer")


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


def _chemins_tiennent(renvois_lus: list[str], chaine: Chaine, technique: Technique,
                      contexte: str, etape: Etape) -> None:
    """Un renvoi « $<champ de technique>.<chemin> » lit le FICHIER de la
    technique choisie : refusé à la lecture si cette technique-ci ne porte pas
    le chemin — sous elle, l'étape n'aurait rien à désigner."""
    champ = chaine.champ_de_technique
    if champ is None:
        return
    for renvoi in renvois_lus:
        tete, _, reste = renvoi.partition(".")
        if tete != champ or not reste:
            continue
        if _lire_chemin(technique.donnees, reste.split(".")) is _ABSENT:
            raise WorkflowMappingError(
                f"{contexte} : « ${renvoi} » à l'étape {etape.id!r} de {chaine.nom!r} — "
                f"le fichier de la technique ne porte pas « {reste} » (sections : "
                f"{', '.join(sorted(technique.donnees)) or 'aucune'})")


def champs_admis(chaine: Chaine, techniques: dict[str, Technique] | None = None
                 ) -> dict[str, Champ]:
    """Tous les champs qu'un appelant peut nommer : ceux de la chaîne, plus ceux
    de TOUTES les techniques.

    Toutes, et pas seulement la technique choisie : un raccourci enregistré
    sous une technique doit pouvoir se rejouer sous une autre sans être refusé
    champ par champ. Ce qui n'appartient pas à la technique choisie est ÉCARTÉ et dit,
    jamais refusé (voir `valeurs`).

    Une chaîne qui ne CHOISIT aucune technique n'en admet aucun champ : les
    réglages des techniques d'un autre mode n'ont rien à faire dans son
    contrat (mesuré le 2026-09-19 : un mode sans technique publiait les
    papiers et les encres d'un autre parmi ce qu'il accepte).
    """
    tous = dict(chaine.champs)
    if chaine.champ_de_technique is None:
        return tous
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

    ``$<champ>.<chemin>`` lit dans ``resultats`` comme ``$etape.cle`` : c'est
    là que `resultats_initiaux` pose le fichier de la technique choisie, sous
    le nom du champ qui la choisit — ce module n'a rien d'autre à savoir.
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
    if est_conditionnelle(valeur):
        condition = resoudre(valeur["si"], valeurs, resultats, strict)
        if not strict and condition == valeur["si"]:
            return valeur          # pas encore connu : l'aperçu montre la condition, il n'invente pas la branche
        return resoudre(valeur["sinon"] if _vide(condition) else valeur["alors"], valeurs, resultats, strict)
    if isinstance(valeur, dict):
        return {k: resoudre(v, valeurs, resultats, strict) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [resoudre(v, valeurs, resultats, strict) for v in valeur]
    return valeur


def resultats_initiaux(chaine: Chaine, technique: Technique | None) -> dict[str, Any]:
    """Ce que les renvois trouvent AVANT la première étape : le fichier de la
    technique choisie, sous le nom du champ qui la choisit.

    « $technique » (sans chemin) reste le NOM de la technique — il est dans
    les valeurs, lues d'abord ; « $technique.budget.queue_s » descend dans son
    fichier, comme « $etape.recit.cle » descend dans un résultat. Une étape ne
    peut pas porter le nom d'un champ (refusé à la lecture) : la place est
    libre, et rien d'autre n'a à connaître cette convention.
    """
    champ = chaine.champ_de_technique
    if champ is None or technique is None:
        return {}
    return {champ: dict(technique.donnees)}


def entrees_du_role(role: Role, params: dict[str, Any], valeurs: dict[str, Any],
                    resultats: dict[str, Any], strict: bool = True) -> dict[str, Any]:
    """Les entrées de nœud qu'une étape à rôle envoie : celles du rôle, écrites
    chez la technique et résolues ici, SOUS celles de l'étape — le plan garde
    le dernier mot sur ce qu'il a lui-même écrit, et une technique ne recouvre
    pas en silence ce que la chaîne demande. La même lecture sert à l'aperçu :
    ce qu'un lanceur montre est ce qui part."""
    return {**resoudre(role.inputs, valeurs, resultats, strict),
            **(params.get("inputs") or {})}


def champs_lus_par(etape: Etape, technique: Technique | None = None) -> set[str]:
    """Les TÊTES des renvois qu'une étape lit — dans ses paramètres, son
    « quand », et ce que la technique met derrière son rôle ou sa liste de
    contrôles. Un champ exposé s'y reconnaît à son nom ; une étape d'amont
    aussi, et l'appelant fait le tri. La clé de MÉMOIRE ne compte pas : un
    champ qui ne servirait qu'à distinguer des souvenirs ne pèse sur rien."""
    params = etape.params
    if isinstance(params, dict) and "memoire" in params:
        params = {k: v for k, v in params.items() if k != "memoire"}
    tetes = {r.split(".", 1)[0] for r in list(renvois(params)) + list(renvois(etape.quand))}
    if technique is not None:
        if etape.role is not None and etape.role in technique.roles:
            tetes |= {r.split(".", 1)[0] for r in renvois(technique.roles[etape.role].inputs)}
        nommes = etape.controles_nommes
        if nommes is not None and nommes in technique.controles:
            tetes |= {r.split(".", 1)[0] for r in renvois(technique.controles[nommes])}
    return tetes


def champs_que_rien_ne_lit(chaine: Chaine, techniques: dict[str, Technique] | None = None
                           ) -> list[tuple[str, str]]:
    """Les champs EXPOSÉS que rien ne lit : ceux de la chaîne qu'aucune étape
    ni aucune technique ne nomme, ceux d'une technique qu'aucun de ses rôles,
    aucun de ses contrôles ni aucune étape ne nomme sous CETTE chaîne. Un tel
    champ s'afficherait, se remplirait, et ne pèserait sur rien : un faux champ
    — Antoine, 2026-09-24 : « s'il n'y a rien dans ComfyUI en ce sens, on ne
    met pas de faux champ dans maestro, c'est une règle générale importante »,
    et « la règle doit compter partout ». Le champ qui CHOISIT la technique ne
    compte pas : aucun renvoi ne le lit, il désigne un fichier. Rend
    [(propriétaire, champ)], le propriétaire étant 'chaine' ou le nom de la
    technique."""
    techniques = techniques or {}
    lus_par_la_chaine: set[str] = set()
    for etape in chaine.etapes:
        lus_par_la_chaine |= champs_lus_par(etape, None)
        for technique in techniques.values():
            lus_par_la_chaine |= champs_lus_par(etape, technique)
    sans: list[tuple[str, str]] = [("chaine", nom) for nom in chaine.champs
                                   if nom != chaine.champ_de_technique and nom not in lus_par_la_chaine]
    for nom_t, technique in sorted(techniques.items()):
        lus: set[str] = set()
        for etape in chaine.etapes:
            lus |= champs_lus_par(etape, technique)
        sans.extend((nom_t, nom) for nom in technique.champs if nom not in lus)
    return sans


def _vide(valeur: Any) -> bool:
    """Ce qu'une étape facultative appelle « vide » : texte blanc, faux, zéro,
    liste ou objet vides, absent — la règle de « quand », écrite une fois."""
    if isinstance(valeur, str):
        return not valeur.strip()
    return not valeur


def tete_de_quand(quand: Any) -> str | None:
    """Le champ (ou l'étape) que « quand » INTERROGE, quelle que soit sa forme
    — le renvoi seul, ou celui que porte la condition nommée."""
    renvoi = quand.get("valeur") if isinstance(quand, dict) else quand
    if not isinstance(renvoi, str) or not renvoi.startswith("$"):
        return None
    return renvoi.lstrip("$").split(".", 1)[0]


def sans_effet_si_sautee(chaine: Chaine, etape: Etape, technique: Technique | None,
                         valeurs: dict[str, Any]) -> list[str]:
    """Les champs exposés qui restent SANS EFFET quand cette étape est sautée :
    ceux qu'elle seule lit (aucune autre étape ne les nomme), et qui portent
    une valeur non vide — un réglage posé pour une étape qui n'a pas lieu.

    Une police d'appel sans appel partait nulle part sans le dire (2026-09-19) ;
    le renvoi de « quand » lui-même n'y est pas : c'est lui qui est vide.

    Ce que le « sinon » RENVOIE n'y est pas non plus : une étape de passe-plat
    sautée rend elle-même ces champs à l'aval, ils ont donc bien un effet.
    """
    lus = champs_lus_par(etape, technique)
    ailleurs: set[str] = set()
    for autre in chaine.etapes:
        if autre.id != etape.id:
            ailleurs |= champs_lus_par(autre, technique)
    quand = tete_de_quand(etape.quand)
    rendus = {r.split(".", 1)[0] for r in renvois(etape.sinon)}
    exposes = champs_retenus(chaine, technique)
    return sorted(nom for nom in lus
                  if nom in exposes and nom not in ailleurs and nom != quand
                  and nom not in rendus
                  and nom != chaine.champ_de_technique and not _vide(valeurs.get(nom)))


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
        nom = str(brute)
        # Le fichier est déjà chez le moteur (un dépôt d'il y a une minute, un
        # rejeu, un raccourci d'hier) : son NOM est tout ce qu'on a, et il
        # suffit à écarter ce qui ferait tomber le moteur (2026-09-22).
        pourquoi = refus_par_le_nom(nom, champ.media)
        if pourquoi is not None:
            raise _refus(champ, pourquoi)
        return nom
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
                # Une PIÈCE JOINTE facultative laissée au repos vaut « non
                # fourni » (None) : l'étape qui l'écrit dans ses médias
                # (« "image": "$image" ») reçoit rien, au lieu de « n'a rien à
                # désigner » — mesuré le 2026-09-18 sur une image de référence
                # absente. Un champ typé sans défaut, lui, reste absent : « "" »
                # et « false » sont des défauts déclarés, pas des absences.
                if champ.media is not None:
                    sorties[nom] = None
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
    le run pour savoir de combien on a raté. Un contrôle qui porte une « aide »
    (ce qu'il mesure, quoi faire quand il tombe) la rend avec sa ligne : un
    refus qui ne dit que « mesuré, attendu » est juste, et muet (2026-09-19,
    deux plans refusés sans un mot de plus).
    """
    lignes: list[dict[str, Any]] = []
    for controle in controles:
        mesure = resoudre(controle.get("valeur"), valeurs_exposees, resultats, strict=False)
        if isinstance(mesure, str) and mesure.startswith("$"):
            mesure = None                       # rien à ce nom : le contrôle échoue, et le dit
        attendu = resoudre(controle.get("attendu"), valeurs_exposees, resultats, strict=False)
        op = str(controle.get("op"))
        ligne = {"id": str(controle.get("id") or controle.get("valeur")),
                 "op": op, "attendu": attendu, "mesure": mesure,
                 "ok": evaluer(op, mesure, attendu)}
        aide = controle.get("aide")
        if isinstance(aide, str) and aide.strip():
            ligne["aide"] = aide.strip()
        lignes.append(ligne)
    return lignes

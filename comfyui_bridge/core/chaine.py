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
    "rendre": (frozenset({"workflow", "media"}) | frozenset(RenderIntent.__dataclass_fields__),
               frozenset({"workflow"})),
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

    @property
    def workflow(self) -> str | None:
        if self.genre == "rendre" and isinstance(self.params, dict):
            valeur = self.params.get("workflow")
            return str(valeur) if valeur else None
        return None


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


# -- lecture ------------------------------------------------------------------


def _options_depuis(nom: str, brut: Any, chaine: str) -> Any:
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
            f"chaîne {chaine!r} : « options_depuis » de {nom!r} doit être un objet "
            f"({', '.join(_SOURCES_D_OPTIONS)})")
    nommees = [source for source in _SOURCES_D_OPTIONS if source in brut]
    if len(nommees) > 1:
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : {nom!r} nomme deux sources d'options à la fois "
            f"({', '.join(nommees)}) — une seule peut faire la liste")
    if "menu" in brut and not str(brut["menu"]).strip():
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : {nom!r} tire ses options d'un « menu » sans le nommer")
    return brut


def _champ(nom: str, brut: Any, chaine: str) -> Champ:
    if not isinstance(brut, dict):
        raise WorkflowMappingError(f"chaîne {chaine!r} : le champ exposé {nom!r} doit être un objet")
    categorie = brut.get("media")
    if categorie is not None:
        if not media_category(str(categorie)):
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : {nom!r} déclare une catégorie de média inconnue "
                f"({categorie!r})")
        return Champ(nom=nom, media=str(categorie), type="STRING",
                     libelle=str(brut.get("libelle") or nom),
                     requis=bool(brut.get("requis", False)))
    genre = str(brut.get("type", "STRING")).upper()
    if genre not in _TYPES:
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : {nom!r} déclare le type {genre!r}, hors de "
            f"{', '.join(_TYPES)}")
    options = brut.get("options")
    if options is not None and not isinstance(options, list):
        raise WorkflowMappingError(f"chaîne {chaine!r} : « options » de {nom!r} doit être une liste")
    return Champ(
        nom=nom, type=genre, defaut=brut.get("defaut"),
        minimum=brut.get("min"), maximum=brut.get("max"), pas=brut.get("step"),
        options=tuple(options) if options is not None else None,
        options_depuis=_options_depuis(nom, brut.get("options_depuis"), chaine),
        libelle=str(brut.get("libelle") or nom),
        unite=brut.get("unite"),
        requis=bool(brut.get("requis", False)),
    )


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
    if genre == "verifier":
        if not isinstance(params, list) or not params:
            raise WorkflowMappingError(
                f"chaîne {chaine!r} : « verifier » de {ident!r} attend une liste de contrôles")
        for controle in params:
            if not isinstance(controle, dict):
                raise WorkflowMappingError(
                    f"chaîne {chaine!r} : un contrôle de {ident!r} doit être un objet")
            op = str(controle.get("op") or "")
            if op not in _OPS:
                raise WorkflowMappingError(
                    f"chaîne {chaine!r} : contrôle {controle.get('id')!r} de {ident!r} : "
                    f"l'opérateur {op!r} est hors de {', '.join(_OPS)}")
            if "valeur" not in controle:
                raise WorkflowMappingError(
                    f"chaîne {chaine!r} : contrôle {controle.get('id')!r} de {ident!r} : "
                    f"« valeur » manquante")
        return Etape(id=ident, genre=genre, params=params)
    if not isinstance(params, dict):
        raise WorkflowMappingError(
            f"chaîne {chaine!r} : « {genre} » de {ident!r} attend un objet de paramètres")
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
    return Etape(id=ident, genre=genre, params=params)


def lire(brut: Any, nom_declare: str | None = None) -> Chaine:
    """Lire une définition de chaîne, et refuser tout ce qui ne tient pas."""
    if not isinstance(brut, dict):
        raise WorkflowMappingError("une chaîne est un objet JSON")
    nom = str(brut.get("chaine") or nom_declare or "").strip()
    if not nom:
        raise WorkflowMappingError("cette chaîne ne se nomme pas (clé « chaine »)")
    champs = {str(k): _champ(str(k), v, nom) for k, v in (brut.get("expose") or {}).items()}
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
        for renvoi in renvois(etape.params):
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
            options: dict[str, tuple[Any, ...]] | None = None) -> dict[str, Any]:
    """Ce que la chaîne va employer, à partir de ce que l'appelant a envoyé.

    Le défaut déclaré comble un champ absent ; un champ requis absent, une
    valeur hors bornes ou hors menu sont refusés ici — la passerelle fait
    autorité sur les valeurs, quoi qu'un client ait laissé passer.
    """
    options = options or {}
    inconnus = sorted(k for k in demande if k not in chaine.champs)
    if inconnus:
        raise UnknownWorkflowInputError(
            f"chaîne {chaine.nom!r} : champ(s) qu'elle n'expose pas : {', '.join(inconnus)}",
            workflow=chaine.nom, fields=inconnus, accepts=sorted(chaine.champs))
    sorties: dict[str, Any] = {}
    for nom, champ in chaine.champs.items():
        brute = demande.get(nom)
        if brute is None or brute == "":
            if champ.requis:
                raise _refus(champ, f"est requis par la chaîne {chaine.nom!r}",
                             workflow=chaine.nom)
            if champ.defaut is None:
                continue
            brute = champ.defaut
        sorties[nom] = valeur_de(champ, brute, options.get(nom))
    return sorties


def defauts(chaine: Chaine) -> dict[str, Any]:
    return {nom: c.defaut for nom, c in chaine.champs.items() if c.defaut is not None}


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

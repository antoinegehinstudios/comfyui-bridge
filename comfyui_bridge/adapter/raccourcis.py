"""Les raccourcis d'un mode : un ensemble de réglages ENREGISTRÉ, et son aperçu.

Un mode publie des champs et leurs défauts ; un utilisateur qui a trouvé SON
réglage (le fond, le tracé, l'ambiance, les durées) n'avait aucun moyen de le
garder — il le retapait, et le retapait faux. Un raccourci est ce réglage-là,
nommé, avec l'aperçu de la livraison qui l'a fait naître — et ses SOURCES, les
pièces jointes de cette livraison, gardées à part des réglages. Le mode
lui-même, avec ses défauts, reste le raccourci implicite : il n'est écrit nulle
part.

Ici, le STOCKAGE et la LECTURE, sans HTTP : un fichier JSON par raccourci, à
côté des aperçus des modes (`_data/raccourcis/<mode>/<id>.json`), et l'image
animée qui le montre sous le même nom. La validation des valeurs appartient au
mode (le noyau des chaînes, le profil d'un graphe) et reste à l'API : deux
autorités sur la même valeur finissent par diverger.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

# Les formats qu'un aperçu animé peut prendre, dans l'ordre où on les préfère —
# `montage_video.apercu_anime` écrit du WebP animé quand l'ffmpeg du poste sait
# l'écrire (dix fois plus léger, mesuré), du GIF sinon. C'est la MÊME liste que
# celle des aperçus de mode : l'écrire deux fois, c'est servir un jour un
# `image/gif` annoncé `image/webp`.
FORMATS: tuple[tuple[str, str], ...] = ((".webp", "image/webp"), (".gif", "image/gif"))

# Ce que la fiche garde d'une DEMANDE comme réglages : tout sauf ce que la
# passerelle possède elle-même (le mode visé, l'étiquette de sortie), la forme
# du média, les entrées de nœud brutes et les contraintes — un raccourci
# enregistre des RÉGLAGES, pas une requête. Les pièces jointes, elles, sont ses
# sources (``sources_de``), à part.
RESERVES: tuple[str, ...] = ("workflow", "label", "kind", "media", "inputs", "constraints")

# Le rang par défaut d'un raccourci dans la liste d'un mode. Cent, comme les
# `ordre` de la vitrine : il reste de la place devant pour en épingler un sans
# renuméroter les autres.
ORDRE_PAR_DEFAUT = 100

# Un identifiant plus long qu'un titre de carte ne sert plus à rien : il est
# tapé dans une URL et lu dans un dossier.
LONGUEUR_ID = 40


def dossier(base: Path, workflow: str) -> Path:
    """Où vivent les raccourcis d'un mode : à côté des aperçus, jamais dans le
    dossier de sortie du moteur (un réglage enregistré n'est pas un livrable)."""
    return Path(base) / "raccourcis" / workflow


def identifiant(titre: str, existants: Iterable[str] = ()) -> str:
    """L'identifiant d'un raccourci, tiré de son titre.

    Un identifiant tiré du titre se lit dans l'URL et dans le dossier — un
    numéro n'aurait rien dit à celui qui ouvre `_data/raccourcis/`. Les accents
    sont retirés plutôt que percentés : « Sépia » écrivait `S%C3%A9pia` dans
    l'adresse. Deux titres identiques ne s'écrasent pas : le second prend `-2`.
    """
    plie = unicodedata.normalize("NFD", str(titre or ""))
    sans_accent = "".join(c for c in plie if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "-", sans_accent.lower()).strip("-")
    # La coupe se fait APRÈS la réduction : couper le titre brut aurait pu
    # tomber au milieu d'un accent décomposé et laisser un tiret orphelin.
    base = base[:LONGUEUR_ID].strip("-") or "raccourci"
    pris = {str(e) for e in existants}
    if base not in pris:
        return base
    rang = 2
    while f"{base}-{rang}" in pris:
        rang += 1
    return f"{base}-{rang}"


def filtrer_demande(demande: dict[str, Any] | None, medias: Iterable[str] = ()) -> dict[str, Any]:
    """La demande d'un run, ramenée aux RÉGLAGES qu'un raccourci rejoue.

    Les pièces jointes en sont retirées : ce sont les SOURCES du raccourci,
    gardées à part (``sources_de``) — un réglage se juge contre les défauts du
    mode et se valide par lui, une source ne se juge pas.
    """
    exclus = set(RESERVES) | {str(m) for m in medias}
    return {k: v for k, v in (demande or {}).items() if k not in exclus}


def sources_de(demande: dict[str, Any] | None, medias: Iterable[str] = ()) -> dict[str, str]:
    """Les pièces jointes d'une demande, par nom de champ : les SOURCES qu'un
    raccourci rejoue, à part de ses réglages.

    Un raccourci enregistré depuis une livraison rejoue CETTE livraison : sans
    ses sources, le lanceur ouvrait le formulaire sur la dernière image déposée
    dans la session — celle d'un autre mode, parfois — et c'est elle qui
    partait (2026-09-20, Antoine : « ce n'est pas la bonne source qui est
    enregistrée, mais la dernière produite »). Le nom est celui du fichier chez
    le moteur, comme pour un rejeu : une source retirée de là échoue au
    lancement, comme un rejeu — la passerelle ne sait pas où le moteur range
    ses entrées. Une pièce au repos (vide) n'est pas une source.
    """
    noms = {str(m) for m in medias}
    return {k: str(v).strip() for k, v in (demande or {}).items()
            if k in noms and isinstance(v, str) and v.strip()}


def _fichier(base: Path, workflow: str, ident: str) -> Path:
    return dossier(base, workflow) / f"{ident}.json"


def lire(base: Path, workflow: str, ident: str) -> dict[str, Any] | None:
    """La fiche d'un raccourci, ou ``None`` s'il n'y en a pas.

    Un fichier PRÉSENT mais illisible n'est pas traité comme une absence : il
    n'est écrit que d'un coup (voir ``ecrire``), donc un JSON cassé est une
    panne à voir, pas un raccourci qui n'existerait pas.
    """
    f = _fichier(base, workflow, ident)
    if not f.is_file():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def lister(base: Path, workflow: str) -> list[dict[str, Any]]:
    """Les fiches d'un mode, dans l'ordre où un lanceur les affiche : le rang
    déclaré d'abord, puis le titre — comparé sans casse ni accent, sinon
    « Élan » tombait après « Zéphyr » dans la liste.

    À rang ET titre égaux (deux essais du même nom), le plus ancien passe
    devant : sans ce dernier départage, l'ordre était celui du système de
    fichiers, et « Mon réglage » et « Mon réglage » changeaient de place d'un
    rechargement à l'autre.
    """
    d = dossier(base, workflow)
    if not d.is_dir():
        return []
    # Un fichier caché n'est pas une fiche : c'est le brouillon d'une écriture
    # en cours (ou celui qu'un arrêt brutal a laissé), et le lire rendrait un
    # JSON tronqué comme s'il était un raccourci.
    fiches = [json.loads(f.read_text(encoding="utf-8"))
              for f in sorted(d.glob("*.json")) if not f.name.startswith(".")]
    return sorted(fiches, key=lambda f: (f.get("ordre") if isinstance(f.get("ordre"), int)
                                         else ORDRE_PAR_DEFAUT, _range(f.get("titre")),
                                         str(f.get("cree_le") or ""), str(f.get("id") or "")))


def modes(base: Path) -> list[str]:
    """Les modes qui ont au moins une fiche : les sous-dossiers de
    `_data/raccourcis/`. Un mode retiré du catalogue garde son dossier — ses
    fiches ne sont pas perdues, juste plus listées sous une entrée."""
    d = Path(base) / "raccourcis"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def sans_sources(base: Path, workflow: str) -> list[dict[str, Any]]:
    """Les fiches d'un mode écrites AVANT les sources (2026-09-20) : sans la
    clé. C'est la forme de l'ancienne méthode, à compléter une fois."""
    return [f for f in lister(base, workflow) if "sources" not in f]


def _range(titre: Any) -> str:
    plie = unicodedata.normalize("NFD", str(titre or ""))
    return "".join(c for c in plie if not unicodedata.combining(c)).casefold()


def ecrire(base: Path, workflow: str, fiche: dict[str, Any]) -> Path:
    """Écrire une fiche, d'un seul coup.

    Le fichier est posé de côté puis remplacé : écrite en place, une fiche
    relue pendant sa réécriture serait tronquée — c'est déjà arrivé à l'aperçu
    animé d'un mode (0 octet servi en 200).
    """
    ident = str(fiche["id"])
    cible = _fichier(base, workflow, ident)
    cible.parent.mkdir(parents=True, exist_ok=True)
    brouillon = cible.with_name(f".{ident}.part.json")
    brouillon.write_text(json.dumps(fiche, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(brouillon, cible)
    return cible


def retirer(base: Path, workflow: str, ident: str) -> bool:
    """Retirer un raccourci : sa fiche ET son aperçu, quel qu'en soit le format.

    Laisser l'image derrière ferait réapparaître la vignette de l'ancien sous
    le prochain raccourci qui reprendrait le même titre.
    """
    d = dossier(base, workflow)
    existait = False
    for f in (d / f"{ident}.json", *(d / f"{ident}{ext}" for ext, _ in FORMATS)):
        if f.is_file():
            f.unlink()
            existait = existait or f.suffix == ".json"
    return existait


def cible_apercu(base: Path, workflow: str, ident: str) -> Path:
    """Où fabriquer l'aperçu d'un raccourci — SANS extension : c'est
    ``montage_video.apercu_anime`` qui la pose, selon ce que cet ffmpeg sait
    écrire."""
    return dossier(base, workflow) / ident


def apercu_fichier(base: Path, workflow: str, ident: str) -> tuple[Path, str] | None:
    """L'aperçu d'un raccourci s'il existe, avec son type."""
    for ext, mime in FORMATS:
        f = dossier(base, workflow) / f"{ident}{ext}"
        if f.is_file():
            return f, mime
    return None


def perime(fautes: Iterable[tuple[str, str]]) -> dict[str, Any] | None:
    """La forme de ce qu'un raccourci PÉRIMÉ publie : ``{"champs": [...],
    "raison": "…"}`` — ou ``None`` quand rien ne lui est reproché.

    Un raccourci vieillit sans qu'on y touche : un champ que le mode n'expose
    plus, une valeur sortie du menu, un réglage d'une autre technique que la
    sienne. Le retirer en silence effacerait ce que l'utilisateur avait nommé ;
    le publier tel quel le laisserait échouer au lancement, sans un mot. Il est
    donc publié PÉRIMÉ, avec ses champs et la raison — c'est le lanceur qui le
    grise et l'utilisateur qui décide (2026-09-19). Le jugement, lui, est
    celui d'une demande (à l'API, où vit la validation) ; ici, la forme.
    """
    lignes = [(str(champ), str(raison)) for champ, raison in fautes]
    if not lignes:
        return None
    return {"champs": sorted({champ for champ, _ in lignes}),
            "raison": " ; ".join(raison for _, raison in lignes)}


def _texte(valeur: Any) -> str:
    """Une valeur, telle qu'on la LIT sur une carte.

    Un FLOAT entier s'écrit sans sa décimale : la durée typée 12.0 s'affichait
    « 12.0 s », et se comparait au défaut déclaré « 12 » comme à un autre
    nombre — un écart annoncé là où l'utilisateur n'avait rien changé.
    """
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur)


def ecarts(valeurs: dict[str, Any] | None, defauts: dict[str, Any] | None,
           decrire: Callable[[str], dict[str, Any]],
           formats: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ce qui DIFFÈRE des défauts du mode, habillé pour l'affichage.

    Une carte qui listerait les quinze réglages d'un raccourci ne dirait rien :
    ce qui le distingue, c'est ce que l'utilisateur a changé. La comparaison
    passe par ``_texte`` des deux côtés — le défaut est déclaré brut (« 1 ») et
    la valeur est typée (1.0), et les comparer tels quels inventait un écart.

    ``formats`` (le vocabulaire déclaré) sert à replier largeur ET hauteur en un
    seul écart « Format » : l'utilisateur a choisi « 1080p, Paysage », pas
    « 1920 px » puis « 1080 px ».
    """
    lignes: list[dict[str, Any]] = []
    for champ, valeur in (valeurs or {}).items():
        if _texte(valeur) == _texte((defauts or {}).get(champ)):
            continue
        decrit = decrire(champ) or {}
        lignes.append({
            "champ": champ,
            "libelle": decrit.get("libelle") or champ,
            "valeur": valeur,
            "libelle_valeur": _libelle_valeur(valeur, decrit),
        })
    return _replier_en_format(lignes, formats)


# Les deux champs qu'un format recouvre. Des noms du DOMAINE — une largeur est
# une largeur quel que soit le mode —, pas ceux d'un nœud.
CHAMPS_DU_FORMAT: tuple[str, str] = ("width", "height")


def _entier(valeur: Any) -> int | None:
    """Une mesure en pixels, ou ``None`` si ce n'en est pas une. Un booléen est
    un entier pour Python, jamais une largeur."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, int):
        return valeur
    if isinstance(valeur, float) and valeur.is_integer():
        return int(valeur)
    return None


def format_du_couple(largeur: int, hauteur: int,
                     formats: dict[str, Any] | None) -> dict[str, Any] | None:
    """Le format déclaré que ce couple de côtés désigne, s'il y en a un.

    La règle est GÉOMÉTRIQUE, pas métier — c'est ce qui la garde vraie quand le
    vocabulaire s'allonge : largeur < hauteur ⇒ portrait (la largeur est le
    petit côté), largeur > hauteur ⇒ paysage. Un carré ne désigne aucune
    orientation ; un couple hors du vocabulaire n'est pas un format, et reste
    une largeur et une hauteur — ce qu'il est.
    """
    if largeur == hauteur or not formats:
        return None
    portrait = largeur < hauteur
    court, long_ = (largeur, hauteur) if portrait else (hauteur, largeur)
    orientation = next((o for o in (formats.get("orientations") or ())
                        if str(o.get("valeur")) == ("portrait" if portrait else "paysage")), None)
    resolution = next((r for r in (formats.get("resolutions") or ())
                       if r.get("cote_court") == court and r.get("cote_long") == long_), None)
    if orientation is None or resolution is None:
        return None
    return {"champ": "format", "libelle": "Format",
            "valeur": f"{resolution.get('valeur')} {orientation.get('valeur')}",
            "libelle_valeur": f"{resolution.get('libelle') or resolution.get('valeur')}, "
                              f"{orientation.get('libelle') or orientation.get('valeur')}"}


def _replier_en_format(lignes: list[dict[str, Any]],
                       formats: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Largeur et hauteur, repliées en un seul écart quand elles font un format.

    Les deux ensemble ne sont qu'UN choix, et l'utilisateur l'a fait dans ces
    mots-là : « 1080p, Paysage ». Les laisser en « 1920 px » puis « 1080 px »
    obligeait à recomposer le format de tête, carte après carte. Le repli prend
    la place du PREMIER des deux ; l'ordre des autres écarts ne bouge pas.
    """
    presents = [ligne for ligne in lignes if ligne["champ"] in CHAMPS_DU_FORMAT]
    if len(presents) != len(CHAMPS_DU_FORMAT):
        return lignes                     # un seul des deux a bougé : pas un format
    cotes = {ligne["champ"]: _entier(ligne["valeur"]) for ligne in presents}
    if any(cote is None for cote in cotes.values()):
        return lignes
    replie = format_du_couple(cotes["width"], cotes["height"], formats)
    if replie is None:
        return lignes
    repliees: list[dict[str, Any]] = []
    for ligne in lignes:
        if ligne["champ"] not in CHAMPS_DU_FORMAT:
            repliees.append(ligne)
        elif replie is not None:
            repliees.append(replie)
            replie = None                 # posé une fois, à la place du premier
    return repliees


def _libelle_valeur(valeur: Any, decrit: dict[str, Any]) -> str:
    """Comment une VALEUR se lit : son libellé déclaré, sinon elle-même avec son
    unité. « sepia » ne se lit pas, « Sépia » oui ; « 12 » ne dit pas de quoi,
    « 12 s » le dit."""
    declare = (decrit.get("libelles_valeurs") or {}).get(_texte(valeur))
    if declare:
        return str(declare)
    unite = decrit.get("unite")
    if isinstance(valeur, bool) or not unite:
        return _texte(valeur)
    return f"{_texte(valeur)} {unite}"

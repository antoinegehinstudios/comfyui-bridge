"""Habiller un menu : de la LISTE du fournisseur aux libellés d'un formulaire.

Une liste de choix vient toujours du fournisseur — le moteur pour un COMBO de
nœud, le catalogue pour un mode de chaîne. Ce que le fournisseur ne donne pas,
c'est de quoi la lire : « sumi-e » n'est pas « Sumi-e (lavis d'encre japonais),
famille encre & lavis ». Ces libellés sont DÉCLARÉS à la passerelle (clé
``menus`` du fichier de réconciliation), jamais recopiés dans un client — la
liste de 59 styles s'est déjà allongée deux fois.

Deux formes de déclaration, et une seule règle : la liste reste celle du
fournisseur.

  * ``libelles`` — une table écrite à la main, pour une poignée de valeurs ;
  * ``source_fichier`` — une PROJECTION à la lecture d'un fichier que le
    fournisseur tient déjà (les styles vivent dans le paquet de nœuds). Rien
    n'est recopié : le jour où le fournisseur ajoute un style, il apparaît.

Une valeur sans libellé reste elle-même. Une source illisible ne se tait pas :
elle rend un ``manque``, parce qu'un menu silencieusement dégarni ressemble
trait pour trait à un menu normal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Le fichier de styles est relu à chaque formulaire ; sa date de modification
# dit s'il a changé. Sans ce cache, décrire un workflow rouvrait 59 entrées.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# L'unité de ce que porte un champ, quand le nom du champ la fixe. Les noms
# sont ceux du domaine (`duration_s`, `width`), pas ceux d'un nœud : une durée
# est en secondes ici quel que soit le workflow. Un champ absent d'ici n'a pas
# d'unité — inventer « unités » sous un nombre ne dit rien à personne.
UNITE_DE_CHAMP: dict[str, str] = {
    "duration_s": "s", "width": "px", "height": "px",
}


def unite(champ: str, declaree: str | None = None) -> str | None:
    return declaree or UNITE_DE_CHAMP.get(champ)


def _table_du_fichier(source: dict[str, Any]) -> dict[str, Any]:
    chemin = Path(str(source.get("chemin") or ""))
    stamp = chemin.stat().st_mtime
    cle = f"{chemin}|{source.get('table')}"
    connu = _CACHE.get(cle)
    if connu and connu[0] == stamp:
        return connu[1]
    doc = json.loads(chemin.read_text(encoding="utf-8"))
    brut = doc.get(str(source.get("table") or "")) if source.get("table") else doc
    table: dict[str, Any] = {}
    if isinstance(brut, dict):
        table = {str(k): v for k, v in brut.items() if isinstance(v, dict)}
    elif isinstance(brut, list):
        # Une table en liste porte sa clé dans chaque ligne : sans elle, on ne
        # saurait pas à quelle valeur du fournisseur la ligne répond.
        for ligne in brut:
            if isinstance(ligne, dict):
                valeur = ligne.get("valeur") or ligne.get("id") or ligne.get("cle")
                if valeur:
                    table[str(valeur)] = ligne
    else:
        raise ValueError(f"« {source.get('table')} » n'est ni un objet ni une liste")
    _CACHE[cle] = (stamp, table)
    return table


def _libelles(menu: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """La table des libellés d'un menu, et ce qui a manqué pour l'obtenir."""
    if isinstance(menu.get("libelles"), dict):
        return {str(k): v for k, v in menu["libelles"].items() if isinstance(v, dict)}, None
    source = menu.get("source_fichier")
    if not isinstance(source, dict):
        return {}, "déclaration de menu sans « libelles » ni « source_fichier »"
    try:
        table = _table_du_fichier(source)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, f"source des libellés illisible ({source.get('chemin')}) : {exc}"
    # La projection : quel champ de la source porte le libellé, le résumé, le
    # groupe. Déclarée, parce qu'aucune convention ne vaut pour tous les
    # fournisseurs — ici c'est « famille » qui fait le groupe.
    projection = {"libelle": str(source.get("libelle") or "libelle"),
                  "resume": str(source.get("resume") or "resume"),
                  "groupe": str(source.get("groupe") or "groupe")}
    # « impose » : la colonne du fournisseur qui dit ce que CETTE valeur impose
    # aux autres champs (une charte : sa palette, ses interdits, sa police) —
    # un objet {clé du fournisseur: valeur lisible}. Déclarée par son nom (relayée
    # telle quelle), ou par {colonne, champs: {clé du fournisseur: champ de la
    # chaîne}} : le fournisseur parle sa langue, la réconciliation traduit, et
    # ce qui n'a pas de champ ici ne part pas. Un lanceur grise et remplit les
    # champs imposés avec. Absente, rien : le lanceur grise par la seule
    # déclaration « impose_par » du champ, sans dire de valeur.
    return {valeur: {**{cle: ligne.get(champ) for cle, champ in projection.items()},
                     **_impose_de(ligne, source.get("impose"))}
            for valeur, ligne in table.items()}, None


def _impose_de(ligne: dict[str, Any], declaration: Any) -> dict[str, Any]:
    if isinstance(declaration, str):
        colonne, champs = declaration, None
    elif isinstance(declaration, dict) and declaration.get("colonne"):
        colonne, champs = str(declaration["colonne"]), declaration.get("champs")
    else:
        return {}
    porte = ligne.get(colonne)
    if not isinstance(porte, dict):
        return {}
    if not isinstance(champs, dict):
        return {"impose": dict(porte)}
    return {"impose": {str(champ): porte[cle] for cle, champ in champs.items() if cle in porte}}


def _lignes_brutes(menu: dict[str, Any]) -> dict[str, Any]:
    """Les lignes du fournisseur telles quelles (pas la projection) : c'est sur
    elles qu'un filtre lit ce qu'une ligne porte."""
    if isinstance(menu.get("libelles"), dict):
        return {str(k): v for k, v in menu["libelles"].items() if isinstance(v, dict)}
    source = menu.get("source_fichier")
    if not isinstance(source, dict):
        return {}
    try:
        return _table_du_fichier(source)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _porte(ligne: Any, cle: str, exige: Any) -> bool:
    """Une ligne PORTE ce qu'on exige : la valeur elle-même, ou — dans une liste
    — un élément égal ou un objet dont le « nom » l'est (les temps d'une
    structure de récit sont des objets nommés)."""
    tenu = (ligne or {}).get(cle) if isinstance(ligne, dict) else None
    if isinstance(tenu, list):
        return any(item == exige or (isinstance(item, dict) and item.get("nom") == exige)
                   for item in tenu)
    if isinstance(tenu, dict):
        return exige in tenu
    return tenu == exige


def valeurs(menu: dict[str, Any] | None,
            requiert: dict[str, Any] | None = None) -> tuple[list[str], str | None]:
    """Les VALEURS qu'un menu déclaré permet — ses clés, dans l'ordre du fournisseur.

    Un menu sert d'ordinaire à HABILLER une liste que le fournisseur donne. Il
    arrive qu'il SOIT la liste : une chaîne n'a pas de moteur à interroger, et
    recopier chez elle les vingt structures de récit les figerait au jour où on
    l'a écrite. Elle nomme le menu ; la liste reste celle du fichier que le
    fournisseur tient.

    ``requiert`` : ne garder que les lignes qui portent ce que le champ exige
    ({champ de la ligne: valeur}) — un plan de révélation veut une structure
    qui a une accroche (``{"temps": "hook"}``), et vingt et une structures
    offertes dont dix-huit font échouer le plan sont des fantômes (mesuré le
    2026-09-17). Le filtre lit les lignes du fournisseur, pas la projection.

    Rend ``(valeurs, manque)`` : une source illisible rend une liste vide ET la
    raison, jamais une liste vide seule — un menu silencieusement dégarni
    ressemble trait pour trait à un menu normal.
    """
    table, manque = _libelles(menu or {})
    if requiert:
        lignes = _lignes_brutes(menu or {})
        return [v for v in table
                if all(_porte(lignes.get(v), cle, exige) for cle, exige in requiert.items())], manque
    return list(table), manque


def retitrer(source: dict[str, Any] | None, menu: dict[str, Any] | None) -> dict[str, Any]:
    """La SOURCE d'un champ (le fichier, la table qu'il nomme par « options_depuis »)
    habillée par le menu déclaré AU NOM du champ : ce menu retitre le champ (son
    libellé, son aide) et, valeur par valeur, ce qu'il connaît de la source — il
    n'EFFACE jamais les libellés que la source apporte pour les valeurs qu'il
    ignore. Mesuré le 2026-09-24 : le menu « ambiance » de l'encre (lanterne,
    chandelle…) recouvrait tout le catalogue d'ambiances d'une image, dont les
    valeurs s'affichaient en clés nues dans le lanceur. Une source illisible garde
    les libellés du menu tels quels : mieux vaut un titre que rien."""
    source, menu = dict(source or {}), dict(menu or {})
    table, _manque = _libelles(source) if source else ({}, None)
    propres = menu.get("libelles") if isinstance(menu.get("libelles"), dict) else {}
    fusion = {**source, **{k: v for k, v in menu.items() if k != "libelles"}}
    if table:
        fusion["libelles"] = {**table, **{k: v for k, v in propres.items() if k in table}}
    elif propres:
        fusion["libelles"] = dict(propres)
    return fusion


def choix(options: list[Any] | tuple[Any, ...] | None,
          menu: dict[str, Any] | None) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Les options du fournisseur, habillées de ce qui a été déclaré.

    Rend ``(choix, manque)``. ``choix`` est ``None`` quand il n'y a pas de
    liste : un champ libre n'est pas un menu vide.
    """
    if options is None:
        return None, None
    table, manque = _libelles(menu) if menu else ({}, None)
    habilles: list[dict[str, Any]] = []
    for option in options:
        valeur = str(option)
        decrit = table.get(valeur) or {}
        entree: dict[str, Any] = {"valeur": option,
                                  "libelle": str(decrit.get("libelle") or valeur)}
        if decrit.get("resume"):
            entree["resume"] = decrit["resume"]
        if decrit.get("groupe"):
            entree["groupe"] = decrit["groupe"]
        if isinstance(decrit.get("impose"), dict):
            entree["impose"] = dict(decrit["impose"])
        habilles.append(entree)
    return habilles, manque

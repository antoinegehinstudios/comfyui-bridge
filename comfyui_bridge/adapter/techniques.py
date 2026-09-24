"""Où vivent les TECHNIQUES, et comment on les lit.

Une chaîne est un PLAN : ses étapes nomment des rôles (« deroulement »,
« conclusion »), jamais un graphe de peinture. Une technique dit quel graphe
tient chaque rôle, ce qu'il reçoit, quels réglages elle ajoute et quels
contrôles elle porte.

Antoine, 2026-09-16 : « la mention de brume ne doit pas être tenue par le
workflow de la passerelle : cela veut dire qu'il porte une dépendance à la
brume et devra se faire doublon pour faire autrement ». Avant ce jour, deux
chaînes jumelles se recopiaient à onze étapes près deux — une technique de plus
était une chaîne de plus. C'est maintenant un FICHIER de plus, et rien d'autre :
ni la chaîne, ni le code ne nomment une technique (la règle `flux-hors-du-code`
du socle tient ce point).

Les fichiers vivent à côté des chaînes, dans le dossier de données de la machine
(`_data/techniques/*.json`) ; le paquet en garde une copie de référence
(`resources/techniques-exemples/`), comme pour les chaînes — sans elle, une
installation neuve repartirait sur l'ancienne version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..core.chaine import Technique, lire_technique
from ..core.errors import WorkflowMappingError

# Le nom du dossier, à côté de `chaines/` et `workflows/` dans les données de la
# machine. Pas une variable d'environnement de plus : une technique n'est pas un
# réglage de poste, c'est une donnée du flux.
DOSSIER = "techniques"


def dossier(data_dir: Path | str | None) -> Path | None:
    return (Path(data_dir) / DOSSIER) if data_dir else None


def lire_toutes(data_dir: Path | str | None) -> dict[str, Technique]:
    """Les techniques déclarées sur cette machine, par leur nom.

    Aucune n'est un cas particulier : le fichier se nomme lui-même (clé
    « technique »), et son nom de fichier ne sert qu'à le trouver. Un fichier
    illisible ou qui ne tient pas est refusé ICI, en le nommant : découvert au
    cinquième run d'une chaîne, il aurait fait perdre les quatre précédents.
    """
    d = dossier(data_dir)
    if d is None or not d.is_dir():
        return {}
    lues: dict[str, Technique] = {}
    for fichier in sorted(d.glob("*.json")):
        technique = lire_fichier(fichier)
        if technique.nom in lues:
            raise WorkflowMappingError(
                f"deux fichiers déclarent la technique {technique.nom!r} dans {d} — "
                f"laquelle l'emporterait ?")
        lues[technique.nom] = technique
    return lues


def lire_fichier(fichier: Path) -> Technique:
    try:
        brut = json.loads(Path(fichier).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(
            f"technique {Path(fichier).stem!r} : impossible de lire {fichier} : {exc}") from exc
    return lire_technique(brut, Path(fichier).stem)


def vues(techniques: dict[str, Technique], graphe_de: Callable[[str], Any] | None = None
         ) -> list[dict[str, Any]]:
    """Les techniques telles qu'un lanceur les rend : de quoi peupler une liste.

    Le libellé et le résumé viennent du fichier, jamais d'une table écrite dans
    un client — c'est la même règle que pour les menus : la liste appartient au
    fournisseur. Chacune dit aussi ce qu'elle NE règle PAS (`manques`), détecté
    parmi ses voisines de la même chaîne.
    """
    return [{"valeur": nom, "libelle": technique.libelle or nom, "resume": technique.resume,
             "par_defaut": bool(technique.par_defaut), "manques": manques(technique, techniques, graphe_de)}
            for nom, technique in sorted(techniques.items())]


def manques(technique: Technique, voisines: dict[str, Technique] | None = None,
            graphe_de: Callable[[str], Any] | None = None) -> list[dict[str, str]]:
    """Ce que cette technique NE RÈGLE PAS, détecté — jamais un faux champ à sa
    place. Antoine, 2026-09-24 : « s'il n'y a rien dans ComfyUI en ce sens, on
    ne met pas de faux champ dans maestro ; ce manque peut être mentionné quand
    il est détecté », puis « la règle doit compter partout, pas seulement pour le
    graphe négation ». Trois détections, de la plus parlante à la plus nue :

    * `declare` — une section de son fichier dit `applique: false` avec un `dit`
      (« negatif » sous une technique sans guidage) : le mot de l'auteur ;
    * `graphe` — un champ qu'une technique VOISINE (même chaîne) expose et que
      celle-ci n'a pas, dont son graphe tient pourtant l'entrée à une valeur
      fixe (« cfg » à 1.0 sur le nœud 3) : lu dans le graphe, avec la valeur ;
    * `absent` — le même champ, que rien de son graphe ne nomme : elle ne
      l'expose pas, il ne se règle pas sous elle.

    Un champ voisin se reconnaît par son nom, et par les entrées de nœud où la
    voisine le branche (« 3.cfg » : l'entrée `cfg`). Sans lecteur de graphes,
    la deuxième détection se tait — on ne dit « tenu à » que ce qu'on a lu.
    """
    declares = {str(cle): section for cle, section in (technique.donnees or {}).items()
                if isinstance(section, dict) and section.get("applique") is False}
    absents: dict[str, dict[str, Any]] = {}
    for nom_v, voisine in sorted((voisines or {}).items()):
        if nom_v == technique.nom:
            continue
        for cle, champ in voisine.champs.items():
            if cle in technique.champs:
                continue
            entree = absents.setdefault(cle, {"libelle": champ.libelle or cle, "entrees": set()})
            for role in voisine.roles.values():
                for nid_entree, valeur in (role.inputs or {}).items():
                    if isinstance(valeur, str) and valeur.strip() == f"${cle}" and "." in nid_entree:
                        entree["entrees"].add(nid_entree.split(".", 1)[1])
    trouves: list[dict[str, str]] = []
    for cle in sorted(set(declares) | set(absents)):
        libelle = str(absents.get(cle, {}).get("libelle") or cle)
        if cle in declares:
            trouves.append({"quoi": cle, "libelle": libelle, "detecte": "declare",
                            "dit": str(declares[cle].get("dit") or f"« {cle} » n'est pas lu par cette technique")})
            continue
        tenus = _tenus_par_le_graphe(technique, {cle} | absents[cle]["entrees"], graphe_de)
        if tenus:
            trouves.append({"quoi": cle, "libelle": libelle, "detecte": "graphe",
                            "dit": "son graphe le tient à " + " ; ".join(
                                f"« {valeur} » (nœud {nid}, {classe})" for nid, classe, valeur in tenus)
                            + " sans l'exposer"})
        else:
            trouves.append({"quoi": cle, "libelle": libelle, "detecte": "absent",
                            "dit": "cette technique ne l'expose pas : il ne se règle pas sous elle"})
    return trouves


def _tenus_par_le_graphe(technique: Technique, noms: set[str],
                         graphe_de: Callable[[str], Any] | None) -> list[tuple[str, str, str]]:
    """(nœud, classe, valeur) pour chaque entrée LITTÉRALE d'un graphe des rôles
    de la technique qui porte l'un de ces noms — une valeur écrite dans le
    graphe, pas un lien vers un autre nœud, et pas une entrée que le rôle
    BRANCHE lui-même (« 61.fond » sous « $brume » : l'entrée est réglée, sous
    un autre nom — la valeur écrite dans le graphe n'est que son défaut)."""
    if graphe_de is None:
        return []
    tenus: list[tuple[str, str, str]] = []
    vus: set[str] = set()
    for role in technique.roles.values():
        if role.workflow in vus:
            continue
        vus.add(role.workflow)
        graphe = graphe_de(role.workflow)
        if not isinstance(graphe, dict):
            continue
        branchees = {str(cle) for cle in (role.inputs or {}) if "." in str(cle)}
        for nid, noeud in graphe.items():
            entrees = noeud.get("inputs") if isinstance(noeud, dict) else None
            if not isinstance(entrees, dict):
                continue
            for nom in sorted(noms):
                if f"{nid}.{nom}" in branchees:
                    continue
                if nom in entrees and not isinstance(entrees[nom], (list, dict)):
                    valeur = str(entrees[nom])
                    tenus.append((str(nid), str(noeud.get("class_type") or "?"),
                                  valeur if len(valeur) <= 60 else valeur[:57] + "…"))
                    break
    return tenus

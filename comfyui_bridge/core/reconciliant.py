"""Les RÉCONCILIANTS : un seul élément par techno extérieure, le seul à lire sa source.

Antoine, 2026-09-25 : « si chaque nouveauté promue par Héraldiste, TOUT le workflow tenu par
maestro doit indépendamment se mettre à jour, c'est trop d'effort et voué au pourrissement ; les
workflows servis au catalogue de maestro doivent être standardisés suivant un template fait
d'éléments réconciliant avec les éléments extérieurs : seul cet élément réconciliant se met à jour,
une fois, pour que tout workflow obtienne la nouveauté. » Puis : « lance le chantier de
standardisation et réconciliation unique par techno ».

Un réconciliant (`resources/reconciliants/<rôle>.json`) est ce que les pratiques reconnues
nomment un MODÈLE CANONIQUE (Enterprise Integration Patterns) tenu par une COUCHE ANTICORRUPTION
(Domain-Driven Design) : il est le seul à connaître la source — ses graphes, ses menus, les clés de
son récit — et il range ce qu'elle sert dans des EMPLACEMENTS PAR RÔLE (ce qu'une création peut en
faire : la consigne, les fichiers à poser, la texture, la typographie, les images de référence…),
jamais par nom de champ de la source. Une chaîne le DÉCLARE et le BRANCHE :

    "reconciliants": {"charte": {"media": "image", "prompt": "$prompt", "livrable": "$livraison.livrable"}}

puis ne lit que ses emplacements : ``$charte.consigne.style_en``. Au chargement, le réconciliant est
DÉPLIÉ dans la chaîne — ses champs en tête, ses étapes à leur place, ses emplacements réécrits en
renvois — : la chaîne qui s'exécute est une chaîne ordinaire, et rien d'autre n'a à le savoir.

Une nouveauté de la source qui entre dans un rôle existant ne touche que le réconciliant : toute
chaîne qui le branche l'a au chargement suivant. Un rôle vraiment neuf est un emplacement de plus :
chaque mode publie ceux qu'il ne prend pas (``non_pris``), et les FAITS que la source sert sans
qu'aucun emplacement ne les prenne sont dits (`faits_non_servis`), jamais perdus en silence.

LE LANGAGE D'UN RÉCONCILIANT — tout ce qu'il sait écrire :

* ``"@nom"`` (une valeur entière) : ce que la chaîne BRANCHE sous ``nom`` ; si elle ne le branche
  pas, l'entrée ou l'élément de liste qui le porte disparaît (une entrée de nœud qu'on ne donne pas
  n'est pas écrite : le nœud garde son défaut).
* ``{"@": "nom", "sinon": v}`` : ce qui est branché, ou ``v``.
* ``"@si"`` / ``"@sauf"`` dans un objet : l'objet n'existe que si le branchement est donné
  (``"@si": "nom"``) ou vaut une valeur (``"@si": {"nom": "video"}``, ``{"nom": ["a", "b"]}``) ;
  ``"@sauf"`` dit le contraire.
* ``{"@selon": "nom", "<valeur>": x, …, "@autre": y}`` : la branche de la valeur branchée ; sans
  branche pour elle (ni « @autre »), la valeur n'existe pas — comme un « @nom » non branché.

Rien d'autre : un réconciliant décrit ce qu'il apporte, ce n'est pas un script. Ce module ne
connaît aucune source : il lit ce que les fichiers déclarent.

LES PROMESSES (second temps, même jour). Antoine : « j'ai demandé un template : la prise de charte doit
y être présente, et à son poids exactement comme c'est défini par le template ; les spécificités propres
au workflow sont portées ailleurs, mais le template délivre les choses qu'il porte, sans mentir, sans
faux paramètre que le workflow ne sait pas tenir ». Un réconciliant déclare ses PROMESSES — ce que la
source impose à toute création qui la prend (« promesses » : un libellé et les emplacements qui la
tiennent). Une chaîne tient chaque promesse, en lisant l'un de ses emplacements (elle-même, ou par sa
technique), ou la DÉCLINE avec sa raison (« sans » : {promesse: raison}) ; ni l'un ni l'autre, ou les
deux, est refusé au chargement (`juger_les_promesses`). Une promesse déclinée est DITE — le lanceur
l'écrit sous le champ qui prend la source —, jamais tue. Le champ qui « prend » la source (« prise » :
{champ, sauf}) est celui sous lequel ces promesses valent.

LES TECHNIQUES lisent les emplacements comme la chaîne (« $analyse.reperes », « $charte.texte.police ») :
elles sont réécrites POUR la chaîne qui les emploie (`reecrire_technique`, depuis `core.chaine.
techniques_pour`), et une technique qui lirait le récit d'une étape de réconciliant est refusée comme une
chaîne.
"""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .errors import WorkflowMappingError

DOSSIER = Path(__file__).resolve().parent.parent / "adapter" / "resources" / "reconciliants"

# Où une étape d'un réconciliant se pose dans la chaîne qui le branche. Trois places, parce que
# trois moments suffisent à toute source : AVANT tout (ce qu'elle impose pèse sur le reste), APRÈS
# la livraison (ce qu'elle mesure se mesure sur le fichier livré), AVANT le contrôle final (ce
# qu'elle constate se dit avec les autres constats).
PLACES: tuple[str, ...] = ("debut", "apres_livraison", "avant_controle")

_CLES: frozenset[str] = frozenset({"reconciliant", "version", "resume", "source", "requiert", "branchements",
                                   "champs", "etapes", "emplacements", "prise", "promesses", "_lire_moi"})
# Le branchement réservé d'une chaîne : les promesses qu'elle décline, chacune avec sa raison.
SANS = "sans"
_CLES_SOURCE: frozenset[str] = frozenset({"techno", "adresse", "schemas", "graphes", "menus", "faits"})
_CLES_BRANCHEMENT: frozenset[str] = frozenset({"requis", "valeurs", "aide"})
_DIRECTIVES: frozenset[str] = frozenset({"@si", "@sauf", "@selon", "@autre", "@", "sinon"})
_NOM = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER = re.compile(r"^@([a-z][a-z0-9_]*)$")
_CHEMIN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_ABSENT = object()


# -- lire ---------------------------------------------------------------------------------------

def lire(nom: str, dossier: Path | None = None) -> dict[str, Any]:
    """Le réconciliant `nom` tel que son fichier le déclare — refusé s'il manque ou ne tient pas."""
    chemin = (Path(dossier) if dossier else DOSSIER) / f"{nom}.json"
    if not chemin.is_file():
        connus = ", ".join(sorted(tous(dossier))) or "aucun"
        raise WorkflowMappingError(
            f"aucun réconciliant « {nom} » ({chemin.name} manque dans {chemin.parent}) — connus : {connus}")
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"réconciliant « {nom} » : impossible de lire {chemin} : {exc}") from exc
    _tenir(nom, brut)
    return brut


def tous(dossier: Path | None = None) -> dict[str, dict[str, Any]]:
    """Tous les réconciliants déclarés, par rôle. Deux réconciliants pour une même techno sont
    refusés : c'est la règle même (« réconciliation unique par techno »)."""
    racine = Path(dossier) if dossier else DOSSIER
    lus: dict[str, dict[str, Any]] = {}
    for chemin in sorted(racine.glob("*.json")) if racine.is_dir() else []:
        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowMappingError(f"réconciliant {chemin.name} : illisible : {exc}") from exc
        _tenir(chemin.stem, brut)
        lus[chemin.stem] = brut
    par_techno: dict[str, str] = {}
    for role, brut in lus.items():
        techno = str(brut["source"]["techno"]).strip().lower()
        if techno in par_techno:
            raise WorkflowMappingError(
                f"deux réconciliants lisent « {brut['source']['techno']} » ({par_techno[techno]} et {role}) : "
                "une techno n'a qu'un réconciliant, sinon une nouveauté se câble deux fois")
        par_techno[techno] = role
    return lus


def _tenir(nom: str, brut: Any) -> None:
    """Refuser, à la lecture, tout ce qu'un fichier de réconciliant peut prouver faux."""
    ctx = f"réconciliant « {nom} »"
    if not isinstance(brut, dict) or brut.get("reconciliant") != nom:
        raise WorkflowMappingError(f"{ctx} : le fichier doit se nommer lui-même (« reconciliant »: « {nom} »)")
    inconnues = sorted(set(brut) - _CLES)
    if inconnues:
        raise WorkflowMappingError(f"{ctx} : clé(s) inconnue(s) {', '.join(inconnues)} (admises : {', '.join(sorted(_CLES))})")
    if not _VERSION.match(str(brut.get("version") or "")):
        raise WorkflowMappingError(f"{ctx} : « version » s'écrit majeure.mineure.correctif (versionnage sémantique)")
    source = brut.get("source")
    if not isinstance(source, dict) or not str(source.get("techno") or "").strip():
        raise WorkflowMappingError(f"{ctx} : « source » nomme la techno qu'il est seul à lire (« techno »)")
    if set(source) - _CLES_SOURCE:
        raise WorkflowMappingError(f"{ctx} : « source » porte des clés inconnues {sorted(set(source) - _CLES_SOURCE)}")
    for cle in ("graphes", "menus"):
        if not isinstance(source.get(cle, []), list) or not all(isinstance(g, str) and g for g in source.get(cle, [])):
            raise WorkflowMappingError(f"{ctx} : « source.{cle} » est une liste de noms")
    faits = source.get("faits")
    if faits is not None and (not isinstance(faits, dict) or not faits.get("menu") or not faits.get("colonne")
                              or not isinstance(faits.get("lus"), list)):
        raise WorkflowMappingError(f"{ctx} : « source.faits » dit {{menu, colonne, lus: [...], libelles?}}")
    branchements = brut.get("branchements") or {}
    if not isinstance(branchements, dict):
        raise WorkflowMappingError(f"{ctx} : « branchements » est un objet {{nom: {{requis, valeurs, aide}}}}")
    for b, decl in branchements.items():
        if not _NOM.match(str(b)) or not isinstance(decl, dict) or set(decl) - _CLES_BRANCHEMENT:
            raise WorkflowMappingError(f"{ctx} : branchement {b!r} mal déclaré (clés admises : requis, valeurs, aide)")
    if not isinstance(brut.get("champs", {}), dict):
        raise WorkflowMappingError(f"{ctx} : « champs » est un objet {{nom: champ}}")
    etapes = brut.get("etapes", [])
    if not isinstance(etapes, list):
        raise WorkflowMappingError(f"{ctx} : « etapes » est une liste de {{place, etape}}")
    for item in etapes:
        if (not isinstance(item, dict) or item.get("place") not in PLACES or not isinstance(item.get("etape"), dict)
                or not item["etape"].get("id")):
            raise WorkflowMappingError(
                f"{ctx} : chaque étape s'écrit {{\"place\": {' | '.join(PLACES)}, \"etape\": {{\"id\": …}}}} ({item!r:.120})")
    emplacements = brut.get("emplacements", {})
    if not isinstance(emplacements, dict) or not all(_CHEMIN.match(str(k)) for k in emplacements):
        raise WorkflowMappingError(f"{ctx} : « emplacements » est un objet {{chemin.a.points: valeur}}")
    if not isinstance(brut.get("requiert", []), list):
        raise WorkflowMappingError(f"{ctx} : « requiert » est une liste de rôles")
    prise = brut.get("prise")
    if prise is not None and (not isinstance(prise, dict) or not prise.get("champ") or not isinstance(prise.get("sauf", []), list)
                              or prise["champ"] not in (brut.get("champs") or {})):
        raise WorkflowMappingError(f"{ctx} : « prise » nomme l'un de ses champs et les valeurs qui ne prennent pas la source "
                                   "({champ, sauf: [...]})")
    promesses = brut.get("promesses") or {}
    if not isinstance(promesses, dict):
        raise WorkflowMappingError(f"{ctx} : « promesses » est un objet {{promesse: {{libelle, emplacements: [...]}}}}")
    if promesses and prise is None:
        raise WorkflowMappingError(f"{ctx} : des promesses sans « prise » — sous quel champ vaudraient-elles ?")
    for ident, promesse in promesses.items():
        if (not _NOM.match(str(ident)) or not isinstance(promesse, dict) or not str(promesse.get("libelle") or "").strip()
                or not isinstance(promesse.get("emplacements"), list) or not promesse["emplacements"]):
            raise WorkflowMappingError(f"{ctx} : la promesse {ident!r} dit son « libelle » et les « emplacements » qui la tiennent")
        for chemin in promesse["emplacements"]:
            if not any(k == chemin or k.startswith(f"{chemin}.") for k in emplacements):
                raise WorkflowMappingError(f"{ctx} : la promesse {ident!r} nomme l'emplacement {chemin!r}, qu'il ne déclare pas")
    # Chaque branchement employé est déclaré : une faute de frappe dans « @acroche » ferait
    # disparaître une entrée sans un mot. Pour la même raison, les branches d'un « @selon » et les
    # valeurs d'un « @si » sont parmi celles que le branchement déclare, quand il en déclare.
    contenu = {k: brut.get(k) for k in ("champs", "etapes", "emplacements")}
    employes = _noms_employes(contenu)
    inconnus = sorted(employes - set(branchements))
    if inconnus:
        raise WorkflowMappingError(f"{ctx} : branchement(s) employé(s) sans être déclaré(s) : {', '.join(inconnus)}")
    for nom_b, valeur in _valeurs_employees(contenu):
        permises = (branchements.get(nom_b) or {}).get("valeurs")
        if permises and valeur not in permises:
            raise WorkflowMappingError(
                f"{ctx} : « {valeur} » n'est pas une valeur du branchement « {nom_b} » (les siennes : {', '.join(map(str, permises))})")


def _valeurs_employees(valeur: Any) -> list[tuple[str, Any]]:
    """Les (branchement, valeur) qu'un « @selon » et un « @si » / « @sauf » à valeur nomment."""
    trouves: list[tuple[str, Any]] = []
    if isinstance(valeur, list):
        for v in valeur:
            trouves += _valeurs_employees(v)
    elif isinstance(valeur, dict):
        if isinstance(valeur.get("@selon"), str):
            trouves += [(valeur["@selon"], k) for k in valeur if not k.startswith("@")]
        for cle in ("@si", "@sauf"):
            regle = valeur.get(cle)
            if isinstance(regle, dict):
                for nom_b, attendu in regle.items():
                    trouves += [(str(nom_b), a) for a in (attendu if isinstance(attendu, list) else [attendu])]
        for k, v in valeur.items():
            if k not in ("@si", "@sauf"):
                trouves += _valeurs_employees(v)
    return trouves


def _noms_employes(valeur: Any) -> set[str]:
    noms: set[str] = set()
    if isinstance(valeur, str):
        m = _PLACEHOLDER.match(valeur)
        if m:
            noms.add(m.group(1))
    elif isinstance(valeur, list):
        for v in valeur:
            noms |= _noms_employes(v)
    elif isinstance(valeur, dict):
        for cle in ("@si", "@sauf"):
            regle = valeur.get(cle)
            if isinstance(regle, str):
                noms.add(regle)
            elif isinstance(regle, dict):
                noms |= {str(k) for k in regle}
        for cle in ("@selon", "@"):
            if isinstance(valeur.get(cle), str):
                noms.add(valeur[cle])
        for k, v in valeur.items():
            if k not in ("@si", "@sauf", "@selon", "@"):
                noms |= _noms_employes(v)
    return noms


# -- instancier ---------------------------------------------------------------------------------

def _condition(regle: Any, branches: dict[str, Any], ctx: str) -> bool:
    if isinstance(regle, str):
        return regle in branches
    if isinstance(regle, dict) and len(regle) == 1:
        nom, attendu = next(iter(regle.items()))
        if nom not in branches:
            return False
        return branches[nom] in (attendu if isinstance(attendu, list) else [attendu])
    raise WorkflowMappingError(f"{ctx} : « @si » / « @sauf » nomme un branchement, ou {{branchement: valeur(s)}} ({regle!r})")


def _instancier(valeur: Any, branches: dict[str, Any], ctx: str) -> Any:
    """La valeur du réconciliant pour CES branchements — ou `_ABSENT` si elle n'existe pas ici."""
    if isinstance(valeur, str):
        m = _PLACEHOLDER.match(valeur)
        if m:
            return copy.deepcopy(branches[m.group(1)]) if m.group(1) in branches else _ABSENT
        return valeur
    if isinstance(valeur, list):
        return [x for x in (_instancier(v, branches, ctx) for v in valeur) if x is not _ABSENT]
    if not isinstance(valeur, dict):
        return valeur
    if "@si" in valeur and not _condition(valeur["@si"], branches, ctx):
        return _ABSENT
    if "@sauf" in valeur and _condition(valeur["@sauf"], branches, ctx):
        return _ABSENT
    reste = {k: v for k, v in valeur.items() if k not in ("@si", "@sauf")}
    if "@selon" in reste:
        # Une valeur sans branche (et sans « @autre ») n'existe pas pour ce branchement : une entrée de
        # nœud propre à la vidéo n'est pas écrite pour une image. Les branches sont vérifiées contre les
        # valeurs que le branchement déclare (`_tenir`) : une faute de frappe n'y passe pas.
        nom = reste["@selon"]
        branche = str(branches.get(nom)) if nom in branches else None
        if branche is not None and branche in reste and not branche.startswith("@"):
            return _instancier(reste[branche], branches, ctx)
        if "@autre" in reste:
            return _instancier(reste["@autre"], branches, ctx)
        return _ABSENT
    if "@" in reste:
        if set(reste) - {"@", "sinon"}:
            raise WorkflowMappingError(f"{ctx} : {{\"@\": nom, \"sinon\": valeur}} n'admet rien d'autre ({sorted(reste)})")
        nom = reste["@"]
        return copy.deepcopy(branches[nom]) if nom in branches else _instancier(reste.get("sinon"), branches, ctx)
    sorti: dict[str, Any] = {}
    for k, v in reste.items():
        if k.startswith("@"):
            raise WorkflowMappingError(f"{ctx} : directive inconnue « {k} » (connues : {', '.join(sorted(_DIRECTIVES))})")
        x = _instancier(v, branches, ctx)
        if x is not _ABSENT:
            sorti[k] = x
    return sorti


def _sans(ctx: str, role: str, reconciliant: dict[str, Any], branches: dict[str, Any]) -> dict[str, str]:
    """Les promesses que la chaîne décline, chacune avec sa raison — vérifiées contre celles du réconciliant."""
    sans = branches.get(SANS, {})
    promesses = reconciliant.get("promesses") or {}
    if not isinstance(sans, dict) or not all(isinstance(r, str) and r.strip() for r in sans.values()):
        raise WorkflowMappingError(f"{ctx} : « {SANS} » du réconciliant « {role} » est un objet {{promesse: raison}}, "
                                   "chaque raison écrite")
    inconnues = sorted(set(sans) - set(promesses))
    if inconnues:
        raise WorkflowMappingError(
            f"{ctx} : le réconciliant « {role} » ne promet pas {', '.join(inconnues)} "
            f"(ses promesses : {', '.join(sorted(promesses)) or 'aucune'})")
    return {str(k): str(v).strip() for k, v in sans.items()}


def _verifier_branchements(ctx: str, role: str, reconciliant: dict[str, Any], branches: Any) -> dict[str, Any]:
    declares = reconciliant.get("branchements") or {}
    if not isinstance(branches, dict):
        raise WorkflowMappingError(f"{ctx} : le réconciliant « {role} » se branche par un objet {{branchement: valeur}}")
    branches = {k: v for k, v in branches.items() if k != SANS}
    inconnus = sorted(set(branches) - set(declares))
    if inconnus:
        raise WorkflowMappingError(
            f"{ctx} : le réconciliant « {role} » ne connaît pas le(s) branchement(s) {', '.join(inconnus)} "
            f"(les siens : {', '.join(sorted(declares))})")
    manquants = sorted(b for b, d in declares.items() if d.get("requis") and b not in branches)
    if manquants:
        raise WorkflowMappingError(
            f"{ctx} : le réconciliant « {role} » exige le(s) branchement(s) {', '.join(manquants)} — "
            + " ; ".join(f"{b} : {declares[b].get('aide') or '?'}" for b in manquants))
    for b, v in branches.items():
        permises = declares[b].get("valeurs")
        if permises and not (isinstance(v, str) and v.startswith("$")) and v not in permises:
            raise WorkflowMappingError(
                f"{ctx} : branchement « {b} » du réconciliant « {role} » : {v!r} n'est pas l'une de {permises}")
    return dict(branches)


# -- déplier ------------------------------------------------------------------------------------

def _renvois(valeur: Any):
    if isinstance(valeur, str):
        if valeur.startswith("$"):
            yield valeur[1:]
    elif isinstance(valeur, dict):
        for v in valeur.values():
            yield from _renvois(v)
    elif isinstance(valeur, list):
        for v in valeur:
            yield from _renvois(v)


def _lire_chemin(valeur: Any, chemin: list[str]) -> Any:
    for pas in chemin:
        if not isinstance(valeur, dict) or pas not in valeur:
            return _ABSENT
        valeur = valeur[pas]
    return valeur


def _reecrire(valeur: Any, emplacements: dict[str, dict[str, Any]], lus: dict[str, set[str]], ctx: str) -> Any:
    """Remplacer chaque « $<rôle>.<emplacement> » par ce que le réconciliant y range."""
    if isinstance(valeur, str) and valeur.startswith("$"):
        tete, _, chemin = valeur[1:].partition(".")
        if tete not in emplacements or not chemin:
            return valeur            # un champ, une étape de la chaîne, le champ du réconciliant lui-même
        places = emplacements[tete]
        if chemin in places:
            lus[tete].add(chemin)
            return copy.deepcopy(places[chemin])
        morceaux = chemin.split(".")
        for i in range(len(morceaux) - 1, 0, -1):
            base = ".".join(morceaux[:i])
            if base in places:
                trouve = _lire_chemin(places[base], morceaux[i:])
                if trouve is not _ABSENT:
                    lus[tete].add(base)
                    return copy.deepcopy(trouve)
                break
        raise WorkflowMappingError(
            f"{ctx} : « {valeur} » — le réconciliant « {tete} » n'a pas d'emplacement « {chemin} » ; ce qu'il "
            f"sert ne se lit que par ses emplacements ({', '.join(sorted(places))})")
    if isinstance(valeur, dict):
        return {k: _reecrire(v, emplacements, lus, ctx) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_reecrire(v, emplacements, lus, ctx) for v in valeur]
    return valeur


def _refuser_les_lectures_directes(ctx: str, registre: dict[str, dict[str, Any]], instances: dict[str, dict[str, Any]],
                                   champs: dict[str, Any], etapes: list[Any], livrable: Any) -> None:
    """Une chaîne ne lit une source que par son réconciliant : ni ses graphes, ni ses menus, ni le
    récit des étapes qu'il apporte — et ne redéclare ni ses champs ni ses étapes."""
    graphes = {g: r for r, rec in registre.items() for g in rec["source"].get("graphes", [])}
    menus = {m: r for r, rec in registre.items() for m in rec["source"].get("menus", [])}
    etapes_r = {e["id"]: r for r, inst in instances.items() for _p, e in inst["etapes"]}
    champs_r = {c: r for r, inst in instances.items() for c in inst["champs"]}

    def techno(role: str) -> str:
        return str(registre[role]["source"]["techno"])

    for etape in etapes:
        if not isinstance(etape, dict):
            continue
        ident = str(etape.get("id") or "")
        if ident in etapes_r:
            raise WorkflowMappingError(
                f"{ctx} : l'étape {ident!r} est celle du réconciliant « {etapes_r[ident]} » — la chaîne ne la redéclare pas")
        rendre = etape.get("rendre")
        workflow = rendre.get("workflow") if isinstance(rendre, dict) else None
        if workflow in graphes:
            role = graphes[workflow]
            raise WorkflowMappingError(
                f"{ctx} : l'étape {ident!r} rend « {workflow} », qui lit {techno(role)} en direct — une source ne se "
                f"lit que par son réconciliant : déclarer « reconciliants »: {{« {role} »: …}} et lire ses emplacements")
        for renvoi in list(_renvois(etape)):
            tete = renvoi.split(".", 1)[0]
            if tete in etapes_r and tete not in instances:
                raise WorkflowMappingError(
                    f"{ctx} : l'étape {ident!r} lit « ${renvoi} », le récit de l'étape du réconciliant « {etapes_r[tete]} » — "
                    f"passer par ses emplacements « ${etapes_r[tete]}.… »")
    for nom, champ in champs.items():
        if nom in champs_r:
            raise WorkflowMappingError(
                f"{ctx} : le champ {nom!r} est celui du réconciliant « {champs_r[nom]} » — la chaîne ne le redéclare pas")
        depuis = champ.get("options_depuis") if isinstance(champ, dict) else None
        menu = depuis.get("menu") if isinstance(depuis, dict) else None
        if menu in menus:
            role = menus[menu]
            raise WorkflowMappingError(
                f"{ctx} : le champ {nom!r} tire ses choix du menu « {menu} », qui est celui de {techno(role)} — "
                f"il n'appartient qu'au réconciliant « {role} »")
    if isinstance(livrable, str) and livrable.startswith("$"):
        tete = livrable[1:].split(".", 1)[0]
        if tete in etapes_r:
            raise WorkflowMappingError(f"{ctx} : le livrable « {livrable} » désigne une étape du réconciliant « {etapes_r[tete]} »")


def _assembler(ctx: str, propres: list[Any], instances: dict[str, dict[str, Any]],
               branches: dict[str, dict[str, Any]]) -> list[Any]:
    debut: list[Any] = []
    apres: dict[str, list[Any]] = defaultdict(list)
    avant_controle: list[Any] = []
    ids = [e.get("id") for e in propres if isinstance(e, dict)]
    for role, inst in instances.items():
        for place, etape in inst["etapes"]:
            if place == "debut":
                debut.append(etape)
            elif place == "avant_controle":
                avant_controle.append(etape)
            else:
                livrable = branches[role].get("livrable")
                tete = livrable[1:].split(".", 1)[0] if isinstance(livrable, str) and livrable.startswith("$") else None
                if tete not in ids:
                    raise WorkflowMappingError(
                        f"{ctx} : le réconciliant « {role} » pose l'étape {etape['id']!r} après la livraison — le "
                        f"branchement « livrable » doit désigner le fichier livré par une étape de la chaîne "
                        f"(« $<étape>.livrable » ; trouvé : {livrable!r})")
                apres[tete].append(etape)
    dernier = len(propres) - 1 if propres and isinstance(propres[-1], dict) and "verifier" in propres[-1] else None
    sortie = list(debut)
    for rang, etape in enumerate(propres):
        if rang == dernier:
            sortie.extend(avant_controle)
        sortie.append(etape)
        sortie.extend(apres.get(etape.get("id"), []) if isinstance(etape, dict) else [])
    if dernier is None:
        sortie.extend(avant_controle)
    return sortie


def deplier_avec_provenance(brut: Any, dossier: Path | None = None) -> tuple[Any, dict[str, Any]]:
    """La chaîne telle qu'elle s'exécute, et ce que chaque réconciliant y a apporté (voir `_deplier`)."""
    sortie, provenance, _emplacements, _etapes = _deplier(brut, dossier)
    return sortie, provenance


def chaine_executable(brut: Any, dossier: Path | None = None) -> tuple[Any, dict[str, Any]]:
    """La chaîne dépliée, plus ce que ses TECHNIQUES doivent savoir pour lire les emplacements comme elle :
    « _emplacements » (par rôle) et « _etapes_reconciliees » (l'étape → son réconciliant), que
    `core.chaine.lire` garde sur la chaîne et que `techniques_pour` emploie."""
    sortie, provenance, emplacements, etapes = _deplier(brut, dossier)
    if isinstance(sortie, dict) and emplacements:
        sortie = {**sortie, "_emplacements": emplacements, "_etapes_reconciliees": etapes}
    return sortie, provenance


def _deplier(brut: Any, dossier: Path | None = None) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, str]]:
    """La chaîne telle qu'elle s'exécute, et ce que chaque réconciliant y a apporté.

    Une chaîne sans « reconciliants » revient telle quelle — après avoir été vérifiée : elle ne lit
    aucune source en direct. La provenance dit, par rôle : la version du réconciliant, la techno, ses
    champs et ses étapes, les emplacements que la chaîne lit et ceux qu'elle ne prend pas.
    """
    if not isinstance(brut, dict):
        return brut, {}, {}, {}
    ctx = f"chaîne {str(brut.get('chaine') or '?')!r}"
    declares = brut.get("reconciliants")
    if declares is None:
        declares = {}
    if not isinstance(declares, dict):
        raise WorkflowMappingError(f"{ctx} : « reconciliants » est un objet {{rôle: {{branchement: valeur}}}}")
    registre = tous(dossier)
    for role in declares:
        if role not in registre:
            raise WorkflowMappingError(
                f"{ctx} : aucun réconciliant « {role} » (connus : {', '.join(sorted(registre)) or 'aucun'})")
    vus: list[str] = []
    for role in declares:
        for requis in registre[role].get("requiert") or []:
            if requis not in vus:
                raise WorkflowMappingError(
                    f"{ctx} : le réconciliant « {role} » lit ce que sert « {requis} » — le déclarer AVANT lui")
        vus.append(role)

    branches: dict[str, dict[str, Any]] = {}
    instances: dict[str, dict[str, Any]] = {}
    declines: dict[str, dict[str, str]] = {}
    for role, bruts in declares.items():
        rec = registre[role]
        declines[role] = _sans(ctx, role, rec, bruts if isinstance(bruts, dict) else {})
        branches[role] = _verifier_branchements(ctx, role, rec, bruts)
        cadre = f"{ctx}, réconciliant « {role} »"
        champs = _instancier(rec.get("champs") or {}, branches[role], cadre)
        etapes = []
        for declaree in rec.get("etapes") or []:
            item = _instancier(declaree, branches[role], cadre)
            if item is not _ABSENT and isinstance(item.get("etape"), dict):
                etapes.append((item["place"], item["etape"]))
        emplacements = _instancier(rec.get("emplacements") or {}, branches[role], cadre)
        instances[role] = {"champs": champs if champs is not _ABSENT else {},
                           "etapes": etapes,
                           "emplacements": emplacements if emplacements is not _ABSENT else {}}

    propres_champs = dict(brut.get("expose") or {})
    propres_etapes = list(brut.get("etapes") or [])
    _refuser_les_lectures_directes(ctx, registre, instances, propres_champs, propres_etapes, brut.get("livrable"))

    # Un réconciliant peut lire ce qu'un autre, déclaré avant lui, range (la culture d'une œuvre part
    # de son analyse) : ses propres valeurs se réécrivent avec les emplacements de ceux d'avant.
    lus: dict[str, set[str]] = defaultdict(set)
    connus: dict[str, dict[str, Any]] = {}
    for role, inst in instances.items():
        cadre = f"{ctx}, réconciliant « {role} »"
        inst["champs"] = _reecrire(inst["champs"], connus, lus, cadre)
        inst["etapes"] = [(p, _reecrire(e, connus, lus, cadre)) for p, e in inst["etapes"]]
        inst["emplacements"] = _reecrire(inst["emplacements"], connus, lus, cadre)
        connus[role] = inst["emplacements"]
    lus_par_la_chaine: dict[str, set[str]] = defaultdict(set)
    champs: dict[str, Any] = {}
    for role, inst in instances.items():
        for nom, champ in inst["champs"].items():
            if nom in champs:
                raise WorkflowMappingError(f"{ctx} : deux réconciliants apportent le champ {nom!r}")
            champs[nom] = champ
    for nom, champ in propres_champs.items():
        champs[nom] = _reecrire(champ, connus, lus_par_la_chaine, ctx)
    propres = [_reecrire(e, connus, lus_par_la_chaine, ctx) for e in propres_etapes]
    etapes = _assembler(ctx, propres, instances, branches)

    sortie: dict[str, Any] = {}
    for cle, valeur in brut.items():
        if cle in ("reconciliants", "_emplacements", "_etapes_reconciliees"):
            continue                     # ce qui se calcule ici ne s'écrit jamais à la main
        sortie[cle] = (champs if cle == "expose" else etapes if cle == "etapes"
                       else _reecrire(valeur, connus, lus_par_la_chaine, ctx) if cle == "livrable" else valeur)
    if instances and "expose" not in sortie:
        sortie["expose"] = champs
    provenance = {}
    for role, inst in instances.items():
        rec = registre[role]
        promesses = rec.get("promesses") or {}
        provenance[role] = {
            "version": rec["version"], "techno": rec["source"]["techno"],
            "champs": list(inst["champs"]), "etapes": [e["id"] for _p, e in inst["etapes"]],
            "emplacements_lus": sorted(lus_par_la_chaine[role]),
            "non_pris": sorted(set(inst["emplacements"]) - lus_par_la_chaine[role] - lus[role]),
            **({"prise": dict(rec["prise"])} if rec.get("prise") else {}),
            **({"promesses": {i: {"libelle": str(p["libelle"]), "emplacements": list(p["emplacements"])}
                              for i, p in promesses.items()},
                "declines": dict(declines[role])} if promesses else {}),
        }
    emplacements = {role: inst["emplacements"] for role, inst in instances.items()}
    etapes_reconciliees = {e["id"]: role for role, inst in instances.items() for _p, e in inst["etapes"]}
    return sortie, provenance, emplacements, etapes_reconciliees


def deplier(brut: Any, dossier: Path | None = None) -> Any:
    """La chaîne telle qu'elle s'exécute : ses réconciliants dépliés (voir `deplier_avec_provenance`)."""
    return deplier_avec_provenance(brut, dossier)[0]


# -- les techniques, et les promesses -----------------------------------------------------------

def emplacements_lus(valeur: Any, emplacements: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    """Les emplacements qu'une valeur (le fichier d'une technique) lit, par rôle."""
    lus: dict[str, set[str]] = defaultdict(set)
    for renvoi in _renvois(valeur):
        tete, _, chemin = renvoi.partition(".")
        places = emplacements.get(tete)
        if places is None or not chemin:
            continue
        morceaux = chemin.split(".")
        for i in range(len(morceaux), 0, -1):
            if ".".join(morceaux[:i]) in places:
                lus[tete].add(".".join(morceaux[:i]))
                break
    return lus


def reecrire_technique(donnees: dict[str, Any], emplacements: dict[str, dict[str, Any]],
                       etapes_reconciliees: dict[str, str], contexte: str) -> dict[str, Any]:
    """Le fichier d'une technique, ses rôles et ses contrôles lisant les emplacements de CETTE chaîne comme
    des renvois ordinaires. Une technique qui lit le récit d'une étape de réconciliant (« $contrainte.recit.x »)
    est refusée : une source ne se lit que par ses emplacements, chez la technique comme dans la chaîne."""
    for renvoi in _renvois({"roles": donnees.get("roles"), "controles": donnees.get("controles")}):
        tete = renvoi.split(".", 1)[0]
        if tete in etapes_reconciliees and tete not in emplacements:
            role = etapes_reconciliees[tete]
            raise WorkflowMappingError(
                f"{contexte} : « ${renvoi} » lit le récit de l'étape du réconciliant « {role} » — passer par ses "
                f"emplacements « ${role}.… »")
    lus: dict[str, set[str]] = defaultdict(set)
    sortie = dict(donnees)
    for cle in ("roles", "controles"):
        if cle in donnees:
            sortie[cle] = _reecrire(copy.deepcopy(donnees[cle]), emplacements, lus, contexte)
    return sortie


def juger_les_promesses(ctx: str, provenance: dict[str, Any], lus_ailleurs: dict[str, set[str]] | None = None) -> None:
    """Chaque promesse d'un réconciliant est TENUE (un de ses emplacements est lu, par la chaîne ou par sa
    technique) ou DÉCLINÉE avec sa raison — jamais les deux, jamais aucun : c'est ce qui fait qu'une chaîne
    ne ment pas sur ce qu'elle fait de la source, et ne propose rien qu'elle ne sache tenir."""
    fautes: list[str] = []
    for role, prov in provenance.items():
        lus = set(prov.get("emplacements_lus") or []) | set((lus_ailleurs or {}).get(role) or ())
        for ident, promesse in (prov.get("promesses") or {}).items():
            tenue = sorted(l for l in lus if any(l == e or l.startswith(f"{e}.") for e in promesse["emplacements"]))
            declinee = ident in (prov.get("declines") or {})
            if tenue and declinee:
                fautes.append(f"« {promesse['libelle']} » ({role}.{ident}) est déclinée (« {SANS} ») et pourtant tenue "
                              f"(elle lit {', '.join(tenue)}) — l'un des deux ment")
            elif not tenue and not declinee:
                fautes.append(f"« {promesse['libelle']} » ({role}.{ident}) n'est ni tenue (lire "
                              f"{' ou '.join(promesse['emplacements'])}) ni déclinée avec sa raison "
                              f"(« reconciliants »: {{« {role} »: {{« {SANS} »: {{« {ident} »: « … » }}}}}})")
    if fautes:
        raise WorkflowMappingError(f"{ctx} : " + " ; ".join(fautes))


# -- ce que la source sert et qu'aucun emplacement ne prend -------------------------------------

def faits_non_servis(reconciliant: dict[str, Any], faits: Any) -> list[dict[str, Any]]:
    """Les faits qu'une valeur du menu de la source porte (une charte : sa colonne « faits ») et que
    le réconciliant ne lit pas — chacun avec ce qu'il en compte. Un fait vide n'est pas un fait.

    C'est ainsi qu'une nouveauté de la source se voit AVANT d'être câblée : elle apparaît ici, dite,
    au lieu d'être perdue en silence ; la câbler est l'affaire du seul réconciliant."""
    declaration = (reconciliant.get("source") or {}).get("faits") or {}
    if not isinstance(faits, dict) or not declaration:
        return []
    lus = {str(f) for f in declaration.get("lus") or []}
    libelles = declaration.get("libelles") if isinstance(declaration.get("libelles"), dict) else {}
    dits: list[dict[str, Any]] = []
    for cle, valeur in faits.items():
        if cle in lus or valeur in (None, "", [], {}, False) or (isinstance(valeur, str) and not valeur.strip()):
            continue
        entree: dict[str, Any] = {"fait": cle, "libelle": str(libelles.get(cle) or cle)}
        if isinstance(valeur, list):
            entree["nombre"] = len(valeur)
            noms = [str(v.get("nom")) for v in valeur if isinstance(v, dict) and v.get("nom")]
            if noms:
                entree["noms"] = noms
        dits.append(entree)
    return dits

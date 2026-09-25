"""Les GABARITS de chaîne : le patron qu'un cas de création déclare suivre, et qu'on vérifie.

Antoine, 2026-09-24 : « standardise par un template un cas de création, en
manifestant le template à suivre ». Un gabarit est un fichier
(`resources/gabarits/<nom>.json`) qui dit ce qu'une chaîne de ce genre DOIT
porter : ses étapes dans l'ordre (avec leur genre, le rôle d'un rendu, le champ
sous lequel une étape n'a lieu que parfois), les genres d'étapes qu'on peut
intercaler, son premier champ, un champ de technique, les champs qu'un autre
impose, les constats et contrôles requis, son livrable. Une chaîne le MANIFESTE
(`"gabarit": "creation"`) ; à la lecture du catalogue, elle est vérifiée
contre lui et refusée si elle s'en écarte — le gabarit est une garde, pas une
recommandation. Une chaîne qui n'en déclare aucun n'est pas jugée ici — mais le
catalogue ne publie aucune chaîne sans gabarit au SOCLE (voir `adapter/catalog.py`).

Un gabarit peut en ÉTENDRE un autre (« etend ») : il en hérite tout ce qu'il ne
redit pas. Le SOCLE (`socle.json`) est la base de toute création cataloguée —
Antoine, 2026-09-25 : « chaque nouveau flux de création sur maestro suit la même
structure à la base » — : un contrôle final du fichier livré, des champs qui
disent leur catégorie et leur aide, un livrable déclaré ; les sources, elles, ne
se lisent que par leur réconciliant (`core/reconciliant.py`). « creation »
l'étend pour le cas d'une image créée par un modèle.

Ce module ne connaît aucun flux : il lit ce que le gabarit déclare et ce que la
chaîne porte, et compare.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chaine import Chaine, Etape
from .errors import WorkflowMappingError

DOSSIER = Path(__file__).resolve().parent.parent / "adapter" / "resources" / "gabarits"
# Le gabarit de base de toute création cataloguée : tout autre l'étend.
SOCLE = "socle"


def lire(nom: str, dossier: Path | None = None, _vus: tuple[str, ...] = ()) -> dict[str, Any]:
    """Le gabarit `nom`, tel que son fichier le déclare et ce qu'il hérite de celui qu'il étend —
    refusé s'il manque ou ne tient pas. `lignee` dit la suite des gabarits, du plus général au sien."""
    if nom in _vus:
        raise WorkflowMappingError(f"gabarit {nom!r} : il s'étend lui-même ({' → '.join(_vus + (nom,))})")
    chemin = (Path(dossier) if dossier else DOSSIER) / f"{nom}.json"
    if not chemin.is_file():
        raise WorkflowMappingError(
            f"gabarit {nom!r} : aucun fichier {chemin.name} dans {chemin.parent} — une chaîne ne "
            f"peut manifester qu'un gabarit qui existe")
    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"gabarit {nom!r} : impossible de lire {chemin} : {exc}") from exc
    if not isinstance(brut, dict) or str(brut.get("gabarit") or "") != nom:
        raise WorkflowMappingError(f"gabarit {nom!r} : le fichier doit se nommer lui-même (clé « gabarit »)")
    parent: dict[str, Any] = {}
    if brut.get("etend"):
        parent = lire(str(brut["etend"]), dossier, _vus + (nom,))
    lu = {**{k: v for k, v in parent.items() if k not in ("gabarit", "resume", "_lire_moi")}, **brut}
    lu["lignee"] = list(parent.get("lignee") or []) + [nom]
    etapes = lu.get("etapes")
    libres = lu.get("etapes_libres")
    if not isinstance(etapes, list) or (not etapes and libres != "toutes"):
        raise WorkflowMappingError(f"gabarit {nom!r} : « etapes » doit être une liste non vide (vide : seulement "
                                   f"quand toutes les étapes sont libres, « etapes_libres »: « toutes »)")
    for e in etapes:
        if not isinstance(e, dict) or not e.get("id") or not e.get("genre"):
            raise WorkflowMappingError(f"gabarit {nom!r} : chaque étape porte un « id » et un « genre » ({e!r})")
    return lu


def _quand_sous(etape: Etape) -> str | None:
    """Le champ sous lequel une étape n'a lieu que parfois (« quand »), ou rien."""
    q = etape.quand
    if isinstance(q, dict):
        v = str(q.get("valeur") or "")
    else:
        v = str(q or "")
    return v[1:].split(".", 1)[0] if v.startswith("$") else None


def ecarts(chaine: Chaine, gabarit: dict[str, Any]) -> list[str]:
    """Tout ce en quoi la chaîne s'écarte du gabarit — vide quand elle le suit."""
    nom = str(gabarit.get("gabarit"))
    fautes: list[str] = []
    ids = [e.id for e in chaine.etapes]
    par_id = {e.id: e for e in chaine.etapes}

    # 1. Les étapes requises, dans l'ordre, avec leur genre, leur rôle, leur « sous ».
    requises = [dict(e) for e in gabarit["etapes"]]
    rang = -1
    for attendue in requises:
        ident = str(attendue["id"])
        if ident not in par_id:
            fautes.append(f"l'étape {ident!r} ({attendue['genre']}) manque")
            continue
        position = ids.index(ident)
        if position <= rang:
            fautes.append(f"l'étape {ident!r} vient avant {ids[rang]!r} : l'ordre du gabarit est "
                          f"{' → '.join(str(e['id']) for e in requises)}")
        rang = max(rang, position)
        etape = par_id[ident]
        if etape.genre != attendue["genre"]:
            fautes.append(f"l'étape {ident!r} est un « {etape.genre} », le gabarit attend un « {attendue['genre']} »")
        if attendue.get("role") and etape.role != attendue["role"]:
            fautes.append(f"l'étape {ident!r} doit tenir le rôle {attendue['role']!r} par une technique "
                          f"(« role » + « technique »), pas un graphe nommé")
        if attendue.get("sous"):
            if _quand_sous(etape) != attendue["sous"]:
                fautes.append(f"l'étape {ident!r} n'a lieu que sous le champ {attendue['sous']!r} : "
                              f"il lui faut « quand » sur « ${attendue['sous']} »")
    # 2. Les étapes en plus : seulement des genres que le gabarit laisse libres (« toutes » : le socle).
    toutes = gabarit.get("etapes_libres") == "toutes"
    libres = set() if toutes else set(str(g) for g in gabarit.get("etapes_libres") or [])
    attendus = {str(e["id"]) for e in requises}
    for etape in chaine.etapes:
        if not toutes and etape.id not in attendus and etape.genre not in libres:
            fautes.append(f"l'étape {etape.id!r} ({etape.genre}) n'est pas au gabarit, qui ne laisse libres que "
                          f"{', '.join(sorted(libres)) or 'aucun genre'}")
    # 3. Le premier champ, le champ de technique.
    premier = gabarit.get("premier_champ")
    if premier:
        noms = list(chaine.champs)
        if not noms or noms[0] != premier:
            fautes.append(f"le premier champ exposé doit être {premier!r} (trouvé : {noms[0] if noms else 'aucun'})")
        elif gabarit.get("premier_champ_rubrique") and chaine.champs[premier].categorie != gabarit["premier_champ_rubrique"]:
            fautes.append(f"le champ {premier!r} doit être rangé sous la rubrique {gabarit['premier_champ_rubrique']!r}")
    if gabarit.get("champ_de_technique") and chaine.champ_de_technique is None:
        fautes.append("un champ qui choisit la technique (« options_depuis »: {\"techniques\": true}) manque")
    # 4. Ce que le premier champ impose : au moins ces champs, avec la même déclaration.
    imposes = gabarit.get("imposes_par") or {}
    if imposes:
        attendu = {"champ": str(imposes.get("champ")), "sauf": [str(v) for v in imposes.get("sauf") or []]}
        for nom_champ in imposes.get("au_moins") or []:
            champ = chaine.champs.get(str(nom_champ))
            if champ is None:
                fautes.append(f"le champ {nom_champ!r}, que {attendu['champ']!r} impose, manque")
            elif champ.impose_par != attendu:
                fautes.append(f"le champ {nom_champ!r} doit déclarer impose_par {json.dumps(attendu, ensure_ascii=False)} "
                              f"(trouvé : {json.dumps(champ.impose_par, ensure_ascii=False)})")
    # 5. Les constats et contrôles requis, par étape.
    for cle, genre in (("constats_requis", "constater"), ("controles_requis", "verifier")):
        for ident, liste in (gabarit.get(cle) or {}).items():
            etape = par_id.get(str(ident))
            if etape is None or etape.genre != genre:
                continue                       # déjà dit au point 1
            presents = {str(c.get("id")) for c in etape.controles_propres if isinstance(c, dict)}
            manquants = [c for c in liste if str(c) not in presents]
            if manquants:
                fautes.append(f"l'étape {ident!r} doit porter {', '.join(manquants)}")
    # 6. Le livrable.
    if gabarit.get("livrable") and chaine.livrable != gabarit["livrable"]:
        fautes.append(f"le livrable doit être {gabarit['livrable']!r} (trouvé : {chaine.livrable!r})")
    if gabarit.get("livrable_declare") and not chaine.livrable:
        fautes.append("la chaîne ne déclare pas son livrable (« livrable »: « $<étape>.livrable »)")
    # 7. Le contrôle final : la dernière étape vérifie le fichier livré, avec ces contrôles au moins.
    final = gabarit.get("controle_final") or {}
    if final:
        derniere = chaine.etapes[-1] if chaine.etapes else None
        if derniere is None or derniere.id != final.get("id") or derniere.genre != final.get("genre"):
            fautes.append(f"la dernière étape doit être {final.get('id')!r} (« {final.get('genre')} ») — trouvé : "
                          f"{derniere.id if derniere else 'aucune'!r}")
        else:
            presents = {str(c.get("id")) for c in derniere.controles_propres if isinstance(c, dict)}
            manquants = [c for c in final.get("controles") or [] if str(c) not in presents]
            if manquants:
                fautes.append(f"le contrôle final {derniere.id!r} doit porter {', '.join(manquants)}")
    # 8. Les réconciliants que le gabarit exige — la PRISE DE CHARTE pour toute création (Antoine, 2026-09-25 :
    #    « j'ai demandé un template, la prise de charte doit y être présente, et à son poids exactement comme
    #    c'est défini par le template ») ; ce que la chaîne n'en tient pas, elle le décline avec sa raison.
    for requis in gabarit.get("reconciliants_requis") or []:
        if str(requis) not in chaine.emplacements:
            fautes.append(f"le réconciliant « {requis} » manque : « reconciliants »: {{« {requis} »: …}}, puis tenir ou "
                          f"décliner (« sans ») chacune de ses promesses")
    # 9. Chaque champ dit sa catégorie et son aide : un lanceur range et explique sans rien connaître.
    if gabarit.get("champs_documentes"):
        muets = [f"{c.nom} ({', '.join(q for q, v in (('categorie', c.categorie), ('aide', c.aide)) if not str(v or '').strip())})"
                 for c in chaine.champs.values() if not str(c.categorie or "").strip() or not str(c.aide or "").strip()]
        if muets:
            fautes.append(f"des champs ne disent pas leur catégorie ou leur aide : {', '.join(muets)}")
    return [f"gabarit {nom!r} : {f}" for f in fautes]


def suit_le_socle(gabarit: dict[str, Any]) -> bool:
    """Le gabarit est-il le socle, ou l'étend-il ?"""
    return SOCLE in (gabarit.get("lignee") or [gabarit.get("gabarit")])


def verifier(chaine: Chaine, gabarit: dict[str, Any]) -> None:
    """Refuser une chaîne qui manifeste un gabarit sans le suivre — en disant tout ce qui s'en écarte."""
    fautes = ecarts(chaine, gabarit)
    if fautes:
        raise WorkflowMappingError(
            f"chaîne {chaine.nom!r} : elle manifeste le gabarit {gabarit.get('gabarit')!r} et s'en écarte — "
            + " ; ".join(fautes))

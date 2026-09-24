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
recommandation. Une chaîne qui n'en déclare aucun n'est pas jugée.

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


def lire(nom: str, dossier: Path | None = None) -> dict[str, Any]:
    """Le gabarit `nom`, tel que son fichier le déclare — refusé s'il manque ou ne tient pas."""
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
    etapes = brut.get("etapes")
    if not isinstance(etapes, list) or not etapes:
        raise WorkflowMappingError(f"gabarit {nom!r} : « etapes » doit être une liste non vide")
    for e in etapes:
        if not isinstance(e, dict) or not e.get("id") or not e.get("genre"):
            raise WorkflowMappingError(f"gabarit {nom!r} : chaque étape porte un « id » et un « genre » ({e!r})")
    return brut


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
    # 2. Les étapes en plus : seulement des genres que le gabarit laisse libres.
    libres = set(str(g) for g in gabarit.get("etapes_libres") or [])
    attendus = {str(e["id"]) for e in requises}
    for etape in chaine.etapes:
        if etape.id not in attendus and etape.genre not in libres:
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
    return [f"gabarit {nom!r} : {f}" for f in fautes]


def verifier(chaine: Chaine, gabarit: dict[str, Any]) -> None:
    """Refuser une chaîne qui manifeste un gabarit sans le suivre — en disant tout ce qui s'en écarte."""
    fautes = ecarts(chaine, gabarit)
    if fautes:
        raise WorkflowMappingError(
            f"chaîne {chaine.nom!r} : elle manifeste le gabarit {gabarit.get('gabarit')!r} et s'en écarte — "
            + " ; ".join(fautes))

"""FastAPI application factory and routes.

The API is deliberately thin: translate wire → domain, call the orchestrator,
translate domain → wire. All decisions live in the core; all errors leave as
RFC 7807. Interactive docs are served at ``/docs`` and ``/redoc``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (BackgroundTasks, FastAPI, File, Form, Request, Response,
                     UploadFile)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..adapter import raccourcis
from ..config import Settings
from ..container import build_container
from ..core.errors import (DependencyUnavailableError, InputValueRefusedError,
                           MediaAssemblyError, RaccourciNotFoundError,
                           UnknownWorkflowInputError)
from ..core.jobs import JobStatus
from ..core.intention import RenderIntent, intent_fields, is_media_param
from ..core.orchestrator import Orchestrator, derivable_params
from .problems import install_problem_handlers
from .schemas import (ArtifactOut, IntentIn, JobOut, RaccourciIn, RejeuIn,
                      WorkflowImportIn)

_DESCRIPTION = """
Decoupled orchestration for ComfyUI.

Submit a declarative **render intent** (prompt, constraints, duration, format,
fps); the service resolves it, reconciles it against **known problems** already
met on this host (**Hermes**, local mode), drives ComfyUI, and delivers the
produced media — or the error, which is recorded so it is not met again. The
business logic never names a ComfyUI node — the reconciliation file does that.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Respect a container injected by create_app (tests / custom settings);
    # only build from the environment when none was provided.
    if getattr(app.state, "container", None) is None:
        app.state.container = build_container(Settings.from_env())
    # A run the engine finished while this service was down still has to be
    # delivered: ask ComfyUI what became of the runs left in flight.
    app.state.recovered = _recover_inflight(app.state.container)
    yield


def _recover_inflight(container) -> list[dict]:
    if getattr(container, "inflight", None) is None:
        return []
    from ..adapter.inflight import recover
    try:
        return recover(container.backend, container.inflight,
                       container.registry, container.settings.host_id)
    except Exception:
        return []          # never keep the service from starting


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.container.orchestrator


_QUEUE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def _queue_snapshot(container) -> dict | None:
    """Queue state for the hot path: short timeout, briefly cached. Reading a
    job must never hang on a busy engine (it would freeze the UI)."""
    import time as _t
    from ..adapter.comfyui_client import ComfyUIClient
    now = _t.monotonic()
    if _QUEUE_CACHE["value"] is not None and now - _QUEUE_CACHE["at"] < 2.0:
        return _QUEUE_CACHE["value"]
    try:
        quick = ComfyUIClient(container.settings.comfyui_base_url, request_timeout_s=1.5)
        value = quick.queue()
    except Exception:
        value = None
    _QUEUE_CACHE["at"], _QUEUE_CACHE["value"] = now, value
    return value


def _with_engine_state(container, out: dict) -> dict:
    """Say whether the engine is RUNNING this job or it is still QUEUED behind
    others. Without it a queued job was displayed as "loading the model", which
    is a different thing entirely (observed: 9 min of false 'loading')."""
    ref = out.get("engine_ref")
    # "queued" is precisely the state that needs it: without it the UI fell back
    # to "loading the model" while the job was in fact waiting behind others.
    if not ref or out.get("status") not in ("accepted", "queued", "running"):
        return out
    q = _queue_snapshot(container)
    if q is None:
        return out
    if ref in q.get("running", []):
        out["engine_state"] = {"state": "running"}
    elif ref in q.get("pending", []):
        pending = q.get("pending", [])
        out["engine_state"] = {"state": "pending", "ahead": pending.index(ref) + len(q.get("running", []))}
    else:
        out["engine_state"] = {"state": "unknown"}
    return out


def _with_work(container, plan):
    """Attach the effective size of the job, read from the injected graph."""
    from ..adapter.work import WORK_MODEL, effective_values, work_units
    try:
        values = effective_values(container.catalog, plan)
        plan.work = work_units(values)
        plan.work_model = WORK_MODEL
        return values
    except Exception:
        return {}


def _analyse_workflow(container, spec) -> dict:
    """What this workflow really accepts and delivers, read from ComfyUI.

    Run at ingestion and on every update: the wiring of the UI and of the API
    is derived from it, never from a generic assumption.
    """
    from ..adapter import media_inputs
    from ..adapter.workflow_io import describe_io
    # `monter` : un montage n'est un graphe qu'une fois déplié, et ses liaisons
    # (« $amorce.1 ») ne désignent un nœud qu'après — les lire telles quelles
    # annoncerait des pièces jointes sans nœud.
    graph, liaisons = container.catalog.monter(spec)
    # Les pièces jointes se lisent dans le GRAPHE : les annoncer ne demande pas
    # que le moteur réponde, et une ingestion moteur éteint doit dire pareil.
    pieces = media_inputs.describe(liaisons, spec.titles, spec.carried, graph)
    probe = container.comfyui.probe()
    if not probe.get("available"):
        return {"described": False, "reason": probe.get("reason", "moteur injoignable"),
                "accepts": sorted(spec.profile.accepts), "media_inputs": pieces}
    io = describe_io(graph, container.comfyui.get_object_info(), spec.titles)
    return {
        "described": True,
        "accepts": sorted(spec.profile.accepts),          # semantic inputs we can drive
        "media_inputs": pieces,                    # les pièces jointes, une par entrée
        "intent_fields": sorted(set(intent_fields(spec.profile.accepts))
                                | set(derivable_params(spec.kind, spec.profile.accepts,
                                                       spec.defaults))),
        # drivable via conversion — la cadence peut n'être qu'un défaut déclaré
        "derived": derivable_params(spec.kind, spec.profile.accepts, spec.defaults),
        "settable_inputs": len(io["inputs"]),      # everything the workflow exposes
        "outputs": io["outputs"],                  # what it delivers
        "carried": spec.carried,                   # media it already holds
        "kind": spec.kind,
    }


def _delivery(container, spec) -> list[dict]:
    """Ce que ce workflow délivre : les nœuds de sortie déclarés par ComfyUI.

    Le contrat d'un appel ne s'arrête pas aux entrées — un appelant doit savoir
    ce qui va sortir. Sans cela, un workflow passé de SaveAudio à SaveAudioMP3
    changeait le format livré sans que rien ne l'annonce.
    """
    try:
        from ..adapter.workflow_io import describe_io
        graph = container.catalog.load_template(spec)
        io = describe_io(graph, container.comfyui.get_object_info(), spec.titles)
        return io["outputs"]
    except Exception:
        return []


def _outputs_signature(outputs: list[dict]) -> list[str]:
    """Une sortie se reconnaît à son nœud ET à sa classe : changer de nœud de
    sauvegarde change le fichier livré."""
    return sorted(f"{o.get('node')}.{o.get('class_type')}" for o in outputs or [])


def _rewiring(avant: dict | None, spec, apres_out: list[dict] | None = None) -> dict:
    """Ce que la ré-analyse a re-mesuré, entrées ET sortie.

    Une ré-extraction qui répond « c'est fait » n'apprend rien : ce qui compte
    est de savoir quelles entrées sont apparues, lesquelles ont disparu,
    lesquelles pilotent désormais un autre nœud — et ce que le workflow délivre
    maintenant, car un changement de nœud de sauvegarde change le format livré.
    """
    from ..core.intention import intent_field_of
    maintenant = {k: (b.node, b.input) for k, b in spec.bindings.items()}
    if avant is None:
        return {"first_analysis": True,
                "added": sorted(intent_field_of(k) for k in maintenant),
                "delivery_changed": False,
                "delivers": [o.get("display_name") or o.get("class_type") for o in (apres_out or [])]}
    anciens = avant["bindings"]
    apparus = sorted(intent_field_of(k) for k in maintenant if k not in anciens)
    disparus = sorted(intent_field_of(k) for k in anciens if k not in maintenant)
    deplaces = [
        {"field": intent_field_of(k), "from": f"{anciens[k][0]}.{anciens[k][1]}",
         "to": f"{maintenant[k][0]}.{maintenant[k][1]}"}
        for k in sorted(set(anciens) & set(maintenant)) if anciens[k] != maintenant[k]
    ]
    sortie_avant = _outputs_signature(avant.get("outputs"))
    sortie_apres = _outputs_signature(apres_out)
    delivery = False
    if sortie_avant != sortie_apres:
        delivery = {"from": sortie_avant, "to": sortie_apres}
    return {"first_analysis": False, "added": apparus, "removed": disparus,
            "moved": deplaces,
            "kind_changed": (avant["kind"] != spec.kind) and {"from": avant["kind"],
                                                              "to": spec.kind} or False,
            "delivery_changed": delivery,
            "delivers": [o.get("display_name") or o.get("class_type") for o in (apres_out or [])]}


def _freshness(container, spec) -> dict:
    """Ce que le catalogue dit de la fraîcheur d'un extrait."""
    from ..adapter.freshness import source_state
    state = source_state(container.comfyui, spec)
    if state["fresh"]:
        return {"source_changed": False}
    out = {"source_changed": True, "source_change_reason": state["reason"]}
    if state.get("missing"):
        out["source_missing"] = True
    return out


def _est_declare(container, name: str) -> bool:
    """Ce nom vient-il du fichier de réconciliation (et non d'une ingestion) ?

    Un spec déclaré n'a pas de ``source`` et son graphe vit hors du dossier des
    workflows enregistrés.
    """
    try:
        spec = container.catalog.get_spec(name)
    except Exception:
        return False
    dossier = container.settings.workflows_dir.resolve()
    try:
        spec.workflow_path.resolve().relative_to(dossier)
        return False                     # il vit dans le dossier : c'est un enregistré
    except ValueError:
        return True


async def _ingest(container, name: str, graph: dict, source: str | None,
                  source_hash: str | None, titles: dict) -> dict:
    """Ingérer un graphe et rendre compte de ce que cela change.

    Deux portes mènent ici — un graphe posté, ou un graphe extrait du moteur —
    et elles doivent produire le MÊME résultat. Écrites deux fois, elles avaient
    divergé au point que l'une référençait des variables qui n'existaient que
    dans l'autre.
    """
    from ..adapter.freshness import forget

    # La photo d'AVANT, prise avant de remplacer : sans elle, une ré-ingestion
    # répond « c'est fait » sans dire ce qu'elle a re-câblé.
    from ..adapter.comfyui_client import source_hash as _empreinte

    # Un nom déjà DÉCLARÉ dans le fichier de réconciliation reprendrait la main
    # au prochain démarrage : l'ingestion semblerait réussir, puis serait
    # annulée sans un mot. Autant le refuser tout de suite.
    if name in getattr(container.catalog, "shadowed", ()) or _est_declare(container, name):
        raise UnknownWorkflowInputError(
            f"{name!r} est déclaré dans le fichier de réconciliation : une entrée déclarée "
            f"reprend la main au redémarrage et annulerait cet enregistrement. Choisis un "
            f"autre nom, ou retire l'entrée déclarée.", workflow=name)

    avant, empreinte_avant = None, None
    try:
        ancien = container.catalog.get_spec(name)
        empreinte_avant = _empreinte(container.catalog.load_template(ancien))
        avant = {"kind": ancien.kind,
                 "bindings": {k: (b.node, b.input) for k, b in ancien.bindings.items()},
                 "outputs": await run_in_threadpool(_delivery, container, ancien)}
    except Exception:
        pass

    spec = container.catalog.register(name, graph, source=source,
                                      source_hash=source_hash, titles=titles)
    analysis = await run_in_threadpool(_analyse_workflow, container, spec)
    forget(name)                      # la question « est-ce à jour ? » a changé de réponse

    # Un souvenir de problème parle d'UNE définition. Quand le graphe change,
    # il ne dit plus rien de ce qui vient d'être enregistré : le garder laissait
    # un workflow réparé refusé pour une cause disparue. Un ré-enregistrement à
    # l'identique, lui, ne révise rien.
    revision = {"revised": 0, "at": None, "cause": None}
    if empreinte_avant is not None and empreinte_avant != _empreinte(graph):
        revision = container.registry.revise(
            container.settings.host_id, name,
            "workflow réenregistré avec un graphe différent")

    apres = await run_in_threadpool(_delivery, container, spec)
    return {**_spec_dict(spec), "analysis": analysis, "delivers": apres,
            "changes": _rewiring(avant, spec, apres),
            # Ce que l'ingestion a changé dans la mémoire, avec le MOMENT : une
            # levée sans date ne se retrouve plus ensuite.
            "revised_problems": revision}


# -- chaînes et vitrine --------------------------------------------------------
#
# Ce qui suit sert deux besoins que le catalogue ne couvrait pas : une entrée
# qui est une CHAÎNE (pas de graphe, des étapes), et la PRÉSENTATION (titres,
# catégories, libellés des menus) sans laquelle un lanceur devait tenir sa
# propre liste — laquelle vieillit dès que le catalogue bouge.


def _menu_declare(c, champ: str | None) -> dict:
    return dict((getattr(c.catalog, "menus", None) or {}).get(champ or "") or {})


def _habiller(c, entree: dict, menu: dict | None = None,
              aides: dict | None = None) -> dict:
    """Ajouter à un champ ce qu'il faut pour l'AFFICHER : choix, libellé, unité, aide.

    La liste des valeurs reste celle du fournisseur (le moteur, ou le catalogue) ;
    seuls les libellés sont déclarés ici. Recopier la liste dans un client la
    figeait le jour où le fournisseur en ajoutait une.
    """
    from ..adapter import menus as _menus
    declare = _menu_declare(c, entree.get("field")) if menu is None else menu
    choix, manque = _menus.choix(entree.get("options"), declare)
    if choix is not None:
        entree["choix"] = choix
    if manque:
        entree["manque"] = manque
    # Le libellé le plus PROCHE l'emporte : celui que la chaîne écrit pour SON
    # champ (« Durée de la révélation »), puis le menu déclaré pour ce nom de
    # champ (« Durée »), puis le titre que l'auteur a donné au nœud. Mesuré dans
    # l'autre ordre : le menu générique effaçait le libellé propre à la chaîne.
    libelle = entree.get("libelle") or declare.get("libelle") or entree.get("label")
    if libelle:
        entree["libelle"] = libelle
    unite = _menus.unite(str(entree.get("field") or ""), entree.get("unite")
                         or declare.get("unite"))
    if unite:
        entree["unite"] = unite
    # L'aide de l'ENTRÉE l'emporte sur celle du menu global : « le sujet s'écrit
    # décor | temps un | temps deux » ne vaut que pour les montages qui
    # répartissent les parts, pas pour tout champ nommé « prompt ».
    aide = (aides or {}).get(str(entree.get("field") or "")) or entree.get("aide") \
        or declare.get("aide")
    if aide:
        entree["aide"] = aide
    return entree


def _habiller_medias(c, pieces: list[dict], aides: dict | None = None) -> list[dict]:
    """Une pièce jointe, telle qu'un formulaire l'affiche : libellé et aide.

    Le nom que l'auteur a donné au nœud manque souvent — un « LoadImage » posé
    sans titre —, et le formulaire montrait alors la clé nue, « image ». Le
    libellé déclaré au menu de ce nom de champ prend le relais : déclaré une
    fois pour tous les workflows, plutôt que retitré nœud par nœud dans ComfyUI.
    """
    for piece in pieces:
        champ = str(piece.get("param") or "")
        declare = _menu_declare(c, champ)
        if not piece.get("label") and declare.get("libelle"):
            piece["label"] = declare["libelle"]
        aide = (aides or {}).get(champ) or declare.get("aide")
        if aide:
            piece["aide"] = aide
    return pieces


def _options_du_catalogue(c, filtre: Any) -> tuple[list[str], dict[str, Any]]:
    """Les entrées PUBLIÉES du catalogue qui répondent au filtre, et leurs titres.

    Une chaîne dont un champ choisit « le mode de révélation » ne doit pas
    porter la liste des modes : elle vieillirait à chaque ajout. Elle dit d'où
    la liste vient, et la passerelle la remplit depuis ses propres entrées.
    """
    reglage = filtre if isinstance(filtre, dict) else {}
    prefixe = str(reglage.get("prefixe") or "")
    categorie = reglage.get("categorie")
    exclure = set(reglage.get("exclure") or ())
    trouves: list[tuple[int, str, str]] = []
    libelles: dict[str, Any] = {}
    for nom in c.catalog.names():
        spec = c.catalog.get_spec(nom)
        if spec.categorie is None or nom in exclure:
            continue                      # non publiée : technique, pas un choix d'utilisateur
        if prefixe and not nom.startswith(prefixe):
            continue
        if categorie and spec.categorie != categorie:
            continue
        trouves.append((spec.ordre, nom, nom))
        rubrique = (c.catalog.categories or {}).get(spec.categorie) or {}
        libelles[nom] = {"libelle": spec.titre or nom, "resume": spec.description,
                         "groupe": rubrique.get("titre")}
    return [nom for _, _, nom in sorted(trouves)], libelles


def _options_declarees(c, depuis: Any) -> tuple[list[str], dict[str, Any]]:
    """La liste d'un champ qui dit d'OÙ elle vient, et de quoi l'habiller.

    Deux sources, une seule règle : la liste appartient au fournisseur. Le
    CATALOGUE pour un champ qui choisit un flux publié ; un MENU déclaré pour
    un champ qui choisit dans un vocabulaire qu'un paquet tient (les structures
    de récit). Recopier l'une ou l'autre dans la chaîne la figerait.
    """
    if isinstance(depuis, dict) and depuis.get("techniques"):
        # LES TECHNIQUES DÉCLARÉES. Une de plus est un fichier de plus : ni la
        # chaîne ni le code ne les listent, sans quoi la chaîne porterait la
        # dépendance dont on vient de la débarrasser.
        from ..adapter import techniques as _techniques
        vues = _techniques.vues(c.catalog.techniques())
        return ([v["valeur"] for v in vues],
                {"libelles": {v["valeur"]: {"libelle": v["libelle"], "resume": v["resume"]}
                              for v in vues}})
    if isinstance(depuis, dict) and depuis.get("menu"):
        from ..adapter import menus as _menus
        declare = _menu_declare(c, str(depuis["menu"]))
        return _menus.valeurs(declare)[0], declare
    filtre = depuis.get("catalogue", depuis) if isinstance(depuis, dict) else depuis
    options, libelles = _options_du_catalogue(c, filtre)
    return options, {"libelles": libelles}


def _options_exposees(c, chaine, technique=None) -> dict[str, tuple]:
    """Les valeurs permises de chaque champ COMBO — celles de la technique
    CHOISIE pour un champ que plusieurs techniques exposent (« fond »)."""
    from ..core import chaine as _noyau
    sorties: dict[str, tuple] = {}
    for nom, champ in _noyau.champs_retenus(chaine, technique).items():
        if champ.options_depuis is not None:
            sorties[nom] = tuple(_options_declarees(c, champ.options_depuis)[0])
        elif champ.options is not None:
            sorties[nom] = tuple(champ.options)
    return sorties


def _entree_de_champ(c, nom: str, champ, aides: dict | None = None) -> dict:
    """UN champ exposé, tel qu'un formulaire le rend."""
    menu = _menu_declare(c, nom)
    options = champ.options
    if champ.options_depuis is not None:
        options, source = _options_declarees(c, champ.options_depuis)
        # Le menu déclaré AU NOM DU CHAMP reste le plus proche : il peut
        # retitrer ce que la source rend, jamais l'inverse.
        menu = {**source, **menu}
    entree: dict[str, Any] = {
        "field": nom, "param": nom, "node": None, "input": None,
        "type": champ.type, "value": champ.defaut, "derived": False,
        "requis": champ.requis, "libelle": champ.libelle,
    }
    for cle, valeur in (("min", champ.minimum), ("max", champ.maximum),
                        ("step", champ.pas), ("unite", champ.unite)):
        if valeur is not None:
            entree[cle] = valeur
    if options is not None:
        entree["options"] = list(options)
    return _habiller(c, entree, menu, aides)


def _intent_inputs_chaine(c, chaine, aides: dict | None = None) -> list[dict]:
    """Le formulaire d'une chaîne : ses champs COMMUNS, puis ceux des techniques.

    Décrit sans le moteur : une chaîne n'a pas de graphe à interroger, et son
    contrat ne doit pas dépendre de ce que ComfyUI répondait ce jour-là.

    Un champ qu'une technique apporte porte ``selon`` : le lanceur ne le montre
    que si la technique courante est dans la liste. Deux techniques peuvent
    exposer le MÊME nom avec des options différentes (« fond ») : le champ porte
    alors les options et le défaut de CHACUNE (``selon_options``,
    ``selon_defauts``), et le lanceur en fait une liste qui suit la technique
    choisie. Rendre les seules options de la technique par défaut aurait figé la
    liste sur elle.
    """
    techniques = c.catalog.techniques() if chaine.champ_de_technique else {}
    entrees = [_entree_de_champ(c, nom, champ, aides)
               for nom, champ in chaine.champs.items() if champ.media is None]
    # Les champs des techniques APRÈS les communs, dans l'ordre des techniques
    # puis de leur déclaration : un formulaire les affiche à la suite du champ
    # qui les commande.
    porteuses: dict[str, list[str]] = {}
    for nom_technique, technique in sorted(techniques.items()):
        for nom in technique.champs:
            porteuses.setdefault(nom, []).append(nom_technique)
    defaut = _technique_par_defaut(c, chaine)
    for nom, qui in porteuses.items():
        if nom in chaine.champs:
            continue                       # un commun ne devient pas conditionnel
        # Le champ s'affiche tel que la technique PAR DÉFAUT le déclare : c'est
        # elle que le formulaire ouvre. Pris chez la première venue (l'ordre
        # alphabétique), « fond » s'ouvrait sur la valeur d'une technique sous
        # la liste d'une autre.
        porteuse = techniques[defaut.nom] if defaut is not None and nom in defaut.champs \
            else techniques[qui[0]]
        entree = _entree_de_champ(c, nom, porteuse.champs[nom], aides)
        entree["selon"] = {"champ": chaine.champ_de_technique, "valeurs": qui}
        if len(qui) > 1:
            entree["selon_options"] = {}
            entree["selon_defauts"] = {}
            for nom_technique in qui:
                champ = techniques[nom_technique].champs[nom]
                habille = _entree_de_champ(c, nom, champ, aides)
                entree["selon_options"][nom_technique] = habille.get("choix") \
                    or habille.get("options") or []
                entree["selon_defauts"][nom_technique] = champ.defaut
        entrees.append(entree)
    return entrees


def _media_inputs_chaine(chaine) -> list[dict]:
    from ..adapter.media_inputs import ACCEPT
    return [{"param": nom, "category": champ.media, "label": champ.libelle,
             "accept": ACCEPT.get(champ.media, "*/*"), "neutral": False,
             "carried": None, "node": None, "input": None, "class_type": None,
             "requis": champ.requis}
            for nom, champ in chaine.champs.items() if champ.media is not None]


def _technique_par_defaut(c, chaine):
    """La technique qu'un formulaire ouvre : celle que le champ déclare par défaut."""
    from ..core import chaine as _noyau
    return _noyau.technique_choisie(chaine, {}, c.catalog.techniques())


def _techniques_vues(c, chaine) -> list[dict]:
    """Les techniques entre lesquelles CETTE chaîne choisit — vide si elle n'en
    emploie aucune (elle nomme alors ses graphes elle-même)."""
    from ..adapter import techniques as _techniques
    if chaine.champ_de_technique is None:
        return []
    return _techniques.vues(c.catalog.techniques())


def _etapes_annoncees(chaine, technique=None) -> list[dict]:
    """Ce qu'une chaîne VA faire, avant de la lancer. Une étape à rôle annonce le
    graphe que la technique lui donne — sans technique nommée, celle par défaut,
    puisque c'est elle qu'un formulaire ouvre."""
    from ..adapter.chaines import workflow_annonce
    return [{"id": e.id, "genre": e.genre, "workflow": workflow_annonce(e, technique),
             **({"role": e.role} if e.role else {})} for e in chaine.etapes]


def _demande_plate(corps: dict) -> dict:
    """Le corps d'une intention, ramené aux champs que la chaîne peut recevoir.

    Ce que la passerelle possède (le nom du workflow, l'étiquette de sortie) ne
    lui est pas soumis ; les pièces jointes envoyées sous « media » comptent
    comme envoyées à la racine, puisque c'est la même chose dite deux façons.
    """
    reserves = {"workflow", "label", "kind", "media"}
    plate = {k: v for k, v in (corps or {}).items() if k not in reserves}
    plate.update({k: v for k, v in ((corps or {}).get("media") or {}).items()})
    return plate


def _valeurs_chaine(c, chaine, corps: dict) -> tuple[dict, list[str]]:
    """Ce que la chaîne va employer, et ce qui n'était pas pour elle.

    Rend ``(valeurs, non appliqués)`` : un réglage d'une AUTRE technique que
    celle choisie n'est pas refusé, il est écarté — sans quoi un raccourci
    enregistré sous une technique cassait au premier essai sous une autre.
    """
    from ..core import chaine as _noyau
    demande = _demande_plate(corps)
    techniques = c.catalog.techniques()
    technique = _noyau.technique_choisie(chaine, demande, techniques)
    return _noyau.valeurs(chaine, demande, _options_exposees(c, chaine, technique), techniques)


def _intention_detape(params: dict, label: str = "") -> Any:
    """Le run que décrit une étape « rendre », en intention.

    Les renvois non résolus (les résultats d'étapes, qui n'existent pas encore)
    sont ÉCARTÉS : c'est ce qui permet de décrire et d'estimer une chaîne avant
    de la lancer, sans inventer de valeur.
    """
    from ..core.intention import MediaKind, RenderIntent
    reglages = {k: v for k, v in params.items()
                if not (isinstance(v, str) and v.startswith("$"))}
    nom = str(reglages.pop("workflow", "") or "")
    media = {k: str(v) for k, v in (reglages.pop("media", None) or {}).items()
             if v and not (isinstance(v, str) and v.startswith("$"))}
    reglages.pop("label", None)
    reglages.pop("constraints", None)
    genre = reglages.pop("kind", None)
    return RenderIntent(workflow=nom, media=media, label=label,
                        kind=MediaKind(genre) if genre else None, **reglages)


def _estimation_chaine(c, chaine, valeurs: dict) -> dict:
    """La somme des étapes « rendre ». Muette dès qu'une seule ne se mesure pas.

    Additionner ce qui est connu en taisant ce qui ne l'est pas donnerait une
    estimation plus courte que la réalité — pire qu'une absence d'estimation.
    """
    from ..core import chaine as _noyau
    lignes: list[dict] = []
    total = bas = haut = 0.0
    manque: str | None = None
    technique = _noyau.technique_choisie(chaine, valeurs, c.catalog.techniques())
    for etape in chaine.rendus:
        params = _noyau.resoudre(etape.params, valeurs, {}, strict=False)
        # Le workflow d'une étape peut être CHOISI à l'appel — par un renvoi
        # (« $mode ») ou par la TECHNIQUE qui tient son rôle : c'est le nom
        # résolu qui a une mesure, pas le rôle ni le renvoi.
        from ..adapter.chaines import workflow_annonce
        vise = str(params.get("workflow") or workflow_annonce(etape, technique) or "")
        if etape.role is not None:
            params = {k: v for k, v in params.items() if k not in ("role", "technique")}
            params["workflow"] = vise
        estimation = None
        try:
            plan = c.orchestrator.build_plan(_intention_detape(params, label="estimation"))
            _with_work(c, plan)
            estimation = c.registry.estimate_duration(
                c.settings.host_id, plan.workflow, plan.work, plan.config,
                work_model=plan.work_model)
        except Exception as exc:            # noqa: BLE001 — un refus se dit, il n'arrête pas
            manque = manque or f"étape {etape.id} : {exc}"
        lignes.append({"id": etape.id, "workflow": vise, "estimate": estimation})
        if estimation is None:
            manque = manque or f"étape {etape.id} ({vise}) n'a jamais été mesurée ici"
        else:
            total += estimation.get("seconds") or 0
            bas += estimation.get("min") or estimation.get("seconds") or 0
            haut += estimation.get("max") or estimation.get("seconds") or 0
    sortie: dict[str, Any] = {"workflow": chaine.nom, "chaine": True, "etapes": lignes}
    if manque:
        sortie["estimate"] = None
        sortie["manque"] = manque
    else:
        sortie["estimate"] = {"seconds": round(total), "min": round(bas), "max": round(haut),
                              "basis": "somme des étapes"}
    return sortie


def _readiness_chaine(c, chaine) -> dict:
    """Une chaîne est praticable quand son PLAN et la technique PAR DÉFAUT le sont.

    Les autres techniques sont jugées aussi, mais une technique qui boite
    n'empêche pas le mode : c'est la technique qu'il faut éviter, pas le mode —
    et le taire aurait laissé l'utilisateur la choisir pour rien. Elle est donc
    NOMMÉE dans les avertissements.
    """
    from ..core import chaine as _noyau
    avertissements: list[dict] = []
    bloquante: dict | None = None
    non_juges: list[str] = []
    defaut = _noyau.technique_choisie(chaine, {}, c.catalog.techniques())

    def juger(nom: str, etape_id: str, technique: str | None, bloque: bool) -> None:
        nonlocal bloquante
        for w in c.registry.known_warnings(c.settings.host_id, nom):
            avertissements.append({"problem": w.get("problem"), "etape": etape_id,
                                   "detail": (w.get("detail") or "")[:300],
                                   "config": w.get("config"), "last_seen": w.get("ts"),
                                   **({"technique": technique} if technique else {})})
        blocages = c.registry.blocking_problems(c.settings.host_id, nom)
        if not blocages:
            return
        premier = blocages[0]
        ligne = {"problem": premier.get("problem"), "etape": etape_id, "workflow": nom,
                 "detail": (premier.get("detail") or "")[:300],
                 "config": premier.get("config"), "last_seen": premier.get("ts"),
                 **({"technique": technique} if technique else {})}
        if bloque and bloquante is None:
            bloquante = ligne
        elif not bloque:
            # Une autre technique que celle par défaut : un avertissement qui la
            # NOMME, pas un refus du mode.
            avertissements.append(ligne)

    for etape in chaine.rendus:
        if etape.role is not None:
            for nom_technique, technique in sorted(c.catalog.techniques().items()):
                role = technique.roles.get(etape.role)
                if role is not None:
                    juger(role.workflow, etape.id, nom_technique,
                          bloque=(defaut is not None and nom_technique == defaut.nom))
            continue
        nom = etape.workflow or ""
        if nom.startswith("$"):
            # Le workflow de cette étape est choisi à l'appel : la mémoire ne
            # peut rien en dire d'avance. Le taire ferait passer « pas jugé »
            # pour « rien à signaler ».
            non_juges.append(f"{etape.id} (le workflow est choisi par « {nom} »)")
            continue
        juger(nom, etape.id, None, bloque=True)
    if bloquante is None:
        return {"workflow": chaine.nom, "chaine": True, "runnable": True,
                "warnings": avertissements, "revisions": [], "non_juge": non_juges}
    return {"workflow": chaine.nom, "chaine": True, "runnable": False,
            "warnings": avertissements, "revisions": [], "non_juge": non_juges,
            "retry_hint": "POST /v1/render?force=true pour essayer malgré ce souvenir",
            **bloquante}


def _extras_admis(c, intent_in) -> dict:
    """Les champs hors modèle : admis SEULEMENT par une chaîne qui les expose.

    Le modèle d'intention accepte tout pour pouvoir en juger ici, où l'on sait
    quel workflow est visé. Sans ce contrôle, un nom mal orthographié repartait
    avec un 202 et ne pilotait rien.
    """
    extras = dict(getattr(intent_in, "model_extra", None) or {})
    if not extras:
        return {}
    try:
        spec = c.catalog.get_spec(intent_in.workflow)
    except Exception:
        spec = None
    exposes = set(spec.exposes) if spec is not None and spec.est_chaine else set()
    inconnus = sorted(k for k in extras if k not in exposes)
    if inconnus:
        cible = (spec.name if spec is not None else intent_in.workflow) or "(défaut)"
        raise UnknownWorkflowInputError(
            f"{cible} n'a pas de champ " + ", ".join(repr(k) for k in inconnus)
            + (f" — il accepte : {', '.join(sorted(exposes))}" if exposes else ""),
            workflow=cible, fields=inconnus)
    return extras


def _apercus_dir(c) -> Path:
    """Où vivent les aperçus animés des modes : à côté des jobs, jamais dans le
    dossier de sortie du moteur (un aperçu n'est pas un livrable)."""
    return Path(c.settings.hermes_db).parent / "apercus"


# Une seule liste de formats pour tout ce que la passerelle fabrique en image
# animée (l'aperçu d'un mode, celui d'un raccourci) : écrite deux fois, elle
# aurait fini par servir un GIF annoncé « image/webp ».
APERCU_FORMATS = raccourcis.FORMATS


def _apercu_fichier(c, name: str) -> tuple[Path, str] | None:
    """L'aperçu d'un mode s'il existe, avec son type : WebP animé de
    préférence, GIF quand l'encodeur manquait à la fabrication."""
    for ext, mime in APERCU_FORMATS:
        f = _apercus_dir(c) / f"{name}{ext}"
        if f.is_file():
            return f, mime
    return None


def _presentation(c, spec) -> dict:
    """La vitrine d'une entrée, avec son aperçu animé s'il en a un.

    L'adresse n'est annoncée que si le fichier est là : une vignette promise
    et absente ferait une image cassée dans chaque lanceur."""
    p = dict(spec.presentation)
    if _apercu_fichier(c, spec.name):
        p["apercu_url"] = f"/v1/workflows/{spec.name}/apercu"
    return p


# -- raccourcis ----------------------------------------------------------------
#
# Un raccourci est un ensemble de réglages enregistré pour un mode. Le MODE,
# avec ses défauts, reste le raccourci implicite — il n'est écrit nulle part ;
# les raccourcis enregistrés viennent après lui. Le lanceur ne fait que DÉSIGNER
# (une livraison devient un raccourci) : c'est ici qu'on valide, qu'on range et
# qu'on fabrique l'image.


def _raccourcis_base(c) -> Path:
    return Path(c.settings.hermes_db).parent


def _medias_du_mode(c, spec) -> list[str]:
    """Les pièces jointes d'un mode, par leur nom de champ.

    Ce sont exactement les champs qu'une fiche ne garde PAS : le nom du fichier
    déposé chez le moteur ne veut plus rien dire demain, et l'image se redépose
    à chaque fois.
    """
    if spec.est_chaine:
        return [nom for nom, champ in c.catalog.chaine(spec).champs.items()
                if champ.media is not None]
    from ..adapter import media_inputs
    graphe, liaisons = c.catalog.monter(spec)
    return [m["param"] for m in media_inputs.describe(liaisons, spec.titles,
                                                      spec.carried, graphe)]


def _decrire_champ(c, spec, chaine=None, technique=None):
    """De quoi HABILLER un écart : le libellé du champ, son unité, les libellés
    de ses valeurs — la même lecture que le formulaire, pas une seconde.

    Un écart écrit « fond : sepia » ne dit rien de plus que le corps de la
    requête ; « Fond de départ : Sépia » se lit sur une carte.

    Le champ est lu chez la technique EMPLOYÉE d'abord : deux techniques peuvent
    exposer le même nom, et pris chez n'importe laquelle, « fond : sepia » se
    lisait sous le libellé d'une autre technique (mesuré sur les quatre
    raccourcis en place). Ce que la technique employée ne porte pas
    est cherché chez les autres — un réglage non appliqué reste nommé.
    """
    from ..adapter import menus as _menus
    from ..core import chaine as _noyau

    def d_une_chaine(nom: str) -> dict:
        champ = (_noyau.champs_retenus(chaine, technique).get(nom)
                 or _noyau.champs_admis(chaine, c.catalog.techniques()).get(nom))
        if champ is None:
            return {}
        menu = _menu_declare(c, nom)
        options = champ.options
        if champ.options_depuis is not None:
            options, source = _options_declarees(c, champ.options_depuis)
            # Le menu déclaré AU NOM DU CHAMP reste le plus proche, comme au
            # formulaire : il retitre ce que la source rend, jamais l'inverse.
            menu = {**source, **menu}
        choix, _manque = _menus.choix(list(options) if options is not None else None, menu)
        return {"libelle": champ.libelle or menu.get("libelle") or nom,
                "unite": _menus.unite(nom, champ.unite or menu.get("unite")),
                "libelles_valeurs": {str(x["valeur"]): x["libelle"] for x in (choix or ())}}

    def d_un_graphe(nom: str) -> dict:
        menu = _menu_declare(c, nom)
        table = menu.get("libelles") if isinstance(menu.get("libelles"), dict) else {}
        return {"libelle": menu.get("libelle") or nom,
                "unite": _menus.unite(nom, menu.get("unite")),
                "libelles_valeurs": {str(v): (d or {}).get("libelle") or str(v)
                                     for v, d in table.items()}}

    return d_une_chaine if chaine is not None else d_un_graphe


def _vue_raccourci(c, spec, fiche: dict) -> dict:
    """La fiche telle qu'un lanceur la rend : ses écarts, et son aperçu s'il existe.

    Les écarts sont calculés À LA LECTURE, contre les défauts du mode
    d'aujourd'hui : figés dans le fichier, ils auraient continué d'annoncer
    « Sépia » comme un choix particulier le jour où le mode en a fait son
    défaut. L'adresse de l'aperçu n'est annoncée que si le fichier est là —
    une vignette promise et absente fait une image cassée par carte.
    """
    from ..core import chaine as _noyau
    chaine = c.catalog.chaine(spec) if spec.est_chaine else None
    # Les défauts contre lesquels l'écart se juge sont ceux de la technique que
    # CE raccourci emploie : le défaut d'une technique n'est pas un écart sous
    # elle, alors que jugé contre une autre il en aurait été un — et chacun de
    # ses réglages aurait alors figuré sur la carte.
    technique = (_noyau.technique_choisie(chaine, fiche.get("valeurs") or {},
                                          c.catalog.techniques())
                 if chaine is not None else None)
    defauts = _noyau.defauts(chaine, technique) if chaine is not None else spec.defaults
    vue = dict(fiche)
    vue["ecarts"] = raccourcis.ecarts(fiche.get("valeurs") or {}, defauts,
                                      _decrire_champ(c, spec, chaine, technique),
                                      getattr(c.catalog, "formats", None))
    ident = str(fiche.get("id") or "")
    if raccourcis.apercu_fichier(_raccourcis_base(c), spec.name, ident):
        vue["apercu_url"] = f"/v1/workflows/{spec.name}/raccourcis/{ident}/apercu"
    return vue


def _raccourcis_vus(c, spec) -> list[dict]:
    return [_vue_raccourci(c, spec, fiche)
            for fiche in raccourcis.lister(_raccourcis_base(c), spec.name)]


def _valeurs_de_raccourci(c, spec, valeurs: dict) -> dict:
    """Les valeurs d'un raccourci, validées PAR LE MODE et gardées typées.

    La passerelle fait autorité : ce qu'elle refuserait au lancement, elle le
    refuse à l'enregistrement. Sans ce contrôle, un raccourci gardait une durée
    de 400 s que le mode plafonne à 79, et n'échouait qu'au moment de lancer —
    bien après que l'utilisateur l'ait nommé.
    """
    from ..core import chaine as _noyau
    if spec.est_chaine:
        chaine = c.catalog.chaine(spec)
        techniques = c.catalog.techniques()
        champs = _noyau.champs_admis(chaine, techniques)
        inconnus = sorted(k for k in valeurs if k not in champs)
        if inconnus:
            raise UnknownWorkflowInputError(
                f"chaîne {spec.name!r} : champ(s) qu'elle n'expose pas : {', '.join(inconnus)}",
                workflow=spec.name, fields=inconnus, accepts=sorted(champs))
        pieces = sorted(k for k in valeurs if champs[k].media is not None)
        if pieces:
            raise UnknownWorkflowInputError(
                f"{spec.name!r} : une pièce jointe ne s'enregistre pas dans un raccourci "
                f"({', '.join(pieces)}) — elle se redépose à chaque fois",
                workflow=spec.name, fields=pieces)
        # Les options de la technique CHOISIE : « fond » n'accepte pas la même
        # chose d'une technique à l'autre, et c'est ce que le raccourci
        # enregistre qui décide.
        technique = _noyau.technique_choisie(chaine, valeurs, techniques)
        options = _options_exposees(c, chaine, technique)
        retenus = _noyau.champs_retenus(chaine, technique)
        return {nom: _noyau.valeur_de(retenus.get(nom) or champs[nom], brute,
                                      options.get(nom))
                for nom, brute in valeurs.items()}
    admis = (set(intent_fields(spec.profile.accepts))
             | set(derivable_params(spec.kind, spec.profile.accepts, spec.defaults))
             | set(spec.profile.accepts))
    inconnus = sorted(k for k in valeurs if k not in admis)
    pieces = sorted(k for k in valeurs if k in admis and is_media_param(k))
    if inconnus or pieces:
        refuses = inconnus + pieces
        raise UnknownWorkflowInputError(
            f"{spec.name!r} n'enregistre pas " + ", ".join(repr(k) for k in refuses)
            + (" (une pièce jointe se redépose à chaque fois)" if pieces else "")
            + f" — il accepte : {', '.join(sorted(a for a in admis if not is_media_param(a)))}",
            workflow=spec.name, fields=refuses)
    return dict(valeurs)


async def _apercu_de_raccourci(c, spec, ident: str, job) -> None:
    """Fabriquer l'aperçu d'un raccourci depuis la livraison qui l'a fait naître.

    Le même mécanisme que l'aperçu d'un mode : la première vidéo du run, résumée
    en image animée. Un run qui n'a livré aucune vidéo (une analyse, un plan)
    n'est pas une erreur — il n'a simplement rien à montrer.
    """
    from ..adapter import montage_video
    if job is None:
        return
    videos = [a for a in job.artifacts if a.kind == "video" and a.path
              and Path(a.path).is_file()]
    if not videos:
        return
    await run_in_threadpool(montage_video.apercu_anime, Path(videos[0].path),
                            raccourcis.cible_apercu(_raccourcis_base(c), spec.name, ident))


def _livraison_du_mode(c, spec, job_id: str):
    """Le run désigné, s'il est bien une livraison DE CE MODE.

    Enregistrer les réglages d'un autre mode aurait fait un raccourci que la
    validation refuse ensuite champ par champ, sans jamais dire la vraie cause.
    """
    job = c.store.get(str(job_id))            # 404 problem+json si inconnu
    if job.workflow != spec.name:
        raise UnknownWorkflowInputError(
            f"le run {job.id} est une livraison de {job.workflow!r}, pas de {spec.name!r}",
            workflow=spec.name, job_id=job.id, job_workflow=job.workflow)
    return job


def _titre_de_raccourci(titre: str) -> str:
    propre = str(titre or "").strip()
    if not propre:
        raise InputValueRefusedError(
            "un raccourci se retrouve par son titre : il est requis", field="titre")
    return propre


def _spec_dict(spec) -> dict:
    if spec.est_chaine:
        return {"name": spec.name, "kind": spec.kind, "chaine": True,
                "presentation": spec.presentation, "accepts": sorted(spec.exposes),
                "intent_fields": sorted(spec.exposes), "derived": [],
                "defaults": spec.defaults, "limits": spec.limits,
                "workflow_source": str(spec.workflow_path), "bindings": {},
                "dependencies": spec.dependencies, "source": None, "source_hash": None}
    return {
        "name": spec.name,
        "kind": spec.kind,
        "accepts": sorted(spec.profile.accepts),
        "intent_fields": sorted(set(intent_fields(spec.profile.accepts))
                                | set(derivable_params(spec.kind, spec.profile.accepts,
                                                       spec.defaults))),
        "derived": derivable_params(spec.kind, spec.profile.accepts, spec.defaults),
        "defaults": spec.defaults,
        "limits": spec.limits,
        "source": spec.source,
        "source_hash": spec.source_hash,
        "dependencies": spec.dependencies,
        "workflow_source": str(spec.workflow_path),
        "bindings": {k: {"node": b.node, "input": b.input} for k, b in spec.bindings.items()},
        "chaine": False,
        "presentation": spec.presentation,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="ComfyUI Bridge",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=lifespan,
    )
    install_problem_handlers(app)

    # Allow the ComfyUI extension (served from :8188) to POST workflows here.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_methods=["*"], allow_headers=["*"],
    )

    # Resolve settings now so the artifact store can be mounted at construction
    # time (the mount needs a concrete directory before the lifespan runs).
    eff = settings or Settings.from_env()
    if settings is not None:  # allow tests to inject a prebuilt container
        app.state.container = build_container(settings)

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {"service": "comfyui-bridge", "version": __version__, "docs": "/docs", "ui": "/ui"}

    @app.get("/healthz", tags=["meta"])
    async def healthz(request: Request) -> dict:
        c = request.app.state.container
        return {
            "status": "ok",
            "host": c.settings.host_id,
            "hermes_mode": c.settings.hermes_mode,
            "backend": c.settings.comfy_backend,
            "dry_run": c.settings.dry_run and c.settings.comfy_backend != "http",  # backend "cli" only
            "comfyui_base_url": c.settings.comfyui_base_url if c.settings.comfy_backend == "http" else None,
            "engine": c.engine.name,
            "engine_state": c.engine_state.get("state"),
            "output_dir": str(c.settings.comfy_output_dir.resolve()),
            # The hard ceiling on ONE job here. A caller that plans a longer
            # wait than this is planning something that cannot happen: the job
            # is killed at this mark whatever it asked for. Measured cost of not
            # saying it: a caller waited an hour for a run it had budgeted four
            # hours for, and learned the ceiling from the failure.
            "job_max_duration_s": c.settings.comfyui_total_timeout_s,
        }

    @app.get("/v1/materiel", tags=["meta"])
    async def materiel_du_poste(request: Request) -> dict:
        """Ce que ce poste DÉCLARE, ce qu'il a vraiment, et le budget qui en sort.

        Un rendu découpé en vingt-trois tranches doit pouvoir s'expliquer sans
        lire le code : d'où vient le budget, et si le fichier dit vrai. Un
        fichier qui annonce plus de mémoire que la machine n'en a est le genre
        d'erreur qu'on ne découvrait qu'au premier rendu long, en panne sèche.
        """
        from ..adapter import materiel as _materiel
        c = request.app.state.container
        return _materiel.etat(getattr(c, "materiel", None),
                              int(getattr(c.settings, "tranche_octets", 0) or 0))

    @app.get("/v1/backend", tags=["backend"])
    async def backend_status(request: Request) -> dict:
        """Backend type + live connection test (probe the ComfyUI server if http)."""
        c = request.app.state.container
        info: dict = {"backend": c.settings.comfy_backend}
        probe = getattr(c.backend, "probe", None)
        if callable(probe):
            info["probe"] = probe()
        else:
            info["probe"] = {"available": None, "reason": "backend has no server to probe"}
        return info

    def _creer(c, intent_in: IntentIn, corps: dict, background: BackgroundTasks,
               response: Response, force: bool = False,
               reprise_de: str | None = None) -> JobOut:
        """Le seul verbe de création, qu'on vise un graphe ou une chaîne.

        Écrit une fois : le rejeu et la reprise passent exactement par ici,
        sinon il aurait fallu tenir plusieurs façons de lancer un run, qui
        auraient divergé.
        """
        _extras_admis(c, intent_in)
        spec = c.catalog.get_spec(intent_in.workflow)
        if spec.est_chaine:
            job = _lancer_chaine(c, spec, corps, background, force, reprise_de)
            response.headers["Location"] = f"/v1/jobs/{job.id}"
            return JobOut.of(job)
        if reprise_de:
            raise InputValueRefusedError(
                f"le run {reprise_de} n'est pas une chaîne : rien à reprendre — le rejouer",
                job_id=reprise_de)
        orch = c.orchestrator
        # UN RENDU DIRECT SE TRANCHE COMME UNE ÉTAPE DE CHAÎNE. Le mécanisme
        # ne regarde ni le mode ni sa catégorie ni qui appelle : seulement le
        # graphe (un nœud qui déclare la tranche) et le budget. Sans cela, le
        # même nœud tenait en mémoire dans une chaîne et débordait appelé seul.
        tranche = _lancer_par_tranches(c, spec, intent_in, corps, background, force)
        if tranche is not None:
            response.headers["Location"] = f"/v1/jobs/{tranche.id}"
            return JobOut.of(tranche)
        # accept() plans + reconciles synchronously; a strict rejection raises
        # HardwareReconciliationError here and leaves as a 422 problem+json.
        job, plan = orch.accept(intent_in.to_domain(), force=force)
        _with_work(c, plan)
        # La demande TELLE QUE REÇUE, gardée avec le run : c'est ce qui le rend
        # rejouable sans que l'appelant réassemble quoi que ce soit.
        c.store.set_demande(job.id, corps)
        # Ce qui tourne, c'est l'ANALYSE stockée. Si la source a bougé depuis,
        # le run exécute l'ancienne version : mesuré, il a rendu un .flac là où
        # la nouvelle sauve un .mp3, sans un mot.
        state = _freshness(c, c.catalog.get_spec(plan.workflow))
        if state.get("source_changed"):
            c.store.append_log(
                job.id, "ATTENTION : ce workflow a changé dans ComfyUI depuis son "
                        f"extraction ({state.get('source_change_reason')}) — c'est "
                        "l'analyse précédente qui tourne ; ré-extrais pour prendre "
                        "les nouveautés")
        background.add_task(orch.execute, job.id, plan)
        response.headers["Location"] = f"/v1/jobs/{job.id}"
        return JobOut.of(job)

    def _lancer_par_tranches(c, spec, intent_in: IntentIn, corps: dict,
                             background: BackgroundTasks, force: bool = False):
        """Un graphe appelé directement, rendu par tranches quand il le faut.

        La même boucle que pour une étape de chaîne — le graphe devient une
        chaîne d'une seule étape « rendu », dont le livrable est le recollage
        des tranches. Rend None quand le rendu tient d'un seul tenant (ou que
        le nœud ne sait pas trancher) : le chemin ordinaire reprend alors.
        Hermes est consulté AVANT, comme pour un run entier : un problème
        connu se refuse en 422 ici, pas à la troisième tranche.
        """
        from ..adapter.chaines import RunnerDeChaines, etapes_initiales
        from ..core import chaine as _noyau
        from ..core.cost import config_fingerprint
        intention = intent_in.to_domain()
        reglages = {champ: getattr(intention, champ)
                    for champ in RenderIntent.__dataclass_fields__
                    if champ not in ("workflow", "media", "label", "kind", "constraints")
                    and getattr(intention, champ) not in (None, "", {}, ())}
        if intention.constraints:
            return None                      # une contrainte n'est pas une valeur de nœud
        # Un texte qui commence par « $ » serait lu comme un renvoi de chaîne :
        # ce rendu-là part entier, comme avant.
        if any(isinstance(v, str) and v.startswith("$") for v in reglages.values()):
            return None
        runner = RunnerDeChaines(c)
        try:
            tranches = runner.tranches_pour(spec.name, reglages)
        except MediaAssemblyError as exc:
            # Une demande qui dépasse ce que ce poste sait découper n'est pas une
            # panne du service : c'est la DEMANDE qui est hors de portée, et le
            # message dit déjà quoi baisser. Rendue en 500, elle faisait porter à
            # la passerelle la faute d'un appelant qui a demandé trop grand — la
            # même confusion que « entrée inconnue » pour une durée trop longue.
            # Dans une CHAÎNE le même refus reste un échec d'étape : là, le job
            # a déjà été accepté.
            raise InputValueRefusedError(exc.detail, **exc.extensions) from exc
        if tranches is None:
            return None
        c.orchestrator.plan_and_reconcile(intention, force=force)     # 422 si Hermes refuse
        brut = {"version": 1, "chaine": spec.name,
                "resume": f"{spec.name}, rendu par tranches",
                "etapes": [{"id": "rendu", "rendre": {"workflow": spec.name,
                                                        "media": dict(intention.media),
                                                        **reglages}}],
                "livrable": "$rendu.livrable"}
        chaine = _noyau.lire(brut, spec.name)
        etiquette = "".join(ch for ch in str(corps.get("label") or "")
                            if ch.isalnum() or ch in "-_")[:40] or spec.name
        job = c.store.create(kind=spec.kind, workflow=spec.name,
                             config=config_fingerprint(reglages), params=reglages,
                             demande=dict(corps or {}))
        c.store.set_etapes(job.id, etapes_initiales(chaine))
        # Le même avertissement que le chemin ordinaire : ce sont les rendus les
        # plus longs qui tournent ici, et ce sont eux qui perdraient le plus à
        # rendre une analyse périmée sans un mot.
        state = _freshness(c, spec)
        if state.get("source_changed"):
            c.store.append_log(
                job.id, "ATTENTION : ce workflow a changé dans ComfyUI depuis son "
                        f"extraction ({state.get('source_change_reason')}) — c'est "
                        "l'analyse précédente qui tourne ; ré-extrais pour prendre "
                        "les nouveautés")
        c.store.append_log(job.id, f"accepted: '{spec.name}' rendu par tranches "
                                   f"({tranches['nombre']} tranches pour tenir en mémoire) — "
                                   f"sortie « cortex/{etiquette} »")
        # Les réglages, et non {} : ce sont eux que le runner porte à la mémoire
        # des problèmes à la fin (`registry.record`). Vides, le succès comme
        # l'échec d'un rendu tranché se classaient sous une configuration vide,
        # alors que le job lui-même est créé sous « 160x120 » juste au-dessus :
        # un OOM rencontré en tranches n'aurait pas refusé le même run ensuite.
        # La chaîne synthétique n'expose aucun champ : rien d'autre ne les lit.
        background.add_task(runner.executer, job.id, chaine, reglages, etiquette, force)
        return c.store.get(job.id)

    def _lancer_chaine(c, spec, corps: dict, background: BackgroundTasks,
                       force: bool = False, reprise_de: str | None = None):
        from ..adapter.chaines import RunnerDeChaines, etapes_initiales
        from ..core.cost import config_fingerprint
        chaine = c.catalog.chaine(spec)
        # 422 sur l'inconnu, sur le hors-bornes ; ce qui appartient à une AUTRE
        # technique n'est pas refusé, il est écarté — et dit plus bas.
        valeurs, non_appliques = _valeurs_chaine(c, chaine, corps)
        runner = RunnerDeChaines(c)
        technique = runner.technique_voulue(chaine, valeurs)
        etiquette = "".join(ch for ch in str(corps.get("label") or "")
                            if ch.isalnum() or ch in "-_")[:40] or spec.name
        job = c.store.create(kind=spec.kind, workflow=spec.name,
                             config=config_fingerprint(valeurs), params=valeurs,
                             demande=dict(corps or {}))
        c.store.set_etapes(job.id, etapes_initiales(chaine, technique))
        c.store.append_log(job.id, f"accepted: chaîne '{spec.name}' — "
                                   f"{len(chaine.etapes)} étapes, sortie « cortex/{etiquette} »")
        if non_appliques:
            # DIT, jamais tu : un réglage qui ne part nulle part ressemble trait
            # pour trait à un réglage appliqué, et l'utilisateur cherchait
            # ensuite pourquoi sa valeur n'avait rien changé.
            quelle = technique.nom if technique is not None else "choisie"
            c.store.append_log(
                job.id, f"non appliqué — n'est pas un réglage de la technique {quelle} : "
                        f"{', '.join(non_appliques)}")
        if reprise_de:
            c.store.append_log(job.id, f"reprise demandée du job {reprise_de}")
        background.add_task(runner.executer, job.id, chaine, valeurs, etiquette, force,
                            reprise_de)
        return c.store.get(job.id)

    @app.post("/v1/render", status_code=202, response_model=JobOut, tags=["render"])
    async def render(
        intent_in: IntentIn,
        request: Request,
        response: Response,
        background: BackgroundTasks,
        force: bool = False,
    ) -> JobOut:
        c = request.app.state.container
        corps = await request.json()
        return _creer(c, intent_in, corps if isinstance(corps, dict) else {},
                      background, response, force)

    @app.post("/v1/jobs/{job_id}/rejouer", status_code=202, response_model=JobOut,
              tags=["render"])
    async def rejouer(job_id: str, request: Request, response: Response,
                      background: BackgroundTasks, corps: RejeuIn | None = None,
                      force: bool = False) -> JobOut:
        """Rejouer un run, à l'identique ou avec des réglages changés.

        Le serveur garde la demande telle qu'il l'a reçue ; rejouer, c'est la
        renvoyer. Reconstruire la demande à partir des paramètres résolus
        rendait autre chose que ce qui avait été demandé — et obligeait chaque
        client à savoir comment la passerelle résout.
        """
        c = request.app.state.container
        job = c.store.get(job_id)                         # 404 si inconnu
        demande = dict(job.demande or {})
        if not demande:
            raise UnknownWorkflowInputError(
                f"le run {job_id} n'a pas gardé la demande qui l'a produit : "
                f"il est antérieur à cette mémoire", job_id=job_id)
        demande.setdefault("workflow", job.workflow)
        demande.update(dict((corps.reglages if corps else {}) or {}))
        return _creer(c, IntentIn.model_validate(demande), demande, background,
                      response, force)

    @app.post("/v1/jobs/{job_id}/reprendre", status_code=202, response_model=JobOut,
              tags=["render"])
    async def reprendre(job_id: str, request: Request, response: Response,
                        background: BackgroundTasks, force: bool = False) -> JobOut:
        """Reprendre une chaîne échouée LÀ OÙ elle s'est arrêtée.

        Les étapes qui avaient abouti sont reprises telles quelles — leurs
        livrables relus, leurs récits relus sur leurs sous-jobs — et la chaîne
        repart à l'étape en échec, avec la même demande. Six heures de
        déroulement 4K ne se perdent pas pour un dépôt refusé à l'étape
        suivante (mesuré le 2026-09-16). Un run qui n'a pas échoué, ou qui
        n'est pas une chaîne, n'a rien à reprendre : 422, dit.
        """
        c = request.app.state.container
        job = c.store.get(job_id)                         # 404 si inconnu
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise InputValueRefusedError(
                f"le run {job_id} a fini {job.status.value} : rien à reprendre",
                job_id=job_id)
        if not job.etapes:
            raise InputValueRefusedError(
                f"le run {job_id} n'est pas une chaîne : rien à reprendre — le rejouer",
                job_id=job_id)
        demande = dict(job.demande or {})
        if not demande:
            raise UnknownWorkflowInputError(
                f"le run {job_id} n'a pas gardé la demande qui l'a produit : "
                f"il est antérieur à cette mémoire", job_id=job_id)
        demande.setdefault("workflow", job.workflow)
        return _creer(c, IntentIn.model_validate(demande), demande, background,
                      response, force, reprise_de=job_id)

    @app.post("/v1/preview", tags=["render"])
    async def preview(intent_in: IntentIn, request: Request) -> dict:
        # Same plan + Hermes reconciliation as /v1/render (can 422), but stops
        # before execution and returns the injected ComfyUI graph instead.
        # Looking at a graph executes nothing: it must never be refused, even
        # for a workflow a past problem stands against. Hermes' opinion is
        # reported alongside instead of blocking the view.
        c = request.app.state.container
        _extras_admis(c, intent_in)
        spec = c.catalog.get_spec(intent_in.workflow)
        if spec.est_chaine:
            from ..core import chaine as _noyau
            chaine = c.catalog.chaine(spec)
            corps = await request.json()
            valeurs, non_appliques = _valeurs_chaine(
                c, chaine, corps if isinstance(corps, dict) else {})
            technique = _noyau.technique_choisie(chaine, valeurs, c.catalog.techniques())
            # Pas de graphe : une chaîne n'en a pas. Ce qu'il y a à voir avant de
            # dépenser, c'est la SUITE des étapes (avec le graphe que la technique
            # donne à chaque rôle) et les valeurs qu'elles recevront.
            return {"workflow": spec.name, "chaine": True, "params": valeurs,
                    "technique": technique.nom if technique is not None else None,
                    "non_appliques": non_appliques,
                    "etapes": [{**ligne,
                                "params": _noyau.resoudre(e.params, valeurs, {}, strict=False)}
                               for e, ligne in zip(chaine.etapes,
                                                   _etapes_annoncees(chaine, technique))],
                    "livrable": chaine.livrable}
        plan = c.orchestrator.build_plan(intent_in.to_domain())
        verdict = c.reconciler.reconcile(plan)
        pv = c.backend.preview(plan)
        return {
            "kind": plan.kind,
            "workflow": plan.workflow,          # the NAME
            "config": plan.config,
            "params": plan.params,
            "reconciliation": {"accepted": verdict.accepted, "reason": verdict.reason,
                               "problem": verdict.problem},
            # From experience only: absent when nothing comparable was ever run.
            "effective": _with_work(c, plan),
            "work": plan.work,
            # Values that will not reach the graph: better said than silent.
            "ignored": list(plan.ignored),
            "estimated_duration": c.registry.estimate_duration(
                c.settings.host_id, plan.workflow, plan.work, plan.config,
                work_model=plan.work_model),
            "graph": pv.pop("workflow"),        # the injected GRAPH (own key)
            **pv,
        }

    @app.get("/v1/workflows", tags=["render"])
    async def list_workflows(request: Request) -> dict:
        """The reconciliation file: workflows a pipeline can call, by name."""
        from ..adapter import media_inputs
        c = request.app.state.container
        cat = c.catalog
        items = {}
        for name in cat.names():
            spec = cat.get_spec(name)
            if spec.est_chaine:
                chaine = cat.chaine(spec)
                items[name] = {
                    "kind": spec.kind,
                    "chaine": True,
                    "presentation": _presentation(c, spec),
                    "resume": chaine.resume,
                    # Ce qu'elle enchaîne : un appelant doit pouvoir dire ce
                    # qu'il lance avant de le lancer — avec le graphe que la
                    # technique par DÉFAUT donne à chaque rôle, puisque c'est
                    # elle qu'un formulaire ouvre.
                    "etapes": _etapes_annoncees(chaine, _technique_par_defaut(c, chaine)),
                    # Les TECHNIQUES entre lesquelles ce mode choisit. Une de
                    # plus est un fichier de plus : un lanceur qui tiendrait sa
                    # propre liste la verrait vieillir au premier ajout.
                    "techniques": _techniques_vues(c, chaine),
                    "accepts": sorted(spec.exposes),
                    "intent_fields": sorted(spec.exposes),
                    # Praticable = toutes ses étapes le sont.
                    "runnable": _readiness_chaine(c, chaine)["runnable"],
                    "warned": bool(_readiness_chaine(c, chaine)["warnings"]),
                    "source_changed": False,
                    "derived": [],
                    "carried": {},
                    "neutral_for": [],
                    "media_inputs": _habiller_medias(c, _media_inputs_chaine(chaine),
                                                     spec.aides),
                    # Les réglages enregistrés sous ce mode : la vitrine les
                    # montre à sa suite, le mode lui-même valant « réglages par
                    # défaut ». Une seconde requête par entrée les aurait fait
                    # apparaître carte après carte.
                    "raccourcis": _raccourcis_vus(c, spec),
                    "defaults": spec.defaults,
                    "limits": spec.limits,
                    "dependencies": spec.dependencies,
                    "source": None,
                    "workflow_source": str(spec.workflow_path),
                    "bindings": {},
                }
                continue
            items[name] = {
                "kind": spec.kind,
                "chaine": False,
                # La vitrine : sans `categorie`, l'entrée reste technique et un
                # lanceur ne la propose pas.
                "presentation": _presentation(c, spec),
                # What this workflow can actually receive. A field it does not
                # bind goes nowhere: offering it would be a lie.
                "accepts": sorted(spec.profile.accepts),
                # The names to actually put in a render request — the contract a
                # caller programs against, UI or not.
                "intent_fields": sorted(set(intent_fields(spec.profile.accepts))
                                        | set(derivable_params(spec.kind, spec.profile.accepts,
                                                               spec.defaults))),
                # Same memory as /readiness and as the reconciler: a workflow
                # known to fail here must not be the one the console opens on.
                "runnable": not c.registry.blocking_problems(c.settings.host_id, name),
                # Rien ne l'empêche de tourner, mais quelque chose s'est déjà mal
                # passé ici : à dire, et à ne pas proposer d'emblée.
                "warned": bool(c.registry.known_warnings(c.settings.host_id, name)),
                # L'extrait est-il encore fidèle à sa source ? Sans cette ligne,
                # un appelant pilotait une analyse périmée sans le savoir.
                **_freshness(c, spec),
                # Drivable without a node of its own, by conversion (seconds -> frames).
                "derived": derivable_params(spec.kind, spec.profile.accepts, spec.defaults),
                # Media already inside the workflow: used as-is if not replaced.
                "carried": spec.carried,
                # …and those a neutral element can stand in for.
                "neutral_for": list(spec.profile.neutral_for),
                # Les pièces jointes, une par entrée média du workflow, avec le
                # nom que l'auteur a donné au nœud. Un workflow à quatre images
                # en déclare quatre : c'est CE contrat qu'un formulaire suit.
                "media_inputs": _habiller_medias(c, media_inputs.describe(
                    cat.monter(spec)[1], spec.titles, spec.carried, cat.monter(spec)[0]),
                    spec.aides),
                # Comme pour une chaîne : ce qu'on a enregistré sous ce mode.
                "raccourcis": _raccourcis_vus(c, spec),
                "defaults": spec.defaults,
                "limits": spec.limits,
                "dependencies": spec.dependencies,
                "source": spec.source,
                "workflow_source": str(spec.workflow_path),
                "bindings": {k: {"node": b.node, "input": b.input} for k, b in spec.bindings.items()},
            }
        from ..core.intention import MEDIA_CATEGORIES, ConstraintOp, MediaKind
        return {
            "default": cat.default_name(),
            # Les rubriques de la vitrine, déclarées au fichier de réconciliation.
            # Un lanceur qui tiendrait sa propre liste de catégories la verrait
            # vieillir dès qu'une entrée change de rangement.
            "categories": cat.categories,
            # Le vocabulaire des FORMATS (orientations, résolutions), déclaré au
            # même endroit. Un lanceur le rend en deux listes et TRADUIT le choix
            # en width/height — la règle est géométrique, pas métier : portrait ⇒
            # la largeur est le petit côté. Deux listes vides quand rien n'est
            # déclaré : largeur et hauteur restent alors des champs ordinaires.
            "formats": cat.formats,
            "workflows": items,
            # Des graphes enregistrés qu'une entrée déclarée du fichier de
            # réconciliation recouvre : ce qui est masqué doit se voir.
            "shadowed_by_declaration": list(getattr(cat, "shadowed", ())),
            # Des titres déclarés pour un workflow absent : renommé ou retiré.
            "presentation_sans_workflow": list(getattr(cat, "vitrines_orphelines", ())),
            # Vocabulary comes from the domain enums — the UI must not keep a copy.
            "vocabulary": {
                "kinds": [k.value for k in MediaKind],
                "constraint_ops": [o.value for o in ConstraintOp],
                # Fields a caller may leave unset to inherit the workflow default.
                # Les pièces jointes n'y figurent pas : elles ne sont pas une
                # liste fixe, chaque workflow déclare les siennes (media_inputs).
                "params": ["width", "height", "batch", "steps", "cfg", "fps",
                           "duration_s", "seed", "negative_prompt"],
                "media_categories": list(MEDIA_CATEGORIES),
            },
        }

    @app.post("/v1/workflows", status_code=201, tags=["workflows"])
    async def import_workflow(body: WorkflowImportIn, request: Request) -> dict:
        """Ingest a ComfyUI API-format workflow: auto-bind it, record its
        dependencies + provenance, make it callable.

        No hand-written bindings — they are derived from the graph. If ``source``
        (a ComfyUI saved-workflow name) is given, its current hash is recorded so
        update detection works later. Verify with POST /v1/preview.
        """
        from ..adapter.comfyui_client import source_hash as _hash
        c = request.app.state.container
        src_hash, titles = None, {}
        if body.source:
            # La source rend possible la détection de mise à jour, et porte les
            # noms de nœuds de l'auteur. Injoignable, on importe sans elle.
            try:
                saved = await run_in_threadpool(c.comfyui.get_saved_workflow, body.source)
                src_hash = _hash(saved)
                from ..adapter.labels import titles_from_ui_workflow
                titles = titles_from_ui_workflow(saved)
            except Exception:
                pass
        return await _ingest(c, body.name, body.workflow, body.source, src_hash, titles)

    # Déclarée AVANT "/v1/workflows/{name}" : sinon "updates" est lu comme
    # un nom de workflow et la route répond 400.
    @app.delete("/v1/workflows/{name}", tags=["workflows"])
    async def remove_workflow(name: str, request: Request) -> dict:
        """Retirer un extrait du catalogue.

        Ce qui s'ingère doit pouvoir se retirer : une source supprimée dans
        ComfyUI laissait un extrait orphelin, toujours appelable et que rien ne
        pouvait sortir de la liste."""
        from ..adapter.freshness import forget
        c = request.app.state.container
        out = c.catalog.unregister(name)
        forget(name)
        return out

    @app.get("/v1/workflows/updates", tags=["workflows"])
    async def workflow_updates(request: Request) -> dict:
        """Les extraits que leur source a dépassés — la notification, en une
        requête, pour un flux qui n'affiche pas de console."""
        c = request.app.state.container
        stale = []
        for name in c.catalog.names():
            spec = c.catalog.get_spec(name)
            state = _freshness(c, spec)
            if state.get("source_changed"):
                stale.append({"workflow": name, "source": spec.source,
                              "reason": state.get("source_change_reason"),
                              "extracted_at": spec.extracted_at})
        return {"count": len(stale), "updates": stale}

    @app.get("/v1/workflows/{name}/apercu", tags=["workflows"], include_in_schema=True)
    async def workflow_apercu(name: str, request: Request) -> FileResponse:
        """L'aperçu animé d'un mode : ce qu'il produit, en six secondes de GIF."""
        c = request.app.state.container
        c.catalog.get_spec(name)                       # 400 si inconnu
        trouve = _apercu_fichier(c, name)
        if trouve is None:
            raise UnknownWorkflowInputError(
                f"{name!r} n'a pas d'aperçu : en désigner un avec PUT /v1/workflows/{name}/apercu "
                f"(un job livré, ou un fichier du dossier de sortie)", workflow=name)
        fichier, mime = trouve
        return FileResponse(fichier, media_type=mime, headers={"Cache-Control": "no-cache"})

    @app.put("/v1/workflows/{name}/apercu", status_code=201, tags=["workflows"])
    async def workflow_apercu_depuis(name: str, request: Request) -> dict:
        """Faire d'une vidéo livrée l'aperçu de ce mode.

        Corps : `{"job_id": "…"}` (le premier livrable vidéo de ce run) ou
        `{"path": "…"}` (un fichier du dossier de sortie, jamais ailleurs). La
        passerelle fabrique le GIF elle-même : un lanceur ne manipule pas
        d'images, il désigne."""
        from ..adapter import montage_video
        c = request.app.state.container
        c.catalog.get_spec(name)
        corps = await request.json()
        if not isinstance(corps, dict):
            raise UnknownWorkflowInputError("un objet JSON était attendu", workflow=name)
        source: Path | None = None
        if corps.get("job_id"):
            job = c.store.get(str(corps["job_id"]))
            videos = [a for a in job.artifacts if a.kind == "video" and a.path]
            if not videos:
                raise UnknownWorkflowInputError(
                    f"le job {job.id} n'a livré aucune vidéo", workflow=name, job_id=job.id)
            source = Path(videos[0].path)
        elif corps.get("path"):
            source = Path(str(corps["path"]))
        else:
            raise UnknownWorkflowInputError("il manque « job_id » ou « path »", workflow=name)
        out_dir = c.settings.comfy_output_dir.resolve()
        try:
            source.resolve().relative_to(out_dir)
        except ValueError:
            raise UnknownWorkflowInputError(
                f"{source} n'est pas dans le dossier de sortie : seul un livrable peut "
                f"devenir un aperçu", workflow=name)
        if not source.is_file():
            raise UnknownWorkflowInputError(f"{source} introuvable", workflow=name)
        cible = _apercus_dir(c) / name            # l'extension suit le format produit
        fait = await run_in_threadpool(montage_video.apercu_anime, source, cible)
        return {"workflow": name, "apercu_url": f"/v1/workflows/{name}/apercu",
                "source": str(source.resolve()), **fait}

    @app.delete("/v1/workflows/{name}/apercu", tags=["workflows"])
    async def workflow_apercu_retirer(name: str, request: Request) -> dict:
        c = request.app.state.container
        existait = False
        for ext, _mime in APERCU_FORMATS:
            fichier = _apercus_dir(c) / f"{name}{ext}"
            if fichier.is_file():
                fichier.unlink()
                existait = True
        return {"workflow": name, "removed": existait}

    # Déclarées AVANT "/v1/workflows/{name}", comme les routes d'aperçu : une
    # route à un seul segment lue en premier avalerait les suivantes.
    @app.get("/v1/workflows/{name}/raccourcis", tags=["workflows"])
    async def workflow_raccourcis(name: str, request: Request) -> dict:
        """Les ensembles de réglages enregistrés pour ce mode.

        Le mode lui-même, avec ses défauts, n'est pas dans la liste : il est le
        raccourci implicite, et un lanceur le montre toujours en premier."""
        c = request.app.state.container
        spec = c.catalog.get_spec(name)                # 400 si le mode est inconnu
        return {"workflow": spec.name, "raccourcis": _raccourcis_vus(c, spec)}

    @app.post("/v1/workflows/{name}/raccourcis", status_code=201, tags=["workflows"])
    async def workflow_raccourci_creer(name: str, corps: RaccourciIn,
                                       request: Request) -> dict:
        """Enregistrer un ensemble de réglages, depuis une livraison ou tel quel.

        C'est le geste que le lanceur relaie après un run réussi : « garde ça ».
        Les valeurs viennent de la DEMANDE du run (sans les pièces jointes, qui
        se redéposent), recouvertes par ce que le corps nomme ; l'aperçu est
        fabriqué ici, depuis la vidéo livrée — un lanceur ne manipule pas
        d'images, il désigne.
        """
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        titre = _titre_de_raccourci(corps.titre)
        if not corps.job_id and corps.valeurs is None:
            raise InputValueRefusedError(
                "un raccourci part d'une livraison (« job_id ») ou de réglages "
                "(« valeurs ») : il en faut au moins un", field="job_id")
        job = _livraison_du_mode(c, spec, corps.job_id) if corps.job_id else None
        brutes = raccourcis.filtrer_demande(job.demande if job else {},
                                            _medias_du_mode(c, spec))
        brutes.update(corps.valeurs or {})
        valeurs = _valeurs_de_raccourci(c, spec, brutes)
        base = _raccourcis_base(c)
        ident = raccourcis.identifiant(
            titre, [f["id"] for f in raccourcis.lister(base, spec.name)])
        # L'image d'abord : une fabrication qui échoue ne doit pas laisser une
        # fiche sans l'aperçu qu'elle promettait.
        await _apercu_de_raccourci(c, spec, ident, job)
        fiche = {
            "id": ident, "workflow": spec.name, "titre": titre,
            "resume": str(corps.resume or "").strip(), "valeurs": valeurs,
            "job_id": job.id if job else None,
            "cree_le": datetime.now(timezone.utc).isoformat(),
            "ordre": corps.ordre if corps.ordre is not None else raccourcis.ORDRE_PAR_DEFAUT,
        }
        raccourcis.ecrire(base, spec.name, fiche)
        return _vue_raccourci(c, spec, fiche)

    @app.get("/v1/workflows/{name}/raccourcis/{ident}", tags=["workflows"])
    async def workflow_raccourci(name: str, ident: str, request: Request) -> dict:
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        fiche = raccourcis.lire(_raccourcis_base(c), spec.name, ident)
        if fiche is None:
            raise RaccourciNotFoundError(
                f"{spec.name} n'a pas de raccourci {ident!r}", workflow=spec.name, id=ident)
        return _vue_raccourci(c, spec, fiche)

    @app.put("/v1/workflows/{name}/raccourcis/{ident}", tags=["workflows"])
    async def workflow_raccourci_modifier(name: str, ident: str, corps: RaccourciIn,
                                          request: Request) -> dict:
        """Modifier un raccourci : seuls les champs PRÉSENTS dans le corps changent.

        Sauf ``valeurs``, qui remplace tout : des réglages fusionnés auraient
        gardé un champ que l'utilisateur venait justement d'effacer. L'identifiant
        ne bouge pas, même renommé — c'est l'adresse que le lanceur a en main.
        """
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        base = _raccourcis_base(c)
        fiche = raccourcis.lire(base, spec.name, ident)
        if fiche is None:
            raise RaccourciNotFoundError(
                f"{spec.name} n'a pas de raccourci {ident!r}", workflow=spec.name, id=ident)
        donnes = corps.model_fields_set
        if "titre" in donnes:
            fiche["titre"] = _titre_de_raccourci(corps.titre)
        if "resume" in donnes:
            fiche["resume"] = str(corps.resume or "").strip()
        if "ordre" in donnes and corps.ordre is not None:
            fiche["ordre"] = corps.ordre
        if "valeurs" in donnes and corps.valeurs is not None:
            fiche["valeurs"] = _valeurs_de_raccourci(c, spec, dict(corps.valeurs))
        if "job_id" in donnes and corps.job_id:
            job = _livraison_du_mode(c, spec, corps.job_id)
            await _apercu_de_raccourci(c, spec, ident, job)
            fiche["job_id"] = job.id
        raccourcis.ecrire(base, spec.name, fiche)
        return _vue_raccourci(c, spec, fiche)

    @app.delete("/v1/workflows/{name}/raccourcis/{ident}", tags=["workflows"])
    async def workflow_raccourci_retirer(name: str, ident: str, request: Request) -> dict:
        """Retirer un raccourci — sa fiche et son aperçu. Retirer ce qui n'existe
        pas n'est pas une erreur : c'est déjà l'état demandé."""
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        return {"workflow": spec.name, "id": ident,
                "removed": raccourcis.retirer(_raccourcis_base(c), spec.name, ident)}

    @app.get("/v1/workflows/{name}/raccourcis/{ident}/apercu", tags=["workflows"])
    async def workflow_raccourci_apercu(name: str, ident: str,
                                        request: Request) -> FileResponse:
        """L'aperçu animé d'un raccourci : ce que CES réglages ont donné."""
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        trouve = raccourcis.apercu_fichier(_raccourcis_base(c), spec.name, ident)
        if trouve is None:
            raise RaccourciNotFoundError(
                f"le raccourci {ident!r} de {spec.name} n'a pas d'aperçu : en donner un "
                f"avec PUT /v1/workflows/{spec.name}/raccourcis/{ident} {{\"job_id\": …}}",
                workflow=spec.name, id=ident)
        fichier, mime = trouve
        return FileResponse(fichier, media_type=mime, headers={"Cache-Control": "no-cache"})

    @app.get("/v1/workflows/{name}", tags=["workflows"])
    async def get_workflow(name: str, request: Request) -> dict:
        cat = request.app.state.container.catalog
        return _spec_dict(cat.get_spec(name))  # IntentValidationError -> 400 problem

    @app.get("/v1/workflows/{name}/readiness", tags=["hermes"])
    async def workflow_readiness(name: str, request: Request) -> dict:
        """Can this workflow run HERE? Answered from what actually happened,
        so the UI does not offer a workflow that is known to fail."""
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        if spec.est_chaine:
            return _readiness_chaine(c, c.catalog.chaine(spec))
        # Same reading of the memory as the reconciler: a problem the host has
        # since overcome no longer stands, and one met on another configuration
        # is named as such instead of condemning the workflow as a whole.
        avertissements = [
            {"problem": w.get("problem"), "detail": (w.get("detail") or "")[:300],
             "config": w.get("config"), "last_seen": w.get("ts")}
            for w in c.registry.known_warnings(c.settings.host_id, spec.name)
        ]
        # Les moments où un souvenir a cessé d'être vrai, le dernier d'abord :
        # « réparé depuis quand, et par quoi » est une date, pas une moyenne.
        revisions = [
            {"problem": r.get("problem"), "config": r.get("config"),
             "seen": r.get("ts"), "revised_at": r.get("revised_ts"),
             "revised_by": r.get("revised_by")}
            for r in c.registry.revisions(c.settings.host_id, spec.name, limit=5)
        ]
        blocking = c.registry.blocking_problems(c.settings.host_id, spec.name)
        if not blocking:
            # Un problème sans frais à redécouvrir n'empêche pas d'essayer : le
            # moteur tranchera lui-même, en quelques millisecondes s'il refuse.
            return {"workflow": spec.name, "runnable": True, "warnings": avertissements,
                    "revisions": revisions}
        first = blocking[0]
        # `blocking` vient du plus récent au plus ancien : ce qui compte pour
        # décider, c'est la DERNIÈRE fois que c'est arrivé. Annoncer la première
        # sous le nom « since » datait le souvenir d'une vieille tentative et
        # laissait croire que rien n'avait été retenté depuis.
        meme = [b for b in blocking if b.get("problem") == first.get("problem")]
        return {
            "workflow": spec.name,
            "runnable": False,
            "problem": first.get("problem"),
            "detail": (first.get("detail") or "")[:300],
            "config": first.get("config"),
            "last_seen": first.get("ts"),
            "first_seen": meme[-1].get("ts"),
            "occurrences": len(meme),
            "warnings": avertissements,
            "revisions": revisions,
            "retry_hint": "POST /v1/render?force=true pour essayer malgré ce souvenir",
            # What it needs, so the gap is actionable rather than mysterious.
            "dependencies": spec.dependencies,
        }

    @app.post("/v1/estimate", tags=["hermes"])
    async def estimate(intent_in: IntentIn, request: Request) -> dict:
        """How long THIS intent will take here — resolution, frames and steps
        included. Fitted on measured runs; silent when nothing was measured."""
        c = request.app.state.container
        _extras_admis(c, intent_in)
        spec = c.catalog.get_spec(intent_in.workflow)
        if spec.est_chaine:
            chaine = c.catalog.chaine(spec)
            corps = await request.json()
            valeurs, _ecartes = _valeurs_chaine(
                c, chaine, corps if isinstance(corps, dict) else {})
            return _estimation_chaine(c, chaine, valeurs)
        plan = c.orchestrator.build_plan(intent_in.to_domain())
        values = _with_work(c, plan)
        return {
            "workflow": plan.workflow,
            "effective": values,
            "work": plan.work,
            "ignored": list(plan.ignored),
            "estimate": c.registry.estimate_duration(
                c.settings.host_id, plan.workflow, plan.work, plan.config,
                work_model=plan.work_model),
        }

    @app.get("/v1/workflows/{name}/io", tags=["workflows"])
    async def workflow_io(name: str, request: Request) -> dict:
        """What this workflow expects and delivers — read from ComfyUI's own
        node schemas, not guessed from parameter names."""
        from ..adapter.workflow_io import describe_io
        c = request.app.state.container
        spec = c.catalog.get_spec(name)
        probe = c.comfyui.probe()
        if spec.est_chaine:
            # Une chaîne se décrit MOTEUR ÉTEINT : son contrat est écrit, pas
            # découvert. Le taire quand le moteur dort aurait rendu un
            # formulaire vide pour une chaîne parfaitement lançable.
            chaine = c.catalog.chaine(spec)
            return {"name": spec.name, "engine": probe, "described": True, "chaine": True,
                    "resume": chaine.resume,
                    "intent_inputs": _intent_inputs_chaine(c, chaine, spec.aides),
                    "media_inputs": _habiller_medias(c, _media_inputs_chaine(chaine),
                                                     spec.aides),
                    "media_inputs_unbound": [],
                    "etapes": _etapes_annoncees(chaine, _technique_par_defaut(c, chaine)),
                    "techniques": _techniques_vues(c, chaine),
                    "inputs": [], "outputs": []}
        # Les liaisons d'un montage visent des rôles (« $commun.style ») : c'est
        # le dépliage qui leur donne un numéro de nœud, et donc des options.
        graph, liaisons = c.catalog.monter(spec)
        if not probe.get("available"):
            return {"name": spec.name, "engine": probe, "described": False}
        object_info = await run_in_threadpool(c.comfyui.get_object_info)
        io = describe_io(graph, object_info, spec.titles)
        from ..adapter import media_inputs
        from ..adapter.workflow_io import intent_inputs
        return {"name": spec.name, "engine": probe, "described": True, "chaine": False,
                # The contract to program against: field name, bounds, current
                # value — the join done once, server side. Habillé de ce qu'il
                # faut pour l'afficher : libellés des valeurs, unité.
                "intent_inputs": [
                    _habiller(c, e, aides=spec.aides)
                    for e in intent_inputs(io["inputs"], liaisons, spec.kind,
                                           spec.defaults, spec.pilote, spec.exemple)],
                # Les pièces jointes pilotables…
                "media_inputs": _habiller_medias(
                    c, media_inputs.describe(liaisons, spec.titles, spec.carried, graph),
                    spec.aides),
                # …et celles que le MOTEUR déclare téléversables sans que rien
                # ici ne les pilote (un custom node absent de la table). Vide
                # en temps normal ; ce qui manque doit se voir.
                "media_inputs_unbound": media_inputs.unbound(graph, liaisons, object_info),
                **io}

    @app.get("/v1/comfyui/workflows", tags=["workflows"])
    async def list_comfyui_workflows(request: Request) -> dict:
        """Workflows saved in ComfyUI, cross-referenced with what's extracted.

        Status per entry: not-extracted / extracted / update-available (the
        ComfyUI source changed since extraction). This is the management view.
        """
        c = request.app.state.container
        from ..adapter.freshness import source_state
        from ..browser.headless import is_available as _hb
        hb_ok, hb_reason = _hb()
        headless = {"available": hb_ok, "reason": hb_reason}
        probe = c.comfyui.probe()
        if not probe.get("available"):
            return {"comfyui": probe, "headless": headless, "saved": []}
        # map ComfyUI source name -> extracted spec
        by_source = {s.source: s for s in (c.catalog.get_spec(n) for n in c.catalog.names()) if s.source}
        saved = []
        for name in c.comfyui.list_saved_workflows():
            spec = by_source.get(name)
            entry = {"name": name, "status": "not-extracted"}
            if spec is not None:
                entry["extracted_as"] = spec.name
                entry["status"] = "extracted"
                state = source_state(c.comfyui, spec)
                if not state["fresh"]:
                    entry["status"] = "update-available"
                    entry["reason"] = state["reason"]
            saved.append(entry)
        return {"comfyui": probe, "headless": headless, "saved": saved}

    @app.post("/v1/comfyui/workflows/{name}/extract", status_code=201, tags=["workflows"])
    async def extract_comfyui_workflow(name: str, request: Request) -> dict:
        """ONE-CLICK extraction: read the saved ComfyUI workflow, convert it via
        ComfyUI's own graphToPrompt in a temporary headless browser, auto-bind,
        record provenance, register. No manual Export (API)."""
        from ..adapter.comfyui_client import source_hash as _hash
        from ..adapter.extractor import extract_api_graph
        from ..browser.headless import is_available as _hb
        c = request.app.state.container
        ok, reason = _hb()
        if not ok:
            raise DependencyUnavailableError(f"headless browser unavailable: {reason}")
        graph = await run_in_threadpool(
            extract_api_graph, c.settings.comfyui_base_url, name, c.settings.browser_timeout_ms
        )
        try:
            src_hash = _hash(c.comfyui.get_saved_workflow(name))
        except Exception:
            src_hash = None
        reg_name = name[:-5] if name.endswith(".json") else name
        titles = {}
        try:
            from ..adapter.labels import titles_from_ui_workflow
            titles = titles_from_ui_workflow(
                await run_in_threadpool(c.comfyui.get_saved_workflow, name))
        except Exception:
            pass
        return await _ingest(c, reg_name, graph, name, src_hash, titles)

    # Déclarée AVANT "/v1/jobs/{job_id}" : sinon la liste est lue comme un
    # identifiant de job et répond 404.
    @app.get("/v1/jobs", tags=["render"])
    async def list_jobs(request: Request, limit: int = 50, enfants: bool = False) -> dict:
        """Les runs, du plus récent au plus ancien — mémoire ET persistés.

        Un appelant qui veut montrer ses livraisons n'a pas à tenir la liste
        des identifiants qu'il a lancés : elle vit ici, et survit à un
        redémarrage de la passerelle.

        Les sous-jobs d'une chaîne n'en font pas partie par défaut : ils sont
        déjà dans les ``etapes`` de leur parent, et les lister à côté montrait
        trois cartes pour une seule création. ``enfants=1`` les rend.
        """
        c = request.app.state.container
        jobs = [j for j in c.store.list(limit * 4 if not enfants else limit)
                if enfants or not j.parent][:limit]
        return {"jobs": [_with_engine_state(c, JobOut.of(j).model_dump()) for j in jobs]}

    @app.get("/v1/jobs/{job_id}", tags=["render"])
    async def get_job(job_id: str, request: Request) -> dict:
        c = request.app.state.container
        return _with_engine_state(c, JobOut.of(c.store.get(job_id)).model_dump())

    @app.get("/v1/jobs/{job_id}/artifacts", response_model=list[ArtifactOut], tags=["render"])
    async def get_artifacts(job_id: str, request: Request) -> list[ArtifactOut]:
        store = request.app.state.container.store
        job = store.get(job_id)
        return [ArtifactOut(**a.__dict__) for a in job.artifacts]

    @app.get("/v1/jobs/{job_id}/events", tags=["render"])
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        """Server-Sent Events: pushes the job on every change until it is terminal.

        Live progress without polling. The UI consumes this; any client can
        ``curl -N`` it. Falls back gracefully — the plain GET job endpoint still
        works for clients that don't speak SSE.
        """
        store = request.app.state.container.store
        store.get(job_id)  # validate now → 404 problem before the stream opens

        async def gen():
            last = None
            for _ in range(900):  # ~6 min hard cap at 0.4s/tick
                if await request.is_disconnected():
                    break
                job = store.get(job_id)
                snap = json.dumps(_with_engine_state(
                    request.app.state.container, JobOut.of(job).model_dump()))
                if snap != last:
                    yield f"data: {snap}\n\n"
                    last = snap
                if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED,
                                  JobStatus.CANCELLED):
                    break
                await asyncio.sleep(0.4)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/inputs/media", status_code=201, tags=["render"])
    @app.post("/v1/inputs/image", status_code=201, tags=["render"])
    async def upload_input_image(request: Request, file: UploadFile = File(...),
                                 param: str = Form("")) -> dict:
        """Hand a local file to ComfyUI so a workflow can use it as input.

        Relays ComfyUI's own ``/api/upload/image``: the engine keeps its input
        folder wherever it likes, and the name it returns is the only one a
        graph can reference. Without this the console offered an image field
        with no way to supply an image.

        ``param`` est l'entrée visée telle que le workflow l'annonce (« image »,
        « image_2 », « model3d »…). Elle ne sert qu'à une chose : savoir OÙ le
        moteur range cette catégorie — un modèle 3D va dans « 3d/ » et se cite
        « 3d/<nom> ». Omise, le fichier va à la racine du dossier d'entrée,
        comme une image."""
        from ..adapter.media_inputs import upload_subfolder
        from ..adapter.neutral import upload_image
        c = request.app.state.container
        payload = await file.read()
        if not payload:
            raise UnknownWorkflowInputError("fichier vide", field="file")
        subfolder = upload_subfolder(param or "")
        name = await run_in_threadpool(
            upload_image, c.settings.comfyui_base_url, file.filename or "image.png", payload,
            True, 60.0, subfolder)
        return {"name": name, "bytes": len(payload), "subfolder": subfolder}

    @app.get("/v1/recovered", tags=["render"])
    async def recovered(request: Request) -> dict:
        """Runs the engine finished without anybody listening.

        Collected at startup, and again when this is asked for: a run orphaned
        mid-flight used to wait for the NEXT restart to be delivered, which can
        be days. Looking at the deliverables is exactly the moment to close the
        loop; the sweep costs one history request per run still in flight."""
        late = await run_in_threadpool(_recover_inflight, request.app.state.container)
        if late:
            request.app.state.recovered = (getattr(request.app.state, "recovered", []) or []) + late
        return {"recovered": getattr(request.app.state, "recovered", []) or [],
                "still_in_flight": list((request.app.state.container.inflight.entries()
                                         if getattr(request.app.state.container, "inflight", None)
                                         else {}).keys())}

    @app.get("/v1/artifacts/origin", tags=["render"])
    async def artifact_origin(request: Request, name: str) -> dict:
        """Avec quoi ce fichier a-t-il été produit ?

        Le moteur inscrit le graphe dans ce qu'il produit (PNG, MP4, FLAC) : on
        le LIT, on ne le recopie pas. Un livrable qui ne sait pas le porter a
        reçu un fichier compagnon, lu de la même façon. À la demande, car ouvrir
        chaque fichier pour lister un dossier serait payer cher pour rien.
        """
        from ..adapter.media import SIDECAR_SUFFIX
        from ..adapter.provenance import read_embedded, summarize
        c = request.app.state.container
        out_dir = c.settings.comfy_output_dir.resolve()
        cible = (out_dir / name).resolve()
        try:
            cible.relative_to(out_dir)          # jamais hors du dossier de sortie
        except ValueError:
            raise UnknownWorkflowInputError(f"{name!r} n'est pas dans le dossier de sortie",
                                            artifact=name)
        if not cible.is_file():
            raise UnknownWorkflowInputError(f"{name!r} introuvable", artifact=name)

        # Deux moitiés, jamais la même : le fichier porte le graphe qui l'a
        # produit, le compagnon porte ce que le graphe ignore — le nom du
        # workflow appelé et ce que l'appelant avait demandé.
        graphe = await run_in_threadpool(read_embedded, cible)
        source = "embarquée" if graphe is not None else None

        annexe: dict = {}
        compagnon = cible.with_name(cible.name + SIDECAR_SUFFIX)
        if compagnon.exists():
            try:
                annexe = json.loads(compagnon.read_text(encoding="utf-8"))
            except Exception:
                annexe = {}
        if graphe is None and isinstance(annexe.get("prompt"), dict):
            graphe, source = annexe["prompt"], "fichier compagnon"

        # Une seule source : ce que le fichier porte. Le compagnon n'ajoute que
        # le nom du workflow, qu'aucun format de média ne sait garder.
        reponse = {"artifact": name, "source": source,
                   "workflow": annexe.get("workflow"),
                   "produced_at": annexe.get("produit_le")}
        if isinstance(graphe, dict):
            reponse["inputs"] = summarize(graphe)
            reponse["nodes"] = len(graphe)
        elif not annexe:
            reponse["reason"] = ("ce format ne porte pas son origine et aucun fichier "
                                 "compagnon ne l'accompagne")
        return reponse

    @app.get("/v1/artifacts", tags=["render"])
    async def list_artifacts(request: Request, limit: int = 20) -> dict:
        """The media actually produced, newest first, with WHERE they are.

        The output folder is the single source: whatever a pipeline wrote there
        is what exists, whether this bridge process created it or not.
        """
        from ..adapter.media import DELIVERABLE_EXT, artifact_url, is_working_file, media_kind
        c = request.app.state.container
        out_dir = c.settings.comfy_output_dir.resolve()
        files = [f for f in out_dir.rglob("*")
                 if f.is_file() and f.suffix.lower() in DELIVERABLE_EXT
                 and not is_working_file(f)]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        items = []
        for f in files[: max(1, min(limit, 200))]:
            st = f.stat()
            items.append({
                "name": f.name,
                "kind": media_kind(f),
                "path": str(f),                       # absolute, openable on the host
                "url": artifact_url(out_dir, f),      # served by this bridge
                "bytes": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            })
        return {"output_dir": str(out_dir), "count": len(files), "artifacts": items}

    @app.get("/v1/engines", tags=["backend"])
    async def list_engines(request: Request) -> dict:
        """Declared engines (where ComfyUI runs) and which one is active."""
        from ..adapter.engines import is_alive, load_engines
        c = request.app.state.container
        # Le fichier livré ET la surcharge du poste, comme au démarrage
        # (container.py) : lu sans le dossier de données, ce point d'entrée
        # n'annonçait que le profil livré « attach » alors que le moteur actif
        # était « local » (mesuré : default attach, active local) — on ne
        # pouvait pas y lire qui gère le moteur.
        default, profiles = load_engines(c.settings.engines_file,
                                         data_dir=c.settings.hermes_db.parent)
        return {
            "default": default,
            "active": c.engine.name,
            "state": c.engine_state,
            "engines": {
                n: {"base_url": p.base_url, "manage": p.manage,
                    "description": p.description, "alive": is_alive(p.base_url, 1.5)}
                for n, p in profiles.items()
            },
        }

    @app.post("/v1/engine/start", tags=["backend"])
    async def engine_start(request: Request) -> dict:
        """Bring the engine up if it is not there.

        Same rule as at startup: attach to whatever already answers, and launch
        one only when the active profile manages it. Never a second instance on
        a port that already replies."""
        from ..adapter.engines import ensure_engine
        c = request.app.state.container
        state = await run_in_threadpool(
            ensure_engine, c.engine, 240.0, c.settings.hermes_db.parent)
        c.engine_state.clear()
        c.engine_state.update(state)
        return state

    @app.post("/v1/engine/free", tags=["backend"])
    async def engine_free(request: Request) -> dict:
        """Relay ComfyUI's own /api/free: release VRAM without restarting."""
        c = request.app.state.container
        return await run_in_threadpool(c.comfyui.free)

    @app.post("/v1/engine/interrupt", tags=["backend"])
    async def engine_interrupt(request: Request) -> dict:
        """Relay ComfyUI's own /api/interrupt: cancel the running job."""
        c = request.app.state.container
        return await run_in_threadpool(c.comfyui.interrupt)

    @app.post("/v1/jobs/{job_id}/cancel", tags=["render"])
    async def cancel_job(job_id: str, request: Request) -> dict:
        """Stop THIS run, by the means that fits where it is.

        Pending in the queue -> ComfyUI drops that prompt (the others keep their
        place). Already running -> ComfyUI's own interrupt. Both are the engine's
        official operations; we keep no queue of our own to cancel.

        Une CHAÎNE n'a pas de run à elle : on note l'arrêt sur le parent — les
        étapes restantes ne seront pas faites — et on arrête le sous-job en
        cours par ces mêmes moyens."""
        from ..adapter.chaines import arreter_au_moteur
        c = request.app.state.container
        job = c.store.get(job_id)                     # raises if unknown
        if job.etapes:
            c.store.request_cancel(job_id)
            courante = next((e for e in job.etapes
                             if e.get("statut") == "running" and e.get("job_id")), None)
            if courante is None:
                c.store.append_log(job_id, "arrêt demandé — la chaîne s'arrêtera à "
                                           "la fin de l'étape en cours")
                return {"cancelled": True, "how": "chaine", "etape": None}
            out = await run_in_threadpool(arreter_au_moteur, c, courante["job_id"])
            c.store.append_log(job_id, f"arrêt demandé — étape {courante['id']} "
                                       f"({out.get('how') or out.get('reason')})")
            return {"cancelled": True, "how": "chaine", "etape": courante["id"],
                    "sous_job": out}
        return await run_in_threadpool(arreter_au_moteur, c, job_id)

    @app.post("/v1/engine/restart", tags=["backend"])
    async def engine_restart(request: Request, force: bool = False) -> dict:
        """Bring the engine back, cheapest official means first.

        1. ``/api/free`` — ComfyUI's own memory release; often enough, no restart.
        2. managed profile → stop the process we started, relaunch it detached.
        3. attach profile → ask ComfyUI-Manager to reboot; we never kill a server
           we do not own (that is the user's Desktop).
        """
        from ..adapter.engines import ensure_engine, is_alive, stop_engine
        c = request.app.state.container
        steps: list[dict] = []

        alive = await run_in_threadpool(is_alive, c.engine.base_url, 2.0)
        if alive and not force:
            try:
                steps.append({"step": "free", **await run_in_threadpool(c.comfyui.free)})
                return {"engine": c.engine.name, "outcome": "freed", "steps": steps,
                        "note": "mémoire libérée par ComfyUI, sans redémarrage"}
            except Exception as e:
                steps.append({"step": "free", "ok": False, "reason": str(e)[:200]})

        if c.engine.manage:
            stopped = await run_in_threadpool(stop_engine, c.engine, c.settings.hermes_db.parent)
            steps.append({"step": "stop", **stopped})
            if stopped.get("stopped"):
                state = await run_in_threadpool(
                    ensure_engine, c.engine, 240.0, c.settings.hermes_db.parent)
                steps.append({"step": "start", **state})
                return {"engine": c.engine.name, "outcome": state.get("state"), "steps": steps}
            if not alive:
                state = await run_in_threadpool(
                    ensure_engine, c.engine, 240.0, c.settings.hermes_db.parent)
                steps.append({"step": "start", **state})
                return {"engine": c.engine.name, "outcome": state.get("state"), "steps": steps}
            # Alive, but not started by us (no PID of ours): killing a server we
            # do not own is not on. ComfyUI-Manager reboots it through its own
            # API — that is the official way and it works whoever started it.
            reboot = await run_in_threadpool(c.comfyui.manager_reboot)
            steps.append({"step": "manager-reboot", **reboot})
            if reboot.get("ok"):
                return {"engine": c.engine.name, "outcome": "delegated", "steps": steps,
                        "note": "ce serveur n'a pas été lancé par la passerelle : "
                                "redémarrage demandé à ComfyUI-Manager"}
            # Neither ours to stop nor reachable through the Manager API: say so
            # plainly instead of returning a state that sounds like a success.
            return {"engine": c.engine.name, "outcome": "impossible", "steps": steps,
                    "note": "ce serveur tourne mais n'a pas été lancé par la passerelle, "
                            "et ComfyUI-Manager n'expose pas son redémarrage ici : "
                            "redémarre-le depuis ComfyUI Desktop. "
                            "La mémoire, elle, a pu être libérée (/v1/engine/free)."}

        steps.append({"step": "manager-reboot", **await run_in_threadpool(c.comfyui.manager_reboot)})
        return {"engine": c.engine.name, "outcome": "delegated", "steps": steps,
                "note": "profil 'attach' : le serveur ne nous appartient pas ; "
                        "s'il ne revient pas, redémarre-le toi-même (ComfyUI Desktop)"}

    @app.post("/v1/engine/queue/clear", tags=["backend"])
    async def engine_queue_clear(request: Request) -> dict:
        """Relay ComfyUI's own queue clear: drop everything still pending."""
        c = request.app.state.container
        return await run_in_threadpool(c.comfyui.clear_queue)

    @app.get("/v1/engine/queue", tags=["backend"])
    async def engine_queue(request: Request) -> dict:
        """ComfyUI's own queue, relayed. Runs submitted here appear in it and can
        be watched or cancelled in ComfyUI itself — we keep no second queue."""
        c = request.app.state.container
        probe = c.comfyui.probe()
        if not probe.get("available"):
            return {"engine": probe, "running": [], "pending": []}
        q = c.comfyui.queue()
        # ComfyUI executes ONE prompt at a time; the rest wait. Say it, so the
        # concurrency model is never a guess.
        return {"engine": probe, "concurrency": 1,
                "note": "le moteur exécute un run à la fois, les autres attendent",
                **q}

    @app.get("/v1/hermes/runs", tags=["hermes"])
    async def hermes_runs(request: Request, limit: int = 20) -> dict:
        """What Hermes actually knows: real past runs on this host, this scope."""
        c = request.app.state.container
        return {"host": c.settings.host_id, "scope": c.settings.hermes_scope,
                "runs": c.registry.recent(c.settings.host_id, limit)}

    @app.get("/v1/hermes/problems", tags=["hermes"])
    async def hermes_problems(request: Request, workflow: str, config: str | None = None) -> dict:
        """Known problems for a workflow (optionally one config)."""
        c = request.app.state.container
        return {"workflow": workflow, "config": config,
                "problems": c.registry.problems_for(c.settings.host_id, workflow, config),
                # Ce qui a été levé, quand, et par quoi. Sans cela, un souvenir
                # révisé disparaissait sans laisser trace de sa correction.
                "revised": c.registry.revisions(c.settings.host_id, workflow)}

    # Serve rendered artifacts (and dry-run manifests) so the delivery is
    # actually openable in a browser — Artifact.url points here.
    import mimetypes
    mimetypes.add_type("image/svg+xml", ".svg")  # ensure <img> renders SVG placeholders
    out_dir = eff.comfy_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=str(out_dir), check_dir=False), name="artifacts")

    # Console (vanilla HTML/JS, no build, no CDN) served at /ui — no-store so a
    # stale cached page never hides new features from the user.
    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def ui_page() -> FileResponse:
        return FileResponse(
            static_dir / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    return app


app = create_app()

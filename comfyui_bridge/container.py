"""Composition root — wires concrete implementers into the core once.

Both the REST API and the operator CLI build a ``Container`` and drive the same
``Orchestrator``. That symmetry is the proof of decoupling: two unrelated entry
points, one engine-agnostic core, zero duplicated business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .adapter.catalog import WorkflowCatalog, load_catalog
from .adapter.comfy_cli import ComfyCliBackend
from .adapter.comfy_http import ComfyUIHttpBackend
from .adapter.comfyui_client import ComfyUIClient
from .adapter.engines import EngineProfile, ensure_engine, load_engines
from .adapter.inflight import InflightLog
from .adapter.sidecar import WithOrigin
from .config import Settings
from .core.file_des_demandes import FileDesDemandes
from .core.jobs import JobStatus, JobStore
from .core.orchestrator import Orchestrator
from .core.ports import RenderBackend
from .hermes.reconciler import HermesReconciler
from .hermes.registry import ProblemRegistry


@dataclass
class Container:
    settings: Settings
    registry: ProblemRegistry
    engine: EngineProfile
    engine_state: dict
    reconciler: HermesReconciler
    catalog: WorkflowCatalog
    comfyui: ComfyUIClient
    backend: RenderBackend            # le concret, enveloppé par WithOrigin
    store: JobStore
    orchestrator: Orchestrator
    inflight: InflightLog
    # Une seule demande de l'utilisateur à la fois : les autres attendent leur
    # tour ici, dans l'ordre (voir core.file_des_demandes).
    file: FileDesDemandes
    # Les limites MATÉRIELLES déclarées de ce poste (ou None si rien n'est
    # déclaré) : c'est sur elles que le découpage d'un rendu se calcule.
    materiel: dict | None = None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    registry = ProblemRegistry(settings.hermes_db, scope=settings.hermes_scope)
    data_dir = settings.hermes_db.parent
    # Le matériel du poste, lu une fois : un fichier qui ne tient pas est refusé
    # ici, au démarrage, et non au premier rendu long.
    from .adapter import materiel as _materiel
    limites = _materiel.lire(data_dir)
    catalog = load_catalog(settings.catalog_file, settings.workflows_dir, data_dir=data_dir)

    # Resolve WHERE ComfyUI runs before anything talks to it.
    default_engine, engines = load_engines(settings.engines_file, data_dir=data_dir)
    engine = engines[settings.engine or default_engine]
    if settings.comfyui_base_url:            # explicit override stays king
        engine = EngineProfile(engine.name, settings.comfyui_base_url, manage=False,
                               description="override COMFYUI_BASE_URL")
    engine_state = ensure_engine(engine, lock_dir=settings.hermes_db.parent) if settings.comfy_backend == "http" else {
        "engine": engine.name, "state": "unused", "base_url": engine.base_url}
    settings = replace(settings, comfyui_base_url=engine.base_url)
    comfyui = ComfyUIClient(settings.comfyui_base_url, settings.comfyui_request_timeout_s)

    reconciler = HermesReconciler(registry, host=settings.host_id, mode=settings.hermes_mode)
    inflight = InflightLog(settings.hermes_db.parent / "inflight.json")
    if settings.comfy_backend == "http":
        backend: RenderBackend = ComfyUIHttpBackend(
            settings, catalog, inflight=inflight)
    else:
        backend = ComfyCliBackend(settings, catalog)  # honours dry_run (manifest vs subprocess)

    # Un livrable part avec son origine, quel que soit le backend qui l'a
    # produit : la règle est posée ici, une fois, sur le port.
    backend = WithOrigin(backend)
    # Les runs sont écrits à côté de la mémoire d'Hermes : un redémarrage
    # laissait sinon des livrables dans le dossier de sortie dont plus rien ne
    # disait ce qui les avait produits, ni avec quelle demande.
    store = JobStore(persist_dir=data_dir / "jobs")
    from .adapter import essais as _essais
    conteneur: list = []          # rempli juste avant le retour : les crochets le lisent alors
    file = FileDesDemandes(dire=store.append_log,
                           sur_erreur=lambda job_id, exc: _demande_cassee(store, job_id, exc),
                           ceder=lambda job_id: _essais.ceder_le_moteur(conteneur[0], job_id),
                           sur_cession=lambda job_id, fois: _essais.sur_cession(conteneur[0], job_id, fois))
    orchestrator = Orchestrator(
        backend=backend,
        reconciler=reconciler,
        journal=registry,
        store=store,
        host_id=settings.host_id,
        registry=catalog,
    )
    budget, provenance = _materiel.budget_et_provenance(limites, settings.tranche_octets)
    print(f"[comfyui-bridge] {_materiel.dire(budget, provenance)}")
    conteneur.append(Container(
        settings=settings,
        registry=registry,
        engine=engine,
        engine_state=engine_state,
        reconciler=reconciler,
        catalog=catalog,
        comfyui=comfyui,
        backend=backend,
        store=store,
        orchestrator=orchestrator,
        inflight=inflight,
        file=file,
        materiel=limites,
    ))
    return conteneur[0]


def _demande_cassee(store: JobStore, job_id: str, exc: BaseException) -> None:
    """Une demande dont l'exécution a levé hors de tout rattrapage : le job le
    dit et se ferme, pour que la file passe à la suivante sans laisser un
    « en cours » éternel."""
    try:
        job = store.get(job_id)
        store.append_log(job_id, f"la demande a cassé hors de tout rattrapage : {exc!r}")
        if job.status in (JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING):
            store.mark_failed(job_id, {
                "type": "https://cortex/problems/demande-cassee",
                "title": "Demande interrompue",
                "status": 500,
                "detail": f"{type(exc).__name__}: {exc}",
                "problem_kind": "unknown",
            })
    except Exception:                                    # noqa: BLE001
        pass

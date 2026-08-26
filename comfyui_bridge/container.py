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
from .config import Settings
from .core.jobs import JobStore
from .core.orchestrator import Orchestrator
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
    backend: ComfyCliBackend | ComfyUIHttpBackend
    store: JobStore
    orchestrator: Orchestrator
    inflight: InflightLog


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings.from_env()
    registry = ProblemRegistry(settings.hermes_db, scope=settings.hermes_scope)
    catalog = load_catalog(settings.catalog_file, settings.workflows_dir)

    # Resolve WHERE ComfyUI runs before anything talks to it.
    default_engine, engines = load_engines(settings.engines_file)
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
        backend: ComfyCliBackend | ComfyUIHttpBackend = ComfyUIHttpBackend(
            settings, catalog, inflight=inflight)
    else:
        backend = ComfyCliBackend(settings, catalog)  # honours dry_run (manifest vs subprocess)
    store = JobStore()
    orchestrator = Orchestrator(
        backend=backend,
        reconciler=reconciler,
        journal=registry,
        store=store,
        host_id=settings.host_id,
        registry=catalog,
    )
    return Container(
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
    )

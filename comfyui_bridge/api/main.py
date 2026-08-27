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

from fastapi import BackgroundTasks, FastAPI, File, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import Settings
from ..container import build_container
from ..core.errors import DependencyUnavailableError, UnknownWorkflowInputError
from ..core.jobs import JobStatus
from ..core.intention import intent_fields
from ..core.orchestrator import Orchestrator, derivable_params
from .problems import install_problem_handlers
from .schemas import ArtifactOut, IntentIn, JobOut, WorkflowImportIn

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
    from ..adapter.workflow_io import describe_io
    probe = container.comfyui.probe()
    if not probe.get("available"):
        return {"described": False, "reason": probe.get("reason", "moteur injoignable"),
                "accepts": sorted(spec.bindings)}
    graph = container.catalog.load_template(spec)
    io = describe_io(graph, container.comfyui.get_object_info(), spec.titles)
    return {
        "described": True,
        "accepts": sorted(spec.bindings),          # semantic inputs we can drive
        "intent_fields": sorted(set(intent_fields(spec.bindings))
                                | set(derivable_params(spec.kind, spec.bindings))),
        "derived": derivable_params(spec.kind, spec.bindings),  # drivable via conversion
        "settable_inputs": len(io["inputs"]),      # everything the workflow exposes
        "outputs": io["outputs"],                  # what it delivers
        "carried": spec.carried,                   # media it already holds
        "kind": spec.kind,
    }


def _spec_dict(spec) -> dict:
    return {
        "name": spec.name,
        "kind": spec.kind,
        "accepts": sorted(spec.bindings),
        "intent_fields": sorted(set(intent_fields(spec.bindings))
                                | set(derivable_params(spec.kind, spec.bindings))),
        "derived": derivable_params(spec.kind, spec.bindings),
        "defaults": spec.defaults,
        "limits": spec.limits,
        "source": spec.source,
        "source_hash": spec.source_hash,
        "dependencies": spec.dependencies,
        "workflow_source": str(spec.workflow_path),
        "bindings": {k: {"node": b.node, "input": b.input} for k, b in spec.bindings.items()},
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
        }

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

    @app.post("/v1/render", status_code=202, response_model=JobOut, tags=["render"])
    async def render(
        intent_in: IntentIn,
        request: Request,
        response: Response,
        background: BackgroundTasks,
    ) -> JobOut:
        orch = get_orchestrator(request)
        # accept() plans + reconciles synchronously; a strict rejection raises
        # HardwareReconciliationError here and leaves as a 422 problem+json.
        job, plan = orch.accept(intent_in.to_domain())
        _with_work(request.app.state.container, plan)
        background.add_task(orch.execute, job.id, plan)
        response.headers["Location"] = f"/v1/jobs/{job.id}"
        return JobOut.of(job)

    @app.post("/v1/preview", tags=["render"])
    async def preview(intent_in: IntentIn, request: Request) -> dict:
        # Same plan + Hermes reconciliation as /v1/render (can 422), but stops
        # before execution and returns the injected ComfyUI graph instead.
        # Looking at a graph executes nothing: it must never be refused, even
        # for a workflow a past problem stands against. Hermes' opinion is
        # reported alongside instead of blocking the view.
        c = request.app.state.container
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
        c = request.app.state.container
        cat = c.catalog
        items = {}
        for name in cat.names():
            spec = cat.get_spec(name)
            items[name] = {
                "kind": spec.kind,
                # What this workflow can actually receive. A field it does not
                # bind goes nowhere: offering it would be a lie.
                "accepts": sorted(spec.bindings),
                # The names to actually put in a render request — the contract a
                # caller programs against, UI or not.
                "intent_fields": sorted(set(intent_fields(spec.bindings))
                                        | set(derivable_params(spec.kind, spec.bindings))),
                # Same memory as /readiness and as the reconciler: a workflow
                # known to fail here must not be the one the console opens on.
                "runnable": not c.registry.blocking_problems(c.settings.host_id, name),
                # Drivable without a node of its own, by conversion (seconds -> frames).
                "derived": derivable_params(spec.kind, spec.bindings),
                # Media already inside the workflow: used as-is if not replaced.
                "carried": spec.carried,
                # …and those a neutral element can stand in for.
                "neutral_for": list(spec.profile.neutral_for),
                "defaults": spec.defaults,
                "limits": spec.limits,
                "dependencies": spec.dependencies,
                "source": spec.source,
                "workflow_source": str(spec.workflow_path),
                "bindings": {k: {"node": b.node, "input": b.input} for k, b in spec.bindings.items()},
            }
        from ..core.intention import ConstraintOp, MediaKind
        return {
            "default": cat.default_name(),
            "workflows": items,
            # Vocabulary comes from the domain enums — the UI must not keep a copy.
            "vocabulary": {
                "kinds": [k.value for k in MediaKind],
                "constraint_ops": [o.value for o in ConstraintOp],
                # Fields a caller may leave unset to inherit the workflow default.
                "params": ["width", "height", "batch", "steps", "cfg", "fps",
                           "duration_s", "seed", "negative_prompt", "image"],
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
        src_hash = None
        if body.source:
            try:
                src_hash = _hash(c.comfyui.get_saved_workflow(body.source))
            except Exception:
                src_hash = None  # source unreachable → import without update tracking
        titles = {}
        if body.source:
            try:
                from ..adapter.labels import titles_from_ui_workflow
                titles = titles_from_ui_workflow(
                    await run_in_threadpool(c.comfyui.get_saved_workflow, body.source))
            except Exception:
                pass
        spec = c.catalog.register(body.name, body.workflow, source=body.source,
                                  source_hash=src_hash, titles=titles)
        analysis = await run_in_threadpool(_analyse_workflow, c, spec)
        return {**_spec_dict(spec), "analysis": analysis}

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
        # Same reading of the memory as the reconciler: a problem the host has
        # since overcome no longer stands, and one met on another configuration
        # is named as such instead of condemning the workflow as a whole.
        blocking = c.registry.blocking_problems(c.settings.host_id, spec.name)
        if not blocking:
            return {"workflow": spec.name, "runnable": True}
        first = blocking[0]
        return {
            "workflow": spec.name,
            "runnable": False,
            "problem": first.get("problem"),
            "detail": (first.get("detail") or "")[:300],
            "config": first.get("config"),
            "since": first.get("ts"),
            # What it needs, so the gap is actionable rather than mysterious.
            "dependencies": spec.dependencies,
        }

    @app.post("/v1/estimate", tags=["hermes"])
    async def estimate(intent_in: IntentIn, request: Request) -> dict:
        """How long THIS intent will take here — resolution, frames and steps
        included. Fitted on measured runs; silent when nothing was measured."""
        c = request.app.state.container
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
        graph = c.catalog.load_template(spec)
        probe = c.comfyui.probe()
        if not probe.get("available"):
            return {"name": spec.name, "engine": probe, "described": False}
        io = describe_io(graph, await run_in_threadpool(c.comfyui.get_object_info), spec.titles)
        from ..adapter.workflow_io import intent_inputs
        return {"name": spec.name, "engine": probe, "described": True,
                # The contract to program against: field name, bounds, current
                # value — the join done once, server side.
                "intent_inputs": intent_inputs(io["inputs"], spec.bindings, spec.kind),
                **io}

    @app.get("/v1/comfyui/workflows", tags=["workflows"])
    async def list_comfyui_workflows(request: Request) -> dict:
        """Workflows saved in ComfyUI, cross-referenced with what's extracted.

        Status per entry: not-extracted / extracted / update-available (the
        ComfyUI source changed since extraction). This is the management view.
        """
        c = request.app.state.container
        from ..adapter.comfyui_client import source_hash as _hash
        from ..adapter.labels import titles_from_ui_workflow
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
                try:
                    saved_doc = c.comfyui.get_saved_workflow(name)
                    if spec.source_hash and _hash(saved_doc) != spec.source_hash:
                        entry["status"] = "update-available"
                        entry["reason"] = "le workflow a changé dans ComfyUI"
                    elif titles_from_ui_workflow(saved_doc) and not spec.titles:
                        # Extracted before the author's node titles were read:
                        # the analysis is poorer than the source allows, so the
                        # form under-exposes fields. Say so instead of showing
                        # a reassuring "extrait".
                        entry["status"] = "update-available"
                        entry["reason"] = "analyse incomplète — les noms de nœuds de l'auteur n'ont pas été lus"
                except Exception:
                    pass
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
            titles = titles_from_ui_workflow(await run_in_threadpool(c.comfyui.get_saved_workflow, name))
        except Exception:
            pass
        spec = c.catalog.register(reg_name, graph, source=name, source_hash=src_hash, titles=titles)
        # A new workflow (or an updated one) is ANALYSED here, using ComfyUI's own
        # node schemas. The UI and the API then derive from that analysis.
        analysis = await run_in_threadpool(_analyse_workflow, c, spec)
        return {**_spec_dict(spec), "analysis": analysis}

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
    async def upload_input_image(request: Request, file: UploadFile = File(...)) -> dict:
        """Hand a local image to ComfyUI so a workflow can use it as input.

        Relays ComfyUI's own ``/api/upload/image``: the engine keeps its input
        folder wherever it likes, and the name it returns is the only one a
        graph can reference. Without this the console offered an image field
        with no way to supply an image."""
        from ..adapter.neutral import upload_image
        c = request.app.state.container
        payload = await file.read()
        if not payload:
            raise UnknownWorkflowInputError("fichier vide", field="file")
        name = await run_in_threadpool(
            upload_image, c.settings.comfyui_base_url, file.filename or "image.png", payload)
        return {"name": name, "bytes": len(payload)}

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

    @app.get("/v1/artifacts", tags=["render"])
    async def list_artifacts(request: Request, limit: int = 20) -> dict:
        """The media actually produced, newest first, with WHERE they are.

        The output folder is the single source: whatever a pipeline wrote there
        is what exists, whether this bridge process created it or not.
        """
        from ..adapter.media import MEDIA_EXT, artifact_url, media_kind
        c = request.app.state.container
        out_dir = c.settings.comfy_output_dir.resolve()
        files = [f for f in out_dir.rglob("*")
                 if f.is_file() and f.suffix.lower() in MEDIA_EXT]
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
        default, profiles = load_engines(c.settings.engines_file)
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
        official operations; we keep no queue of our own to cancel."""
        c = request.app.state.container
        job = c.store.get(job_id)                     # raises if unknown
        ref = getattr(job, "engine_ref", None)
        if not ref:
            return {"cancelled": False, "reason": "ce job n'a pas encore été pris par le moteur"}
        q = await run_in_threadpool(c.comfyui.queue)
        c.store.request_cancel(job_id)
        if ref in q.get("pending", []):
            out = await run_in_threadpool(c.comfyui.cancel, [ref])
            c.store.append_log(job_id, "annulé dans la file du moteur (il n'avait pas commencé)")
            return {"cancelled": True, "how": "queue-delete", **out}
        if ref in q.get("running", []):
            out = await run_in_threadpool(c.comfyui.interrupt)
            c.store.append_log(job_id, "interruption demandée au moteur (run en cours)")
            return {"cancelled": True, "how": "interrupt", **out}
        return {"cancelled": False, "reason": "le moteur ne connaît plus ce run"}

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
                "problems": c.registry.problems_for(c.settings.host_id, workflow, config)}

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

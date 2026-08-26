"""Operator CLI — a second thin driver over the same core as the REST API.

    python -m comfyui_bridge render --prompt "a red desert at dusk" --steps 24
    python -m comfyui_bridge render --prompt "clip" --kind video --duration 3 --fps 8
    python -m comfyui_bridge history --host localhost
    python -m comfyui_bridge serve --port 8080

Existing purely to prove the decoupling: the CLI knows nothing ComfyUI-specific
either — it builds an intent and hands it to the orchestrator, exactly as HTTP
does. A reconciliation rejection prints the RFC 7807 document and exits 2.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .container import build_container
from .core.errors import BridgeError, HardwareReconciliationError, to_problem
from .core.jobs import JobStatus
from .api.schemas import ConstraintIn, IntentIn


def _parse_value(raw: str):
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_constraint(spec: str) -> ConstraintIn:
    # form "key:op:value", value may be a comma list for op=in
    try:
        key, op, value = spec.split(":", 2)
    except ValueError:
        raise SystemExit(f"bad --constraint {spec!r}, expected key:op:value")
    if op == "in":
        parsed = [_parse_value(v) for v in value.split(",")]
    else:
        parsed = _parse_value(value)
    return ConstraintIn(key=key, op=op, value=parsed)


def _cmd_render(args, container) -> int:
    intent_in = IntentIn(
        prompt=args.prompt,
        workflow=args.workflow,
        negative_prompt=args.negative,
        kind=args.kind,
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_s=args.duration,
        seed=args.seed,
        steps=args.steps,
        cfg=args.cfg,
        batch=args.batch,
        constraints=[_parse_constraint(c) for c in (args.constraint or [])],
    )
    orch = container.orchestrator
    try:
        job, plan = orch.accept(intent_in.to_domain())
    except HardwareReconciliationError as exc:
        json.dump(to_problem(exc), sys.stderr, indent=2)
        sys.stderr.write("\n")
        return 2
    except BridgeError as exc:
        json.dump(to_problem(exc), sys.stderr, indent=2)
        sys.stderr.write("\n")
        return 1

    orch.execute(job.id, plan)  # inline (the CLI is synchronous)
    from .api.schemas import JobOut

    final = container.store.get(job.id)
    json.dump(JobOut.of(final).model_dump(), sys.stdout, indent=2)
    sys.stdout.write("\n")

    # A request must expose where its result is: print the concrete location to
    # stderr (stdout stays clean JSON for piping).
    if final.status is JobStatus.SUCCEEDED and final.artifacts:
        if final.simulated:  # the backend says so — not a guess from settings
            sys.stderr.write("\n[SIMULATION : aucun rendu. Livrable = plan (manifest), pas un média.]\n")
        sys.stderr.write("Livrable :\n")
        for a in final.artifacts:
            sys.stderr.write(f"  -> [{a.kind}] {a.path}\n")
        sys.stderr.write(f"Dossier de sortie : {container.settings.comfy_output_dir.resolve()}\n\n")
    return 0 if final.status is JobStatus.SUCCEEDED else 3


def _cmd_add_workflow(args, container) -> int:
    from pathlib import Path
    try:
        graph = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"cannot read {args.file}: {e}")
    name = args.name or Path(args.file).stem
    try:
        spec = container.catalog.register(name, graph)
    except BridgeError as e:
        sys.stderr.write(f"error: {e.detail or e}\n")
        return 1
    out = {"name": spec.name, "kind": spec.kind, "defaults": spec.defaults,
           "bindings": {k: {"node": b.node, "input": b.input} for k, b in spec.bindings.items()}}
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_workflows(args, container) -> int:
    cat = container.catalog
    out = {"default": cat.default_name(), "workflows": {
        n: {"kind": cat.get_spec(n).kind, "defaults": cat.get_spec(n).defaults,
            "limits": cat.get_spec(n).limits} for n in cat.names()
    }}
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_probe(args, container) -> int:
    backend = container.backend
    info = {"backend": container.settings.comfy_backend}
    probe = getattr(backend, "probe", None)
    info["probe"] = probe() if callable(probe) else {"available": None, "reason": "no server to probe"}
    json.dump(info, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if info["probe"].get("available") else 1


def _cmd_history(args, container) -> int:
    rows = container.registry.recent(args.host, args.limit)
    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _cmd_serve(args, container) -> int:
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("uvicorn not installed: pip install 'uvicorn[standard]'")
    uvicorn.run("comfyui_bridge.api.main:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="comfyui_bridge", description="ComfyUI Bridge CLI")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="resolve, reconcile, and run an intent")
    r.add_argument("--prompt", required=True)
    r.add_argument("--workflow", default=None, help="named workflow from the reconciliation file")
    r.add_argument("--negative", default=None)
    r.add_argument("--kind", choices=["image", "video", "audio"], default=None)
    # Defaults are None so omitted flags inherit the workflow's declared defaults.
    r.add_argument("--width", type=int, default=None)
    r.add_argument("--height", type=int, default=None)
    r.add_argument("--fps", type=int, default=None)
    r.add_argument("--duration", type=float, default=None, help="seconds (video)")
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--steps", type=int, default=None)
    r.add_argument("--cfg", type=float, default=None)
    r.add_argument("--batch", type=int, default=None)
    r.add_argument("--constraint", action="append", metavar="key:op:value",
                   help="declarative constraint, repeatable (e.g. width:lte:1024)")
    r.set_defaults(func=_cmd_render)

    sub.add_parser("workflows", help="list workflows declared in the reconciliation file").set_defaults(func=_cmd_workflows)

    aw = sub.add_parser("add-workflow", help="ingest a ComfyUI API-format workflow (auto-bound)")
    aw.add_argument("file", help="path to a ComfyUI API-format .json (Export (API))")
    aw.add_argument("--name", default=None, help="name to register (default: file stem)")
    aw.set_defaults(func=_cmd_add_workflow)

    sub.add_parser("probe", help="test the backend connection (ComfyUI server if http)").set_defaults(func=_cmd_probe)

    h = sub.add_parser("history", help="dump recent executions for a host")
    h.add_argument("--host", default=None)
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=_cmd_history)

    s = sub.add_parser("serve", help="run the FastAPI app with uvicorn")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    container = build_container(Settings.from_env())
    if getattr(args, "host", None) is None and args.command == "history":
        args.host = container.settings.host_id
    return args.func(args, container)


if __name__ == "__main__":
    raise SystemExit(main())

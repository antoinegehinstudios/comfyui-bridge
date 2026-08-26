"""Dependency-free smoke test: the pipeline end to end, offline.

    python scripts/smoke.py

Validates, without a GPU, ComfyUI, FastAPI or pytest:
  1. a run completes and names its artifact,
  2. Hermes lets an unknown configuration through (nothing invented),
  3. a REAL failure is recorded with its actual cause,
  4. the identical run is then refused, citing that past problem,
  5. a different configuration is still allowed.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from comfyui_bridge.config import Settings
from comfyui_bridge.container import build_container
from comfyui_bridge.core.errors import BackendExecutionError, HardwareReconciliationError
from comfyui_bridge.core.intention import Constraint, ConstraintOp, RenderIntent
from comfyui_bridge.core.jobs import JobStatus


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_"))
    c = build_container(Settings(comfy_backend="cli", dry_run=True,
                                 hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out"))
    orch = c.orchestrator

    job, plan = orch.accept(RenderIntent(prompt="a lone astronaut on a red dune"))
    orch.execute(job.id, plan)
    done = c.store.get(job.id)
    assert done.status is JobStatus.SUCCEEDED, done.problem
    assert done.artifacts and pathlib.Path(done.artifacts[0].path).exists()
    print("1. run OK ->", done.artifacts[0].path)

    _, p2 = orch.accept(RenderIntent(prompt="unknown config", width=640, height=640))
    print("2. config inconnue acceptée ->", p2.config)

    def boom(_plan, **_kw):
        raise BackendExecutionError("CUDA error: out of memory allocating 12 GiB")
    orch._backend.submit = boom
    j3, p3 = orch.accept(RenderIntent(prompt="big", width=1024, height=1024))
    orch.execute(j3.id, p3)
    failed = c.store.get(j3.id)
    assert failed.status is JobStatus.FAILED
    assert failed.problem.get("problem_kind") == "oom", failed.problem
    print("3. échec réel enregistré ->", failed.problem["problem_kind"], "sur", p3.config)

    try:
        orch.accept(RenderIntent(prompt="big", width=1024, height=1024))
        print("4. FAIL: le problème connu n'a pas été opposé")
        return 1
    except HardwareReconciliationError as exc:
        print("4. refus du problème connu OK ->", exc.detail[:70])

    _, p5 = orch.accept(RenderIntent(prompt="other", width=512, height=512,
                                     constraints=(Constraint("width", ConstraintOp.LTE, 448),)))
    assert p5.params["width"] == 448
    print("5. autre config + contrainte OK ->", p5.config)

    print("ALL SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

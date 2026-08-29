"""ComfyUI CLI backend — the low-level driver.

Implements the ``RenderBackend`` port by injecting params into the workflow and
invoking the official ``comfy`` CLI on the resulting API-format JSON. In dry-run
(the default) it skips the subprocess and writes a manifest artifact instead, so
the whole pipeline is exercisable without a GPU or a ComfyUI install.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from ..config import Settings
from ..core.errors import BackendExecutionError
from ..core.plan import Artifact, BackendResult, ExecutionPlan
from .catalog import WorkflowCatalog, build_injection
from .injector import apply_overrides, inject
from .media import (DELIVERABLE_EXT, DELIVERY_MECHANISM, artifact_url,
                    is_working_file, media_kind)


class ComfyCliBackend:
    def __init__(self, settings: Settings, catalog: WorkflowCatalog) -> None:
        self._settings = settings
        self._catalog = catalog

    def preview(self, plan: ExecutionPlan) -> dict[str, Any]:
        """Show the ComfyUI graph the named workflow WOULD run — no subprocess."""
        return build_injection(self._catalog, plan)

    # Même mécanisme de livraison que l'autre backend : c'est le module
    # partagé qui décide ce qu'un run livre, donc c'est lui qui le versionne.
    delivery_mechanism = DELIVERY_MECHANISM

    def load_of(self, plan):
        """See ``RenderBackend.load_of`` — read from the graph, never assumed."""
        from .work import WORK_MODEL, effective_values, work_units
        try:
            return work_units(effective_values(self._catalog, plan)), WORK_MODEL
        except Exception:
            return None, None

    def submit(self, plan: ExecutionPlan, on_enqueued=None, on_progress=None,
               on_note=None, on_started=None) -> BackendResult:
        spec = self._catalog.get_spec(plan.workflow)
        graph = apply_overrides(
            inject(self._catalog.load_template(spec), spec.bindings, plan.params), plan.overrides)
        out_dir = self._settings.comfy_output_dir.resolve()  # absolute → openable paths
        out_dir.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex[:12]
        wf_path = out_dir / f"_workflow_{token}.json"
        wf_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

        if self._settings.dry_run:
            return self._dry_run(plan, graph, out_dir, token)
        return self._invoke(plan, wf_path, out_dir)

    # -- dry-run --------------------------------------------------------------

    def _dry_run(self, plan: ExecutionPlan, graph: dict[str, Any], out_dir: Path, token: str) -> BackendResult:
        manifest = out_dir / f"cortex_{plan.kind}_{token}.manifest.json"
        payload = {
            "dry_run": True,
            "kind": plan.kind,
            "workflow": plan.workflow,
            "params": plan.params,
            "config": plan.config,
            "injected_workflow": graph,
        }
        manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Dry-run deliverable = the manifest itself (the raw artifact the
        # pipeline produced). No placeholder image, no gimmick.
        art = Artifact(
            kind="manifest",
            path=str(manifest.resolve()),
            url=artifact_url(out_dir, manifest),
            bytes=manifest.stat().st_size,
        )
        return BackendResult(
            artifacts=[art],
            raw_stdout="dry-run: no ComfyUI invoked, no render — wrote workflow plan only",
            simulated=True,
        )

    # -- real invocation ------------------------------------------------------

    def _invoke(self, plan: ExecutionPlan, wf_path: Path, out_dir: Path) -> BackendResult:
        before = self._snapshot(out_dir)
        cmd = [self._settings.comfy_cli_bin] + [
            arg.format(workflow=str(wf_path), timeout=self._settings.exec_timeout_s)
            for arg in self._settings.comfy_run_args
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._settings.exec_timeout_s + 30,
            )
        except FileNotFoundError as exc:
            raise BackendExecutionError(
                f"ComfyUI CLI not found: {self._settings.comfy_cli_bin!r}", cmd=cmd,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BackendExecutionError(
                f"ComfyUI CLI timed out after {self._settings.exec_timeout_s}s", cmd=cmd,
            ) from exc

        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            raise BackendExecutionError(
                f"ComfyUI CLI exited {proc.returncode}",
                returncode=proc.returncode,
                stderr_tail=combined.strip()[-800:],
            )

        artifacts = self._collect(out_dir, before)
        if not artifacts:
            raise BackendExecutionError(
                "ComfyUI reported success but produced no artifacts",
                stdout_tail=(proc.stdout or "")[-800:],
            )
        return BackendResult(artifacts=artifacts, raw_stdout=(proc.stdout or "")[-2000:])

    # -- output collection ----------------------------------------------------

    @staticmethod
    def _snapshot(out_dir: Path) -> set[Path]:
        return {p for p in out_dir.rglob("*") if p.is_file()}

    def _collect(self, out_dir: Path, before: set[Path]) -> list[Artifact]:
        new_files = [
            p for p in out_dir.rglob("*")
            if p.is_file() and p not in before
            and p.suffix.lower() in DELIVERABLE_EXT and not is_working_file(p)
        ]
        new_files.sort(key=lambda p: p.stat().st_mtime)
        return [
            Artifact(
                kind=media_kind(p),
                path=str(p.resolve()),
                url=artifact_url(out_dir, p),
                bytes=p.stat().st_size,
            )
            for p in new_files
        ]

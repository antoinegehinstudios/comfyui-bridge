"""In-memory job registry.

A prototype-grade store: thread-safe, process-local. The REST API returns a job
id immediately and the caller polls it. Swapping this for Redis/Postgres later
means implementing the same tiny surface — nothing in the core assumes memory.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .errors import JobNotFoundError
from .plan import Artifact


class JobStatus(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"        # handed to the engine, waiting its turn
    RUNNING = "running"      # the engine actually started computing it
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"  # stopped on request — not a failure of the workflow


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    workflow: str = ""
    status: JobStatus = JobStatus.ACCEPTED
    config: str = ""
    # Reported by the backend that ran it — never re-derived from settings.
    simulated: bool = False
    # The engine's OWN reference for this run (ComfyUI prompt_id): the run is
    # visible and manageable in ComfyUI's queue under this id.
    engine_ref: str | None = None
    # Someone asked for this run to stop. What follows (the engine reporting an
    # error) is then a consequence of that request, not a fault of the workflow.
    cancel_requested: bool = False
    # Real progress as REPORTED BY THE ENGINE (ComfyUI websocket), never guessed.
    progress: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    problem: dict[str, Any] | None = None
    logs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, config: str = "", workflow: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, workflow=workflow, config=config)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"no job with id {job_id!r}", job_id=job_id)
        return job

    def set_status(self, job_id: str, status: JobStatus) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.touch()

    def set_engine_ref(self, job_id: str, ref: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.engine_ref = ref
            job.touch()

    def set_progress(self, job_id: str, value: int, maximum: int, node: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.progress = {"value": value, "max": maximum, "node": node}
            job.touch()

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(f"{_now()}  {message}")
            job.logs = job.logs[-200:]  # bound growth
            job.touch()

    def mark_succeeded(self, job_id: str, artifacts: list[Artifact],
                       simulated: bool = False) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.simulated = simulated
            job.status = JobStatus.SUCCEEDED
            job.artifacts = list(artifacts)
            job.touch()

    def mark_failed(self, job_id: str, problem: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            # A run someone stopped did not fail: report what really happened.
            job.status = JobStatus.CANCELLED if job.cancel_requested else JobStatus.FAILED
            job.problem = problem
            job.touch()

    def request_cancel(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.cancel_requested = True
            job.touch()

"""Ports — the interfaces the orchestrator depends on, nothing more.

The orchestrator is written against these Protocols only. Concrete
implementations (the ComfyUI CLI backend, the Hermes reconciler) are injected
from the edges. Swapping ComfyUI for another engine, or the SQLite history for
a service call, is a matter of providing new implementers — the core never
changes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .plan import BackendResult, ExecutionPlan, Reconciliation
from .workflow import WorkflowProfile


@runtime_checkable
class RenderBackend(Protocol):
    def submit(self, plan: ExecutionPlan, on_enqueued=None, on_progress=None) -> BackendResult:
        """Execute a plan and return its artifacts. Blocking.

        ``on_enqueued(engine_ref)`` may fire when the engine accepts the job,
        giving the engine's own reference (e.g. ComfyUI's prompt_id).
        ``on_progress(value, max, node)`` may relay the engine's OWN progress."""
        ...

    def load_of(self, plan: ExecutionPlan) -> tuple[float | None, int | None]:
        """How much work this plan represents, and by which barème.

        Only the adapter can answer: the load is read from the injected graph.
        The core asks for it at the moment it records a run, so that every way
        in — API, CLI, recovery — measures the same thing. Computing it at one
        entry point only left every CLI run with no load at all."""
        ...


@runtime_checkable
class Reconciler(Protocol):
    def reconcile(self, plan: ExecutionPlan) -> Reconciliation:
        """Accept, adjust, or reject a plan against known host limits."""
        ...


@runtime_checkable
class WorkflowRegistry(Protocol):
    """The reconciliation file, seen abstractly: resolve a name to a profile."""

    def get_profile(self, name: str | None) -> WorkflowProfile:
        """Return the profile for `name` (or the default). Raise if unknown."""
        ...

    def names(self) -> list[str]:
        ...

    def default_name(self) -> str:
        ...


@runtime_checkable
class ExecutionJournal(Protocol):
    def record(
        self,
        host: str,
        workflow: str,
        params: dict[str, Any],
        status: str,
        problem: str | None = None,
        detail: str | None = None,
        duration_s: float | None = None,
    ) -> None:
        """Persist a REAL run outcome (with its actual cause when it failed)."""
        ...

"""Domain data that crosses the ports: the execution plan and the results.

These types are deliberately ComfyUI-free. ``params`` is a flat, backend-neutral
dict of resolved values; only the adapter knows how those keys land on nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .intention import RenderIntent


@dataclass
class ExecutionPlan:
    """A fully resolved request ready to hand to a backend."""

    intent: RenderIntent
    params: dict[str, Any]
    kind: str = "image"                 # resolved media kind
    workflow: str = ""                  # resolved workflow name (catalog entry)
    overrides: dict[str, Any] = field(default_factory=dict)   # "node.input" -> value
    # Effective size of the job (pixels x frames x steps, millions). Set by the
    # adapter, which is the only layer that can read the workflow's own values.
    work: float | None = None
    # WHICH way that work was counted — a barème, not a value. Recorded with the
    # run so a later change of counting cannot pollute the fit.
    work_model: int | None = None
    # Values that were asked for but that this workflow binds nothing for: they
    # will not reach the graph. Kept so the run can SAY it, instead of letting a
    # setting quietly do nothing.
    ignored: tuple[str, ...] = ()

    @property
    def config(self) -> str:
        """Fingerprint of the current params — computed, never stored, so it
        cannot drift away from what it describes."""
        from .cost import config_fingerprint
        return config_fingerprint(self.params)

    def with_params(self, params: dict[str, Any]) -> "ExecutionPlan":
        return replace(self, params=params)


@dataclass(frozen=True)
class Reconciliation:
    """Verdict from Hermes: does anything KNOWN stand against this run?"""

    accepted: bool
    plan: ExecutionPlan
    reason: str
    config: str = ""
    known_problems: list[str] = field(default_factory=list)
    problem: str | None = None   # problem kind when refused


@dataclass(frozen=True)
class Artifact:
    kind: str            # image | video | manifest
    path: str
    url: str | None = None
    bytes: int | None = None


@dataclass(frozen=True)
class BackendResult:
    artifacts: list[Artifact]
    raw_stdout: str = ""
    vram_peak_mb: int | None = None
    simulated: bool = False   # True for dry-run: artifacts are a plan, not media
    # Time the ENGINE spent computing, as it measured it — queue waiting
    # excluded. None when the engine did not report it.
    execution_s: float | None = None

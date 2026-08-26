"""Hermes — reconciliation against known problems, called in LOCAL mode.

Hermes' value is memory, not cleverness: before a run, it looks up what has
ALREADY gone wrong here (the local problem registry) and what can be checked
up front (declared dependencies present on the host). If a past problem would
be hit again, it says so — citing the actual past failure — instead of burning
a GPU minute rediscovering it.

No invented heuristics: nothing is refused on a guessed VRAM estimate. Only
observed problems and verifiable facts drive the verdict. An empty registry
means "nothing known" and Hermes lets the run proceed.

``mode``: "local" (default) consults the local registry. The verdict shape is
kept simple so a remote/LLM reconciliation role can be plugged in later without
changing the core.
"""

from __future__ import annotations

from ..core import problems as P
from ..core.plan import ExecutionPlan, Reconciliation
from .registry import ProblemRegistry


class HermesReconciler:
    def __init__(self, registry: ProblemRegistry, host: str, mode: str = "local") -> None:
        self._registry = registry
        self._host = host
        self._mode = mode

    def reconcile(self, plan: ExecutionPlan) -> Reconciliation:
        config = plan.config
        known: list[str] = []

        if self._mode == "off":
            return Reconciliation(True, plan, "reconciliation disabled", config, known)

        # 1. Remembered: has this exact config already failed here for a reason
        #    that would recur? A later success clears it (the host changed).
        for past in self._registry.blocking_problems(self._host, plan.workflow, config):
            kind = past.get("problem")
            known.append(f"{kind} ({past.get('ts', '')[:19]})")
            return Reconciliation(
                False, plan,
                f"déjà rencontré ici : {kind} — {(past.get('detail') or '').strip()[:200]}",
                config, known, problem=kind,
            )

        # 2. Nothing known against it. Surface non-blocking past problems as context.
        for past in self._registry.problems_for(self._host, plan.workflow):
            kind = past.get("problem")
            if kind and kind not in P.BLOCKING:
                known.append(f"{kind} sur {past.get('config')} ({past.get('ts', '')[:19]})")
        reason = "aucun problème connu pour cette configuration"
        if known:
            reason += " (problèmes passés sur d'autres configurations : " + "; ".join(known[:3]) + ")"
        return Reconciliation(True, plan, reason, config, known)

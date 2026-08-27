"""High-level orchestration. Knows the *domain*, not the engine.

Flow for one intent:

    intent ──▶ resolve params ──▶ apply declarative constraints
           ──▶ Hermes.reconcile (accept / adjust / reject)
           ──▶ backend.submit  (ComfyUI, behind a port)
           ──▶ journal.record  (feed the next reconciliation)

Not one line here mentions ComfyUI, a node id, or a CLI flag. Everything
engine-specific is reached through ``RenderBackend`` / ``Reconciler`` /
``ExecutionJournal`` ports.
"""

from __future__ import annotations

from typing import Any

from .errors import BridgeError, HardwareReconciliationError, to_problem
from .problems import OOM as P_OOM, UNKNOWN as P_UNKNOWN, classify as classify_problem
from .intention import Constraint, ConstraintOp, RenderIntent, intent_field_of, intent_fields
from .jobs import Job, JobStatus, JobStore
from .plan import ExecutionPlan
from .ports import ExecutionJournal, Reconciler, RenderBackend, WorkflowRegistry

# A user may constrain in intent terms; these fold onto resolved param keys.
# Derived from the ONE correspondence, so a rename cannot drift between the two.
_CONSTRAINT_ALIASES = {intent_field_of(p): p for p in ("latent_batch",)}
_CONSTRAINT_ALIASES["frames"] = "latent_batch"

# No global parameter defaults: values are not invented here. A workflow that
# declares nothing keeps the values its author baked into the graph.


def derivable_params(kind: str, bindings) -> list[str]:
    """Parameters a caller can set although no node carries them directly.

    A video workflow exposing its frame count AND its frame rate is drivable in
    SECONDS: ``resolve_params`` turns duration x fps into frames. Saying so lets
    the form offer the field a user thinks in, without pretending a node holds
    it. Nothing else is inferred.
    """
    if kind == "video" and "duration_s" not in bindings \
            and "latent_batch" in bindings and "fps" in bindings:
        return ["duration_s"]
    return []


class Orchestrator:
    def __init__(
        self,
        backend: RenderBackend,
        reconciler: Reconciler,
        journal: ExecutionJournal,
        store: JobStore,
        host_id: str,
        registry: WorkflowRegistry,
    ) -> None:
        self._backend = backend
        self._reconciler = reconciler
        self._journal = journal
        self._store = store
        self._host = host_id
        self._registry = registry

    # -- planning -------------------------------------------------------------

    def resolve_params(self, intent: RenderIntent, defaults: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Resolve ONLY the parameters that were actually asked for.

        The workflow is the authority on its own settings. A value is injected
        only when the caller stated it, or when the workflow declares a default
        for it. Anything else is left out entirely, so the graph keeps the value
        its author chose — inventing a value here silently overrode workflows
        (a video workflow was being cut down to a single frame).
        """
        declared = dict(defaults or {})
        kind = (intent.kind.value if intent.kind else None) or declared.get("kind", "image")

        asked: dict[str, Any] = {}
        for field in ("negative_prompt", "width", "height", "steps", "cfg", "seed", "fps",
                      "duration_s", "batch"):
            v = getattr(intent, field, None)
            if v is None:
                v = declared.get(field)
            if v is not None:
                asked[field] = v

        params: dict[str, Any] = {"prompt": intent.prompt}
        for media in ("image", "video"):
            if getattr(intent, media, None):
                params[media] = getattr(intent, media)
        for field, cast in (("negative_prompt", str), ("width", int), ("height", int),
                            ("steps", int), ("cfg", float), ("seed", int), ("fps", int),
                            ("duration_s", float)):
            if field in asked:
                params[field] = cast(asked[field])

        # Frame count: only when a duration was actually requested. For a still,
        # the batch is only set if asked. Otherwise the workflow decides.
        fps = int(asked.get("fps", 0)) or 0
        duration = float(asked.get("duration_s", 0) or 0)
        if kind == "video" and duration > 0 and fps > 0:
            params["latent_batch"] = max(1, round(duration * fps))
        elif "batch" in asked:
            params["latent_batch"] = int(asked["batch"])

        # The caller's own name for its output, kept harmless: a flow driving
        # this service finds its files back by it, whatever happens to the job.
        label = "".join(c for c in (intent.label or "") if c.isalnum() or c in "-_")[:40]
        params["filename_prefix"] = f"cortex/{label or kind}"
        return params, kind

    def _apply_constraints(
        self, params: dict[str, Any], constraints: tuple[Constraint, ...]
    ) -> None:
        for c in constraints:
            key = _CONSTRAINT_ALIASES.get(c.key, c.key)
            if key not in params:
                continue
            current = params[key]
            if c.op is ConstraintOp.EQ:
                params[key] = c.value
            elif c.op is ConstraintOp.LTE and isinstance(current, (int, float)):
                params[key] = min(current, c.value)
            elif c.op is ConstraintOp.GTE and isinstance(current, (int, float)):
                params[key] = max(current, c.value)
            elif c.op is ConstraintOp.IN:
                allowed = c.value if isinstance(c.value, (list, tuple)) else [c.value]
                if current not in allowed and allowed:
                    params[key] = allowed[0]

    def build_plan(self, intent: RenderIntent) -> ExecutionPlan:
        profile = self._registry.get_profile(intent.workflow)  # raises if unknown
        # The workflow's declared kind is the default when the caller states none.
        defaults = {"kind": profile.kind, **profile.defaults}
        params, kind = self.resolve_params(intent, defaults)
        self._apply_constraints(params, intent.constraints)
        # A value the workflow cannot receive goes nowhere. Naming it here is
        # the difference between "your 10 frames were applied" and the truth.
        reachable = set(profile.accepts) | set(derivable_params(kind, profile.accepts))
        ignored = tuple(sorted(k for k in params if profile.accepts and k not in reachable))
        return ExecutionPlan(
            intent=intent,
            params=params,
            kind=kind,
            workflow=profile.name,
            overrides=dict(getattr(intent, "inputs", {}) or {}),
            ignored=ignored,
        )

    def plan_and_reconcile(self, intent: RenderIntent) -> ExecutionPlan:
        """Build a plan and put it past Hermes. Raises when a KNOWN problem stands."""
        plan = self.build_plan(intent)
        verdict = self._reconciler.reconcile(plan)
        if not verdict.accepted:
            raise HardwareReconciliationError(
                verdict.reason,
                problem=verdict.problem,
                config=verdict.config,
                known_problems=verdict.known_problems,
                host=self._host,
                workflow=plan.workflow,
            )
        return verdict.plan

    # -- job lifecycle --------------------------------------------------------

    def accept(self, intent: RenderIntent) -> tuple[Job, ExecutionPlan]:
        """Synchronous, cheap: plan + reconcile, then register a pending job."""
        plan = self.plan_and_reconcile(intent)
        job = self._store.create(
            kind=plan.kind,
            config=plan.config,
            workflow=plan.workflow,
        )
        self._store.append_log(job.id, f"accepted: workflow '{plan.workflow}' ({plan.config}), no known problem")
        if plan.ignored:
            self._store.append_log(
                job.id, "non appliqué — ce workflow n'expose pas : " + ", ".join(plan.ignored))
        # Disclose media the workflow carries and the caller did not replace:
        # that content lands in the result, so it must never be silent.
        profile = self._registry.get_profile(plan.workflow)
        for param, value in (profile.carried or {}).items():
            if param not in plan.params:
                if param in (profile.neutral_for or ()):
                    self._store.append_log(
                        job.id,
                        f"{param} non fourni — élément NEUTRE envoyé à la place "
                        f"(le contenu du workflow, {value}, n'est pas réutilisé)")
                else:
                    # No neutral element exists for this category: the workflow
                    # runs on what it carries, and that must not be implied away.
                    self._store.append_log(
                        job.id,
                        f"{param} non fourni et aucun élément neutre disponible "
                        f"pour cette catégorie — le contenu du workflow est utilisé : {value}")
        return job, plan

    def execute(self, job_id: str, plan: ExecutionPlan) -> None:
        """Blocking backend run. Meant to be handed to a background worker.

        Recording the REAL cause of a failure is the point: the next run of the
        same workflow+config is refused up front, citing that past problem,
        instead of hitting it again.
        """
        import time as _time
        started_at = _time.monotonic()
        self._store.set_status(job_id, JobStatus.QUEUED)
        # Neutral wording: the core does not assert what the backend does (a
        # dry-run invokes nothing — the backend's own note reports the truth).
        self._store.append_log(job_id, "preparing workflow")

        def on_enqueued(ref: str, progress_channel: str = "") -> None:
            self._store.set_engine_ref(job_id, ref)
            self._store.append_log(job_id, f"queued in engine as {ref} (visible in its queue)")
            if progress_channel == "attached":
                self._store.append_log(
                    job_id, "progression branchée — le moteur charge le modèle "
                            "(aucune étape annoncée tant que le calcul n'a pas commencé)")
            elif progress_channel:
                self._store.append_log(job_id, f"progression indisponible : {progress_channel}")

        def on_progress(value: int, maximum: int, node: str) -> None:
            self._store.set_status(job_id, JobStatus.RUNNING)
            self._store.set_progress(job_id, value, maximum, node)

        def on_started() -> None:
            # The engine says it STARTED — until now the job was only waiting.
            self._store.set_status(job_id, JobStatus.RUNNING)
            self._store.append_log(job_id, "le moteur a démarré ce run (fin de l'attente en file)")

        try:
            result = self._backend.submit(
                plan, on_enqueued=on_enqueued, on_progress=on_progress,
                on_note=lambda m: self._store.append_log(job_id, m),
                on_started=on_started)
        except BridgeError as exc:
            kind = classify_problem(f"{exc.detail} {exc.extensions.get('stderr_tail', '')}")
            if kind == P_UNKNOWN and exc.extensions.get("oom"):
                kind = P_OOM
            if self._store.get(job_id).cancel_requested:
                # Nothing was learned about this workflow: someone stopped it.
                # Recording a problem here made Hermes blame a graph that was
                # running perfectly well.
                self._store.append_log(job_id, "arrêté à la demande — aucun problème retenu")
                # The engine's interrupt dump says nothing a reader needs: what
                # happened is simply that someone stopped this run.
                self._store.mark_failed(job_id, {
                    "type": "https://cortex/problems/cancelled",
                    "title": "Run arrêté",
                    "status": 499,
                    "detail": "arrêté à la demande depuis la console ; "
                              "rien n'est retenu contre ce workflow",
                    "problem_kind": "cancelled",
                })
                return
            self._journal.record(self._host, plan.workflow, plan.params,
                                 status="failed", problem=kind, detail=exc.detail)
            problem = to_problem(exc)
            problem["problem_kind"] = kind
            self._store.append_log(job_id, f"failed [{kind}]: {exc.detail}")
            self._store.append_log(job_id, f"problème enregistré pour Hermes : {kind} sur {plan.config}")
            self._store.mark_failed(job_id, problem)
            return

        # The engine's own measure excludes queue waiting; ours does not. Prefer
        # the engine, and record nothing rather than a wall-clock lie.
        measured = result.execution_s
        if measured is None and result.simulated:
            measured = _time.monotonic() - started_at
        self._journal.record(self._host, plan.workflow, plan.params, status="succeeded",
                             duration_s=measured, work=plan.work,
                             work_model=plan.work_model)
        # Surface the backend's own truthful note (e.g. "dry-run: no render").
        if result.raw_stdout:
            self._store.append_log(job_id, result.raw_stdout.strip()[:200])
        if result.simulated:
            # Not a deliverable: a plan written offline, no media produced.
            for a in result.artifacts:
                self._store.append_log(job_id, f"simulated (plan only, no media): {a.path}")
        else:
            # End of chain on success: the produced MEDIA is the deliverable.
            for a in result.artifacts:
                self._store.append_log(job_id, f"delivered media: {a.path}")
        self._store.mark_succeeded(job_id, result.artifacts, simulated=result.simulated,
                                   duration_s=measured)

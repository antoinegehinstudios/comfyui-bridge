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

from dataclasses import replace as _replace
from typing import Any

from .delivery import compare, describe
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


def derivable_params(kind: str, bindings, defaults: dict[str, Any] | None = None) -> list[str]:
    """Parameters a caller can set although no node carries them directly.

    A video workflow whose FRAME COUNT can be reached AND whose frame rate is
    known is drivable in SECONDS: ``resolve_params`` turns duration x fps into
    frames. Saying so lets the form offer the field a user thinks in, without
    pretending a node holds it. Nothing else is inferred.

    « Atteignable » vaut pour une liaison comme pour un paramètre PILOTE d'un
    montage (``pour.jusqu_a``) : le nombre d'images décide alors du nombre de
    blocs sans s'écrire dans aucun nœud. Et la cadence peut n'être qu'un DÉFAUT
    déclaré au catalogue. Mesuré sur « video-longue-stylee-h3 » : cadence en
    défaut (24) et non en liaison, donc aucune durée annoncée, donc un
    formulaire qui n'offrait que la graine pour un montage que la DURÉE pilote.
    """
    declares = defaults or {}
    if kind == "video" and "duration_s" not in bindings \
            and "latent_batch" in bindings \
            and ("fps" in bindings or "fps" in declares):
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

    def resolve_params(self, intent: RenderIntent, defaults: dict[str, Any],
                       workflow: str = "") -> tuple[dict[str, Any], str]:
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
                      "duration_s", "batch", "style_graphique", "style_narratif", "tour"):
            v = getattr(intent, field, None)
            if v is None:
                v = declared.get(field)
            if v is not None:
                asked[field] = v

        # Comme tout le reste : injecté seulement s'il a été demandé. Poser une
        # chaîne vide effacerait le texte que l'auteur a mis dans son graphe.
        params: dict[str, Any] = {}
        if intent.prompt:
            params["prompt"] = intent.prompt
        # Les pièces jointes, telles que l'appelant les a nommées. Rien n'est
        # supposé ici sur leur nombre ni sur leur catégorie : le workflow est
        # seul à dire combien d'entrées média il a, et sous quels noms.
        for param, nom in (intent.media or {}).items():
            if nom:
                params[param] = nom
        for field, cast in (("negative_prompt", str), ("width", int), ("height", int),
                            ("steps", int), ("cfg", float), ("seed", int), ("fps", int),
                            ("duration_s", float),
                            ("style_graphique", str), ("style_narratif", str),
                            ("tour", int)):
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
        # Sans nom donné, c'est le WORKFLOW qui nomme : « cortex/video » mettait
        # tous les runs vidéo du poste sous un seul nom, où plus rien ne se
        # distinguait — le nom du workflow, lui, dit déjà ce qui a produit quoi.
        # RÈGLE GÉNÉRALE DE NOMMAGE, tenue ici parce que c'est ici que le fichier
        # se nomme : « <nom donné>_<type> » — le nom que l'appelant a choisi,
        # puis le workflow qui a produit. Un lanceur montre ses livraisons par
        # ce nom ; le type dit ce qui l'a faite quand deux productions
        # portent le même nom. Sans nom donné, le type seul.
        label = "".join(c for c in (intent.label or "") if c.isalnum() or c in "-_")[:40]
        genre = "".join(c for c in workflow if c.isalnum() or c in "-_")[:40]
        if label and genre and label != genre:
            nom = f"{label}_{genre}"
        else:
            nom = label or genre or kind
        params["filename_prefix"] = f"cortex/{nom}"
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
        params, kind = self.resolve_params(intent, defaults, workflow=profile.name)
        self._apply_constraints(params, intent.constraints)
        # A value the workflow cannot receive goes nowhere. Naming it here is
        # the difference between "your 10 frames were applied" and the truth.
        reachable = set(profile.accepts) | set(
            derivable_params(kind, profile.accepts, defaults))
        # Une durée et une cadence CONVERTIES en nombre d'images ont atteint le
        # graphe par ce nombre : les dire ignorées mentirait au demandeur.
        converties = {"duration_s", "fps"} if (
            kind == "video" and "latent_batch" in params and "latent_batch" in reachable) else set()
        # Nommés comme l'appelant les a envoyés : lui rendre "latent_batch"
        # quand il a écrit "batch" le laissait chercher un champ qui n'existe pas.
        ignored = tuple(sorted(intent_field_of(k) for k in params
                               if profile.accepts and k not in reachable and k not in converties))
        return ExecutionPlan(
            intent=intent,
            params=params,
            kind=kind,
            workflow=profile.name,
            overrides=dict(getattr(intent, "inputs", {}) or {}),
            ignored=ignored,
        )

    def plan_and_reconcile(self, intent: RenderIntent,
                           force: bool = False) -> ExecutionPlan:
        """Construire un plan et le soumettre à la mémoire des problèmes.

        ``force`` passe outre un refus : ce qui bloque encore coûte cher à
        redécouvrir, mais un souvenir doit toujours pouvoir être démenti par les
        faits — sinon la seule preuve de la réparation est interdite par la
        mémoire elle-même.
        """
        plan = self.build_plan(intent)
        verdict = self._reconciler.reconcile(plan)
        if not verdict.accepted and force:
            return plan
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

    def _weigh(self, plan: ExecutionPlan) -> None:
        """Make sure the plan carries its load before anything is recorded.

        Measured: only the HTTP API computed it, so every run launched from the
        CLI was journalled with no load and taught the estimate nothing.
        """
        if plan.work is not None:
            return
        weigh = getattr(self._backend, "load_of", None)
        if weigh is None:
            return
        try:
            plan.work, plan.work_model = weigh(plan)
        except Exception:
            pass            # a load we cannot read is left unknown, not invented

    def _mechanism(self) -> int | None:
        """AVEC QUOI ce run a été livré, à joindre à ce qu'on retient de lui.

        Un souvenir écrit par un mécanisme de livraison qui n'existe plus ne dit
        rien du suivant : sans cette note, personne ne pouvait faire la
        différence, et un workflow réparé par un changement de la passerelle
        elle-même restait accusé.
        """
        return getattr(self._backend, "delivery_mechanism", None)

    # -- job lifecycle --------------------------------------------------------

    def accept(self, intent: RenderIntent, force: bool = False) -> tuple[Job, ExecutionPlan]:
        """Synchronous, cheap: plan + reconcile, then register a pending job."""
        plan = self.plan_and_reconcile(intent, force=force)
        job = self._store.create(
            kind=plan.kind,
            config=plan.config,
            workflow=plan.workflow,
            params=plan.params,
        )
        self._store.append_log(job.id, f"accepted: workflow '{plan.workflow}' ({plan.config})"
                                       + (" — MALGRÉ un problème retenu (essai forcé)" if force
                                          else ", no known problem"))
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

        self._weigh(plan)
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
                                 status="failed", problem=kind, detail=exc.detail,
                                 mechanism=self._mechanism())
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
        # A run the engine served from its cache delivered a real file in no
        # time: recording it as a measure of load would teach that this size
        # costs nothing.
        if result.cached:
            self._store.append_log(
                job_id, "résultat resservi par le cache du moteur — durée non représentative")
        self._journal.record(self._host, plan.workflow, plan.params, status="succeeded",
                             duration_s=measured,
                             work=None if result.cached else plan.work,
                             work_model=None if result.cached else plan.work_model,
                             setup_s=None if result.cached else result.setup_s,
                             mechanism=self._mechanism())
        # Surface the backend's own truthful note (e.g. "dry-run: no render").
        if result.raw_stdout:
            self._store.append_log(job_id, result.raw_stdout.strip()[:200])
        if result.simulated:
            # Not a deliverable: a plan written offline, no media produced.
            for a in result.artifacts:
                self._store.append_log(job_id, f"simulated (plan only, no media): {a.path}")
        else:
            # End of chain on success: the produced MEDIA is the deliverable.
            delivered = []
            for a in result.artifacts:
                self._store.append_log(job_id, f"delivered media: {a.path}")
                # Ce qui a été demandé n'est pas toujours ce qui sort : un
                # workflow peut recalculer les dimensions. Le constater sur le
                # fichier, et le dire — au journal comme au livrable.
                gaps = compare(plan.params, a.measured or {})
                if gaps:
                    self._store.append_log(job_id, describe(gaps))
                delivered.append(_replace(a, gaps=tuple(gaps)))
            result = _replace(result, artifacts=delivered)
        self._store.mark_succeeded(job_id, result.artifacts, simulated=result.simulated,
                                   duration_s=measured)

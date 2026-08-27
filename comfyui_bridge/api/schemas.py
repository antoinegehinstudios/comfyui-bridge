"""Request/response models. The API's contract, kept apart from the domain.

``IntentIn.to_domain`` is the single translation point from wire types to the
frozen ``RenderIntent`` the core speaks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.intention import Constraint, ConstraintOp, MediaKind, RenderIntent
from ..core.jobs import Job


class ConstraintIn(BaseModel):
    key: str = Field(..., examples=["width"])
    op: str = Field(..., description="One of ConstraintOp", examples=["lte"])
    value: Any = Field(..., examples=[1024])


class IntentIn(BaseModel):
    # A field this model does not know is a caller mistake, and silence made it
    # a trap: `latent_batch` (the name the catalogue announced) was accepted
    # with a 200 and dropped. Refusing names the real cause.
    model_config = ConfigDict(extra="forbid")

    # Optionnel : tous les workflows n'ont pas de texte (mise à l'échelle,
    # interpolation…). L'exiger obligeait un appelant à inventer "(sans prompt)",
    # qui partait ensuite dans les paramètres non transmis.
    prompt: str | None = Field(None, examples=["a lone astronaut on a red dune, cinematic"])
    workflow: str | None = Field(None, description="Named entry in the reconciliation file; omit for the default.")
    negative_prompt: str | None = None
    # Omitted fields inherit the workflow's declared defaults.
    kind: str | None = Field(None, description="One of MediaKind; omit to use the workflow's")
    # Only nonsense is refused here (zero, negative). The CEILINGS belong to the
    # workflow: ComfyUI declares each input's min/max and validates the graph it
    # is given. A number invented here contradicted a real workflow — 17 frames
    # were refused although the node accepted them.
    width: int | None = Field(None, ge=1)
    height: int | None = Field(None, ge=1)
    fps: int | None = Field(None, ge=1)
    duration_s: float | None = Field(None, ge=0.0)
    seed: int | None = None
    image: str | None = Field(None, description="Input media name as ComfyUI knows it")
    video: str | None = Field(None, description="Input clip name as ComfyUI knows it")
    steps: int | None = Field(None, ge=1)
    cfg: float | None = Field(None, ge=0.0)
    batch: int | None = Field(None, ge=1)
    label: str | None = Field(None, max_length=40, description=
        "Nom de la sortie côté hôte (fichiers 'cortex/<label>_00001_.…'), pour "
        "qu'un flux appelant retrouve SES livrables. Caractères non sûrs retirés.")
    inputs: dict[str, Any] = Field(default_factory=dict,
        description="Entrées propres au workflow, clé 'noeud.entree' (découvertes via /v1/workflows/{name}/io)")
    constraints: list[ConstraintIn] = Field(default_factory=list)

    def to_domain(self) -> RenderIntent:
        return RenderIntent(
            prompt=self.prompt,
            workflow=self.workflow,
            negative_prompt=self.negative_prompt,
            kind=MediaKind(self.kind) if self.kind else None,
            width=self.width,
            height=self.height,
            fps=self.fps,
            duration_s=self.duration_s,
            seed=self.seed,
            label=self.label,
            image=self.image,
            video=self.video,
            steps=self.steps,
            cfg=self.cfg,
            batch=self.batch,
            inputs=dict(self.inputs or {}),
            constraints=tuple(
                Constraint(c.key, ConstraintOp(c.op), c.value) for c in self.constraints
            ),
        )


class WorkflowImportIn(BaseModel):
    name: str = Field(..., min_length=1, examples=["ltx-2.3-t2v"])
    workflow: dict[str, Any] = Field(..., description="A ComfyUI API-format graph (Export (API) in ComfyUI).")
    source: str | None = Field(None, description="ComfyUI saved-workflow name this was extracted from (enables update detection).")


class ArtifactOut(BaseModel):
    kind: str
    path: str
    url: str | None = None
    bytes: int | None = None


class JobOut(BaseModel):
    id: str
    kind: str
    workflow: str
    status: str
    config: str
    simulated: bool
    engine_ref: str | None
    progress: dict[str, Any] | None
    artifacts: list[ArtifactOut]
    duration_s: float | None = None      # measured by the engine, queue excluded
    problem: dict[str, Any] | None
    logs: list[str]
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, job: Job) -> "JobOut":
        return cls(
            id=job.id,
            kind=job.kind,
            workflow=job.workflow,
            status=job.status.value,
            config=job.config,
            simulated=job.simulated,
            engine_ref=job.engine_ref,
            progress=job.progress,
            artifacts=[ArtifactOut(**a.__dict__) for a in job.artifacts],
            duration_s=job.duration_s,
            problem=job.problem,
            logs=job.logs,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

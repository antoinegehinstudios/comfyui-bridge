"""Request/response models. The API's contract, kept apart from the domain.

``IntentIn.to_domain`` is the single translation point from wire types to the
frozen ``RenderIntent`` the core speaks.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.intention import (Constraint, ConstraintOp, MediaKind, RenderIntent,
                              is_media_param)
from ..core.jobs import Job


class ConstraintIn(BaseModel):
    key: str = Field(..., examples=["width"])
    op: str = Field(..., description="One of ConstraintOp", examples=["lte"])
    value: Any = Field(..., examples=[1024])


class IntentIn(BaseModel):
    # Un champ que ce modèle ne connaît pas n'est PAS admis d'office : le
    # silence en avait fait un piège (« latent_batch », le nom que le catalogue
    # annonçait, était accepté avec un 200 puis jeté). Il est retenu ici, et la
    # route le confronte au workflow visé : une CHAÎNE déclare ses propres
    # champs (« conclusion_s », « mode »), qui s'envoient à la racine du corps ;
    # tout le reste est refusé en nommant la cause.
    model_config = ConfigDict(extra="allow")

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
    # Les pièces jointes. Un workflow n'en a pas un nombre fixe : il annonce les
    # siennes dans `accepts` / `intent_fields` (« image », « image_2 », « audio »
    # …). Elles s'envoient au choix ici, ou directement à la racine du corps sous
    # le nom annoncé — « image »: "x.png" marche comme avant. Valeur = le nom que
    # ComfyUI donne au fichier (celui rendu par POST /v1/inputs/media).
    media: dict[str, str] = Field(default_factory=dict,
        examples=[{"image": "premiere.png", "image_2": "derniere.png", "audio": "voix.wav"}],
        description="Pièces jointes par entrée du workflow : {nom annoncé -> nom ComfyUI}")
    steps: int | None = Field(None, ge=1)
    cfg: float | None = Field(None, ge=0.0)
    batch: int | None = Field(None, ge=1)
    # Les menus de style : une valeur du catalogue du nœud de style que le
    # workflow lie (voir `options` dans /v1/workflows/{name}/io). Le moteur
    # refuse une valeur hors catalogue ; ici on ne recopie pas la liste.
    style_graphique: str | None = Field(None, examples=["sumi-e"],
        description="Style graphique, par sa clé au catalogue du workflow (menu)")
    style_narratif: str | None = Field(None, examples=["quatre-temps-social"],
        description="Style narratif, par sa clé au catalogue du workflow (menu)")
    tour: int | None = Field(None, ge=0, description=
        "Montage à un run par tour : le tour de boucle à rendre seul (0, 1, 2…) ; "
        "sans lui, la passerelle enchaîne elle-même les tours")
    parametres: dict[str, Any] = Field(default_factory=dict, description=
        "Réglages nommés, injectés par les liaisons du workflow (le rôle d'une image de "
        "référence, par exemple) : {nom annoncé -> valeur}, sans nommer aucun nœud")
    label: str | None = Field(None, max_length=40, description=
        "Nom de la sortie côté hôte (fichiers 'cortex/<label>_00001_.…'), pour "
        "qu'un flux appelant retrouve SES livrables. Caractères non sûrs retirés.")
    inputs: dict[str, Any] = Field(default_factory=dict,
        description="Entrées propres au workflow, clé 'noeud.entree' (découvertes via /v1/workflows/{name}/io)")
    constraints: list[ConstraintIn] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _pieces_jointes_a_la_racine(cls, data: Any) -> Any:
        """Accepter une pièce jointe sous le nom que le workflow ANNONCE.

        `/v1/workflows` publie « image_2 », « audio »… comme champs à remplir ;
        les refuser ensuite parce qu'ils ne sont pas des champs fixes de ce
        modèle ferait mentir l'annonce. Ils sont repliés dans `media`, et tout
        autre nom inconnu reste refusé — c'est bien une erreur d'appelant.
        """
        if not isinstance(data, dict):
            return data
        declares = set(cls.model_fields)
        media = dict(data.get("media") or {})
        reste: dict[str, Any] = {}
        for cle, valeur in data.items():
            if cle not in declares and is_media_param(str(cle)):
                if valeur is not None:
                    media[str(cle)] = valeur
            else:
                reste[cle] = valeur
        if media:
            reste["media"] = media
        return reste

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
            media={k: str(v) for k, v in (self.media or {}).items() if v},
            steps=self.steps,
            cfg=self.cfg,
            batch=self.batch,
            style_graphique=self.style_graphique,
            style_narratif=self.style_narratif,
            tour=self.tour,
            parametres=dict(self.parametres or {}),
            inputs=dict(self.inputs or {}),
            constraints=tuple(
                Constraint(c.key, ConstraintOp(c.op), c.value) for c in self.constraints
            ),
        )


class EssaiIn(BaseModel):
    """Un graphe API à faire tourner tel quel, par la file — la porte des
    enquêtes, des bancs et des agents (voir POST /v1/essais)."""
    graphe: dict[str, Any] = Field(..., description="Le graphe au format API de ComfyUI")
    label: str | None = Field(None, max_length=40, description="Le nom de l'essai (ses fichiers : cortex/essais/<label>/)")


class WorkflowImportIn(BaseModel):
    name: str = Field(..., min_length=1, examples=["mon-workflow-video"])
    workflow: dict[str, Any] = Field(..., description="A ComfyUI API-format graph (Export (API) in ComfyUI).")
    source: str | None = Field(None, description="ComfyUI saved-workflow name this was extracted from (enables update detection).")


class ArtifactOut(BaseModel):
    kind: str
    path: str
    url: str | None = None
    bytes: int | None = None
    measured: dict[str, Any] | None = None   # ce que le fichier contient vraiment
    gaps: list[dict[str, Any]] = []          # écarts avec ce qui a été demandé


class JobOut(BaseModel):
    id: str
    kind: str
    workflow: str
    status: str
    config: str
    params: dict[str, Any] = {}          # ce qui a été demandé pour ce run
    simulated: bool
    engine_ref: str | None
    progress: dict[str, Any] | None
    artifacts: list[ArtifactOut]
    duration_s: float | None = None      # measured by the engine, queue excluded
    problem: dict[str, Any] | None
    logs: list[str]
    # Les étapes d'une CHAÎNE, avec le sous-job de chacune. Absentes d'un run
    # ordinaire, qui n'en a pas.
    etapes: list[dict[str, Any]] = []
    # Le corps d'intention tel qu'il a été reçu : ce qui rend le run rejouable
    # sans que l'appelant réassemble quoi que ce soit.
    demande: dict[str, Any] = {}
    # La chaîne dont ce run est une étape, s'il y en a une.
    parent: str | None = None
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
            params=job.params,
            simulated=job.simulated,
            engine_ref=job.engine_ref,
            progress=job.progress,
            artifacts=[ArtifactOut(**{**a.__dict__, "gaps": list(a.gaps)})
                       for a in job.artifacts],
            duration_s=job.duration_s,
            problem=job.problem,
            logs=job.logs,
            etapes=job.etapes,
            demande=job.demande,
            parent=job.parent,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class RejeuIn(BaseModel):
    """Rejouer un run, à l'identique ou avec des réglages changés.

    Le corps est facultatif : sans lui, c'est exactement la même demande. Ce
    qui est nommé dans ``reglages`` remplace le champ correspondant — le reste
    ne bouge pas, et l'appelant n'a rien à réassembler.
    """

    model_config = ConfigDict(extra="forbid")

    reglages: dict[str, Any] = Field(default_factory=dict,
                                     examples=[{"seed": 4242, "duration_s": 30}])


class RaccourciIn(BaseModel):
    """Enregistrer (ou modifier) un ensemble de réglages nommé, pour un mode.

    Les valeurs viennent d'une LIVRAISON (``job_id`` : sa demande, sans les
    pièces jointes) ou sont données telles quelles (``valeurs``), ou les deux —
    ce qui est nommé recouvre alors ce que la livraison portait. Le modèle
    refuse ce qu'il ne connaît pas, comme ``RejeuIn`` : un champ mal
    orthographié repartait sinon avec un 201 et n'enregistrait rien.

    À la modification, seuls les champs PRÉSENTS dans le corps changent
    (``model_fields_set``) : sans cela, renommer un raccourci effaçait son
    résumé, qui n'était pas renvoyé.
    """

    model_config = ConfigDict(extra="forbid")

    titre: str = Field("", examples=["Sépia au trait sec, à la chandelle"])
    resume: str = Field("", examples=["Un papier ambré, plus de trait, une flamme étroite."])
    job_id: str | None = None
    valeurs: dict[str, Any] | None = Field(None, examples=[{"duration_s": 45, "fond": "sepia"}])
    ordre: int | None = None

"""In-memory job registry.

A prototype-grade store: thread-safe, process-local. The REST API returns a job
id immediately and the caller polls it. Swapping this for Redis/Postgres later
means implementing the same tiny surface — nothing in the core assumes memory.

Un dossier de persistance peut lui être donné : chaque job y est écrit à chaque
changement d'état, et la liste comme la lecture retombent dessus. Sans lui, un
redémarrage effaçait l'historique des runs — l'appelant voyait ses livrables
dans le dossier de sortie sans pouvoir dire ce qui les avait produits.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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


TERMINAUX = (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)

# La progression change plusieurs fois par seconde ; l'écrire à chaque fois
# ferait un fichier réécrit 3 000 fois pour un run de dix minutes. Un état
# perdu de deux secondes ne coûte rien, ces écritures-là si.
_PAS_DE_PROGRESSION_S = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    workflow: str = ""
    status: JobStatus = JobStatus.ACCEPTED
    config: str = ""
    # Les paramètres résolus pour ce run : ce qu'on a demandé, à relire à côté
    # de ce qui a été livré.
    params: dict[str, Any] = field(default_factory=dict)
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
    # What the ENGINE measured for this run, queue wait excluded. A caller that
    # drives this service from a larger flow needs the cost of what it asked.
    duration_s: float | None = None
    problem: dict[str, Any] | None = None
    logs: list[str] = field(default_factory=list)
    # Les étapes d'une CHAÎNE, dans l'ordre, avec ce que chacune a donné. Vide
    # pour un run ordinaire : un job de chaîne n'exécute rien lui-même, tout ce
    # qu'il y a à voir de lui est là.
    etapes: list[dict[str, Any]] = field(default_factory=list)
    # Le corps d'intention TEL QU'IL A ÉTÉ REÇU (champs plats). C'est ce qui
    # rend un run rejouable côté serveur : reconstruire la demande à partir des
    # paramètres résolus rendait autre chose que ce qui avait été demandé.
    demande: dict[str, Any] = field(default_factory=dict)
    # Le job de chaîne dont celui-ci est une étape, s'il y en a un.
    parent: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()


def _en_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "kind": job.kind, "workflow": job.workflow,
        "status": job.status.value, "config": job.config, "params": job.params,
        "simulated": job.simulated, "engine_ref": job.engine_ref,
        "cancel_requested": job.cancel_requested, "progress": job.progress,
        "artifacts": [{**a.__dict__, "gaps": list(a.gaps)} for a in job.artifacts],
        "duration_s": job.duration_s, "problem": job.problem, "logs": job.logs,
        "etapes": job.etapes, "demande": job.demande, "parent": job.parent,
        "created_at": job.created_at, "updated_at": job.updated_at,
    }


def _depuis_dict(data: dict[str, Any]) -> Job:
    artefacts = []
    for a in data.get("artifacts") or []:
        brut = dict(a)
        brut["gaps"] = tuple(brut.get("gaps") or ())
        artefacts.append(Artifact(**brut))
    return Job(
        id=str(data.get("id")), kind=str(data.get("kind") or ""),
        workflow=str(data.get("workflow") or ""),
        status=JobStatus(data.get("status") or "accepted"),
        config=str(data.get("config") or ""), params=dict(data.get("params") or {}),
        simulated=bool(data.get("simulated")), engine_ref=data.get("engine_ref"),
        cancel_requested=bool(data.get("cancel_requested")), progress=data.get("progress"),
        artifacts=artefacts, duration_s=data.get("duration_s"), problem=data.get("problem"),
        logs=list(data.get("logs") or []), etapes=list(data.get("etapes") or []),
        demande=dict(data.get("demande") or {}), parent=data.get("parent"),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
    )


class JobStore:
    def __init__(self, persist_dir: str | Path | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._dir = Path(persist_dir) if persist_dir else None
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._ecrit_le: dict[str, float] = {}

    # -- persistance ----------------------------------------------------------

    def _fichier(self, job_id: str) -> Path | None:
        return (self._dir / f"{job_id}.json") if self._dir is not None else None

    def _ecrire(self, job: Job, force: bool = True) -> None:
        """Écriture atomique (tmp + rename) : un fichier à moitié écrit lu par
        une autre instance vaut moins qu'un fichier périmé."""
        cible = self._fichier(job.id)
        if cible is None:
            return
        maintenant = time.monotonic()
        if not force and maintenant - self._ecrit_le.get(job.id, 0.0) < _PAS_DE_PROGRESSION_S:
            return
        self._ecrit_le[job.id] = maintenant
        try:
            tmp = cible.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(_en_dict(job), ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(cible)
        except OSError:
            pass          # un historique non écrit ne doit jamais coûter le run

    def _relire(self, job_id: str) -> Job | None:
        cible = self._fichier(job_id)
        if cible is None or not cible.exists():
            return None
        try:
            return _depuis_dict(json.loads(cible.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    # -- cycle de vie ---------------------------------------------------------

    def create(self, kind: str, config: str = "", workflow: str = "",
               params: dict[str, Any] | None = None,
               demande: dict[str, Any] | None = None,
               parent: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, workflow=workflow, config=config,
                  params=dict(params or {}), demande=dict(demande or {}), parent=parent)
        with self._lock:
            self._jobs[job.id] = job
            self._ecrire(job)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # RÉADOPTION : un job relu du disque redevient un job pilotable.
                # Rendu détaché, il se lisait (200) mais toute écriture — un
                # arrêt, un statut — levait KeyError dans un magasin vidé par
                # le redémarrage (mesuré le 2026-09-18 : cancel → 500).
                job = self._relire(job_id)
                if job is not None:
                    self._jobs[job_id] = job
        if job is None:
            raise JobNotFoundError(f"no job with id {job_id!r}", job_id=job_id)
        return job

    def clore_les_chaines_orphelines(self, raison: str) -> list[str]:
        """Les CHAÎNES encore « en cours » sur le disque au démarrage sont mortes :
        une chaîne s'exécute dans un fil de CE processus, et le fil n'a pas
        survécu au redémarrage. Les fermer, en le disant, plutôt que de les
        laisser « running » pour toujours (mesuré le 2026-09-18 : une chaîne
        zombie que rien ne pouvait plus arrêter). Un run simple, lui, est aux
        mains du moteur : c'est le rattrapage des runs en vol qui le suit."""
        if self._dir is None:
            return []
        closes: list[str] = []
        try:
            fichiers = sorted(self._dir.glob("*.json"))
        except OSError:
            return []
        for f in fichiers:
            job = self.get(f.stem) if f.stem not in self._jobs else self._jobs[f.stem]
            if not job.etapes or job.status not in (JobStatus.ACCEPTED, JobStatus.QUEUED,
                                                       JobStatus.RUNNING):
                continue
            self.append_log(job.id, f"chaîne close au démarrage : {raison}")
            self.mark_failed(job.id, {
                "type": "https://cortex/problems/chaine-interrompue",
                "title": "Chaîne interrompue par un redémarrage",
                "status": 503,
                "detail": f"{raison} ; les étapes faites restent faites — "
                          f"POST /v1/jobs/{job.id}/reprendre repart de la première non faite",
                "problem_kind": "chaine-interrompue",
            })
            closes.append(job.id)
        return closes

    def list(self, limit: int = 50) -> list[Job]:
        """Les runs, du plus récent au plus ancien : mémoire ET fichiers.

        Les deux sources, parce qu'un redémarrage ne doit pas effacer ce qui a
        été livré, et que ce qui tourne maintenant n'est pas encore écrit.
        """
        limite = max(1, min(int(limit), 500))
        with self._lock:
            vivants = list(self._jobs.values())
        connus = {j.id for j in vivants}
        tous = list(vivants)
        if self._dir is not None:
            try:
                fichiers = sorted(self._dir.glob("*.json"),
                                  key=lambda f: f.stat().st_mtime, reverse=True)
            except OSError:
                fichiers = []
            for f in fichiers[: limite * 3]:
                if f.stem in connus:
                    continue
                relu = self._relire(f.stem)
                if relu is not None:
                    tous.append(relu)
                    connus.add(relu.id)
        tous.sort(key=lambda j: j.created_at, reverse=True)
        return tous[:limite]

    def set_status(self, job_id: str, status: JobStatus) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.touch()
            self._ecrire(job)

    def set_engine_ref(self, job_id: str, ref: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.engine_ref = ref
            job.touch()
            self._ecrire(job)

    def set_progress(self, job_id: str, value: int, maximum: int, node: str | None = None,
                     etape: dict[str, Any] | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.progress = {"value": value, "max": maximum, "node": node}
            if etape is not None:
                # La progression du sous-job en cours, relayée telle quelle : sans
                # elle, une chaîne n'affichait qu'« étape 2 sur 5 » pendant vingt
                # minutes, sans rien dire de ce qui se passait dedans.
                job.progress["etape"] = etape
            job.touch()
            self._ecrire(job, force=False)

    def set_etapes(self, job_id: str, etapes: list[dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.etapes = list(etapes)
            job.touch()
            self._ecrire(job)

    def set_parent(self, job_id: str, parent: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.parent = parent
            job.touch()
            self._ecrire(job)

    def set_demande(self, job_id: str, demande: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.demande = dict(demande or {})
            job.touch()
            self._ecrire(job)

    def append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(f"{_now()}  {message}")
            job.logs = job.logs[-200:]  # bound growth
            job.touch()
            self._ecrire(job, force=False)

    def mark_succeeded(self, job_id: str, artifacts: list[Artifact],
                       simulated: bool = False, duration_s: float | None = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.simulated = simulated
            job.status = JobStatus.SUCCEEDED
            job.artifacts = list(artifacts)
            job.duration_s = duration_s
            job.touch()
            self._ecrire(job)

    def mark_failed(self, job_id: str, problem: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            # A run someone stopped did not fail: report what really happened.
            job.status = JobStatus.CANCELLED if job.cancel_requested else JobStatus.FAILED
            job.problem = problem
            job.touch()
            self._ecrire(job)

    def set_artifacts(self, job_id: str, artifacts: list[Artifact]) -> None:
        """Ce qu'un run a produit, même s'il finit mal : une chaîne arrêtée à son
        contrôle a écrit de vrais fichiers, et les taire les rendait introuvables."""
        with self._lock:
            job = self._jobs[job_id]
            job.artifacts = list(artifacts)
            job.touch()
            self._ecrire(job)

    def request_cancel(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.cancel_requested = True
            job.touch()
            self._ecrire(job)

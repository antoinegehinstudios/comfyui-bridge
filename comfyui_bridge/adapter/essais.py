"""Les ESSAIS : un graphe qu'une enquête, un banc ou un agent fait tourner sur
le moteur — par la porte de la passerelle, jamais à côté.

Mesuré le 2026-09-18 : une enquête avait envoyé ses expériences directement au
moteur (``POST :8188/prompt``) ; ses prompts passaient avant le rendu
d'Antoine, qui a attendu vingt minutes derrière elles sans que rien ne le dise.
Un essai qui passe par ici entre dans la file des demandes, sur sa voie : il ne
tourne que quand aucune demande n'attend, et il CÈDE la place à une demande
qui arrive (interrompu chez le moteur, il repart de zéro après elle).
"""

from __future__ import annotations

from typing import Any

from ..core.errors import BridgeError
from ..core.jobs import JobStatus


def executer_essai(container, job_id: str, graphe: dict[str, Any], label: str) -> None:
    """Faire tourner UN graphe API tel quel, comme un job : accepté, en file,
    tourné, livré — ou cédé."""
    store = container.store
    backend = container.backend
    soumettre = getattr(backend, "soumettre_graphe", None)
    if soumettre is None:
        store.mark_failed(job_id, {
            "type": "https://cortex/problems/essai-impossible", "title": "Essai impossible",
            "status": 501, "detail": "ce backend ne sait pas faire tourner un graphe tel quel",
            "problem_kind": "unknown"})
        return
    store.set_status(job_id, JobStatus.QUEUED)
    store.append_log(job_id, "essai : le graphe part chez le moteur tel quel")

    def on_enqueued(ref: str, progress_channel: str = "") -> None:
        store.set_engine_ref(job_id, ref)
        store.append_log(job_id, f"queued in engine as {ref} (visible in its queue)")

    def on_progress(value: int, maximum: int, node: str) -> None:
        store.set_status(job_id, JobStatus.RUNNING)
        store.set_progress(job_id, value, maximum, node)

    def on_started() -> None:
        store.set_status(job_id, JobStatus.RUNNING)
        store.append_log(job_id, "le moteur a démarré cet essai")

    try:
        result = soumettre(graphe, label=label, on_enqueued=on_enqueued, on_progress=on_progress,
                           on_note=lambda m: store.append_log(job_id, m), on_started=on_started)
    except BridgeError as exc:
        if container.file.a_cede(job_id):
            # Ce n'est pas un échec : une demande est passée devant. Le job
            # redevient « en file », la file le rejouera de zéro.
            store.set_status(job_id, JobStatus.QUEUED)
            store.append_log(job_id, "essai interrompu par le moteur pour céder la place — "
                                     "il repartira de zéro quand aucune demande n'attendra")
            raise
        if store.get(job_id).cancel_requested:
            store.append_log(job_id, "essai arrêté à la demande")
            store.mark_failed(job_id, {
                "type": "https://cortex/problems/cancelled", "title": "Essai arrêté", "status": 499,
                "detail": "arrêté à la demande", "problem_kind": "cancelled"})
            return
        store.append_log(job_id, f"essai en échec : {exc.detail}")
        store.mark_failed(job_id, {
            "type": "https://cortex/problems/essai", "title": "Essai en échec", "status": 500,
            "detail": exc.detail, "problem_kind": "unknown"})
        return
    store.mark_succeeded(job_id, result.artifacts, simulated=False, duration_s=result.execution_s)
    store.append_log(job_id, f"essai livré : {len(result.artifacts)} fichier(s)")


def ceder_le_moteur(container, job_id: str) -> None:
    """Interrompre chez le moteur ce que cet essai y fait tourner : retiré de
    sa file s'il attend, interrompu s'il tourne. Sans marquer le job « arrêté » :
    il n'est pas arrêté, il cède, et il repartira."""
    job = container.store.get(job_id)
    ref = getattr(job, "engine_ref", None)
    if not ref:
        return
    file = container.comfyui.queue()
    if ref in file.get("pending", []):
        container.comfyui.cancel([ref])
    elif ref in file.get("running", []):
        container.comfyui.interrupt()


def sur_cession(container, job_id: str, fois: int) -> None:
    container.store.append_log(job_id, f"essai remis en tête de sa voie ({fois} cession(s)) — "
                                       f"repart dès que la voie des demandes est vide")

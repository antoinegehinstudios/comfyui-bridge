"""Domain errors.

Each carries the metadata an RFC 7807 ``application/problem+json`` response
needs (``problem_type``, ``title``, ``status``) plus arbitrary ``extensions``
that become extension members of the problem document. The API layer is the
only thing that turns these into HTTP; the core just raises them.
"""

from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    problem_type: str = "about:blank"
    title: str = "Bridge error"
    status: int = 500

    def __init__(self, detail: str = "", **extensions: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extensions: dict[str, Any] = extensions


class IntentValidationError(BridgeError):
    problem_type = "https://cortex/problems/invalid-intent"
    title = "Invalid render intent"
    status = 400


class HardwareReconciliationError(BridgeError):
    """Raised when Hermes refuses a run because a KNOWN problem stands against it
    (a problem already met here, or a dependency verifiably missing)."""

    problem_type = "https://cortex/problems/reconciliation-refused"
    title = "Reconciliation refused: known problem"
    status = 422


class WorkflowMappingError(BridgeError):
    """The external mapping references a node/input absent from the workflow."""

    problem_type = "https://cortex/problems/workflow-mapping"
    title = "Workflow mapping error"
    status = 500


class UnknownWorkflowInputError(BridgeError):
    """The CALLER addressed a node/input the workflow does not have.

    Distinct from a broken mapping of ours: nothing is wrong with the service,
    the request pointed nowhere. Reporting it as 500 blamed the server for a
    typo in a raw input.
    """

    problem_type = "https://cortex/problems/unknown-workflow-input"
    title = "Unknown workflow input"
    status = 422


class InputValueRefusedError(BridgeError):
    """La valeur envoyée sort de ce que la passerelle déclare accepter.

    Distinct d'un champ inconnu : le nom est bon, la valeur ne l'est pas (hors
    bornes, hors menu, requis absent). Les confondre disait « entrée inconnue »
    pour une durée trop longue, et l'appelant cherchait un champ qui existe.
    """

    problem_type = "https://cortex/problems/input-value-refused"
    title = "Input value refused"
    status = 422


class BackendExecutionError(BridgeError):
    problem_type = "https://cortex/problems/backend-execution"
    title = "ComfyUI execution failed"
    status = 502


class ChainControlFailedError(BridgeError):
    """Un contrôle déclaré par une chaîne est faux sur ce qui a été mesuré.

    Ce n'est ni une panne du moteur ni une faute de l'appelant : la chaîne a
    tourné, et ce qu'elle a produit ne tient pas ce qu'elle promet. Les fichiers
    déjà écrits restent livrés — c'est en les regardant qu'on comprend.
    """

    problem_type = "https://cortex/problems/controle-echoue"
    title = "Contrôle de chaîne en échec"
    status = 422


class MediaAssemblyError(BridgeError):
    """Le montage d'un média a échoué (extraction, recollage, mesure).

    Porte la ligne décisive de l'outil : sans elle, un échec d'assemblage se
    lisait « commande en erreur » et il fallait rejouer à la main pour savoir
    que le fichier d'entrée manquait.
    """

    problem_type = "https://cortex/problems/media-assembly"
    title = "Media assembly failed"
    status = 500


class JobNotFoundError(BridgeError):
    problem_type = "https://cortex/problems/job-not-found"
    title = "Job not found"
    status = 404


class DependencyUnavailableError(BridgeError):
    """An optional capability (e.g. the headless browser) is not installed."""

    problem_type = "https://cortex/problems/dependency-unavailable"
    title = "Capability unavailable"
    status = 503


def to_problem(exc: BridgeError, instance: str | None = None) -> dict[str, Any]:
    """Render a domain error as an RFC 7807 problem document (as a plain dict)."""
    body: dict[str, Any] = {
        "type": exc.problem_type,
        "title": exc.title,
        "status": exc.status,
    }
    if exc.detail:
        body["detail"] = exc.detail
    if instance:
        body["instance"] = instance
    # Extension members sit at the top level alongside the standard fields.
    body.update(exc.extensions)
    return body

"""RFC 7807 problem responses.

Every error leaving the API — domain rejection, validation failure, 404, or an
unexpected crash — is serialised as ``application/problem+json`` with the
standard members (``type``, ``title``, ``status``, ``detail``, ``instance``)
plus any extension members the domain error carried (e.g. ``signature``,
``vram_budget_mb`` on a reconciliation failure).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..core.errors import BridgeError, to_problem

PROBLEM_MEDIA = "application/problem+json"


def _resp(status: int, body: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=jsonable_encoder(body), media_type=PROBLEM_MEDIA)


def _nommer_le_champ(request: Request, probleme: dict) -> dict:
    """Le LIBELLÉ du champ refusé, quand la passerelle en déclare un.

    Le noyau connaît le libellé qu'une chaîne écrit pour SON champ ; les
    libellés déclarés au fichier de réconciliation (« Durée » pour
    ``duration_s``) vivent, eux, dans le catalogue — le noyau ne les voit pas.
    Sans ce raccord, un refus ne nommait le champ que par sa clé, et un
    formulaire ne pouvait pas dire lequel de ses champs il devait montrer.
    """
    champ = probleme.get("field")
    if not champ or probleme.get("libelle"):
        return probleme
    conteneur = getattr(request.app.state, "container", None)
    menus = getattr(getattr(conteneur, "catalog", None), "menus", None) or {}
    menu = menus.get(champ)
    libelle = menu.get("libelle") if isinstance(menu, dict) else None
    if libelle:
        probleme["libelle"] = libelle
    return probleme


async def bridge_error_handler(request: Request, exc: BridgeError) -> JSONResponse:
    return _resp(exc.status, _nommer_le_champ(
        request, to_problem(exc, instance=str(request.url.path))))


async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _resp(422, {
        "type": "https://cortex/problems/validation",
        "title": "Request validation failed",
        "status": 422,
        "detail": "one or more fields are invalid",
        "instance": str(request.url.path),
        "errors": exc.errors(),
    })


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _resp(exc.status_code, {
        "type": "about:blank",
        "title": exc.detail if isinstance(exc.detail, str) else "HTTP error",
        "status": exc.status_code,
        "instance": str(request.url.path),
    })


async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    return _resp(500, {
        "type": "about:blank",
        "title": "Internal server error",
        "status": 500,
        "instance": str(request.url.path),
    })


def install_problem_handlers(app) -> None:
    app.add_exception_handler(BridgeError, bridge_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_handler)

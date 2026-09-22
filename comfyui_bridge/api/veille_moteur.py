"""Un moteur qui meurt est relevé, et c'est dit.

Mesuré le 2026-09-22 : le moteur s'est arrêté EN COURS DE RUN, sans une ligne
dans son journal (une faute du pilote graphique, déjà vue). Personne ne l'a
relevé ; la production a attendu la fin de son budget (une heure) avant
d'échouer, et les suivantes n'avaient plus de moteur du tout.

Ce veilleur ne fait qu'une chose : regarder si le moteur ÉCOUTE, et le relever
quand il ne répond plus ET qu'il est à nous (un profil « géré » — jamais un
moteur qu'un autre a lancé). Il ne relève rien tant que le moteur répond :
redémarrer un moteur vivant tuerait le run qui tourne (mesuré le 2026-09-20 —
deux productions perdues).
"""

from __future__ import annotations

import asyncio
import logging

# Tous les combien on regarde, et combien de regards muets de suite valent une
# mort. Trois fois trente secondes : un redémarrage volontaire (une mise en
# service) a le temps de se faire sans qu'on s'en mêle.
PAS_S = 30.0
COUPS_AVANT_DE_RELEVER = 3

_LOG = logging.getLogger("comfyui_bridge.veille_moteur")


async def veiller(conteneur, journal=None, pas: float = PAS_S,
                  coups: int = COUPS_AVANT_DE_RELEVER) -> None:
    from starlette.concurrency import run_in_threadpool

    from ..adapter.engines import ensure_engine, is_alive

    muets = 0
    while True:
        await asyncio.sleep(pas)
        try:
            vivant = await run_in_threadpool(is_alive, conteneur.settings.comfyui_base_url, 3.0)
        except Exception:                                # noqa: BLE001 — une sonde qui échoue est un silence
            vivant = False
        if vivant:
            muets = 0
            continue
        muets += 1
        if muets < coups:
            continue
        muets = 0
        gere = bool(getattr(conteneur.engine, "manage", False))
        if not gere:
            _LOG.warning("le moteur ne répond plus, et il n'est pas à nous : rien à relever")
            if journal is not None:
                journal.retard(0.0, ["moteur muet, profil non géré — non relevé"])
            continue
        _LOG.warning("le moteur ne répond plus depuis ~%.0f s : on le relève", pas * coups)
        try:
            etat = await run_in_threadpool(ensure_engine, conteneur.engine, 240.0,
                                           conteneur.settings.hermes_db.parent)
            conteneur.engine_state.clear()
            conteneur.engine_state.update(etat)
            _LOG.warning("moteur relevé : %s", etat.get("state"))
        except Exception as exc:                          # noqa: BLE001 — jamais fatal au service
            _LOG.warning("le moteur n'a pas pu être relevé : %s", exc)

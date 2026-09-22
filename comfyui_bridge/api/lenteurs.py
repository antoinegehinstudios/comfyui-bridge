"""Ce qui FIGE le service : le mesurer, le nommer, le publier.

La passerelle sert ses requêtes dans UNE boucle : un appel qui calcule ou lit
des fichiers sans céder la main arrête tout le reste — les autres requêtes, les
flux d'événements des jobs en cours, le lancement d'une production. Mesuré le
2026-09-22 : un premier `GET /v1/workflows` a tenu 14,6 s et, pendant ce
temps, `/healthz` (13,6 s), `/v1/jobs` (13,5 s) et `/v1/file` (13,3 s) ont
attendu — du dehors, l'écran ne répond plus et l'utilisateur dit « ça a
lâché ».

Ici, deux instruments qui ne changent rien à ce que la passerelle fait :

* une MESURE par appel (le temps entre la première et la dernière ligne), qui
  garde les appels lents et les appels ratés ;
* un VEILLEUR qui mesure le retard de la boucle elle-même (elle devrait se
  réveiller toutes les 500 ms) et, quand elle est en retard, nomme les appels
  qui étaient en vol à ce moment-là.

Rien n'est deviné : ce qui est publié est ce qui a été mesuré. Sans cela, une
panne vue par l'utilisateur ne laissait AUCUNE trace de ce côté-ci.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

# Au-delà, un appel est LENT : il a tenu la boucle assez longtemps pour se voir
# du dehors (un écran qui ne répond plus, un flux qui se fige).
SEUIL_LENT_S = 2.0

# La boucle se réveille toutes les 500 ms ; au-delà de ce retard, elle était
# tenue par autre chose — c'est cela qui fige tout.
PAS_DU_VEILLEUR_S = 0.5
SEUIL_RETARD_S = 1.0

# Ce qu'on garde : assez pour comprendre une soirée, pas assez pour peser.
MEMOIRE = 200

_LOG = logging.getLogger("comfyui_bridge.lenteurs")


class Lenteurs:
    """La mémoire des appels lents, des appels ratés et des retards de boucle."""

    def __init__(self, memoire: int = MEMOIRE) -> None:
        self.appels: deque[dict[str, Any]] = deque(maxlen=memoire)
        self.retards: deque[dict[str, Any]] = deque(maxlen=memoire)
        self.en_vol: dict[int, dict[str, Any]] = {}
        self.compte = 0
        self.compte_lents = 0
        self.compte_rates = 0

    # -- les appels ---------------------------------------------------------

    def commence(self, methode: str, chemin: str) -> dict[str, Any]:
        self.compte += 1
        appel = {"methode": methode, "chemin": chemin, "depuis": time.monotonic(),
                 "debut": time.time()}
        self.en_vol[id(appel)] = appel
        return appel

    def finit(self, appel: dict[str, Any], statut: int | None, erreur: str | None = None) -> float:
        self.en_vol.pop(id(appel), None)
        duree = time.monotonic() - appel["depuis"]
        if erreur is not None or (statut is not None and statut >= 500) or duree >= SEUIL_LENT_S:
            ligne = {"methode": appel["methode"], "chemin": appel["chemin"],
                     "duree_s": round(duree, 2), "statut": statut, "erreur": erreur,
                     "quand": _instant(appel["debut"])}
            self.appels.append(ligne)
            if erreur is not None or (statut is not None and statut >= 500):
                self.compte_rates += 1
            if duree >= SEUIL_LENT_S:
                self.compte_lents += 1
            _LOG.warning("appel %s %s : %.2f s%s", appel["methode"], appel["chemin"], duree,
                         f" — {erreur}" if erreur else f" ({statut})")
        return duree

    def vus_en_vol(self) -> list[str]:
        maintenant = time.monotonic()
        return [f"{a['methode']} {a['chemin']} (depuis {maintenant - a['depuis']:.1f} s)"
                for a in list(self.en_vol.values())]

    # -- la boucle ----------------------------------------------------------

    def retard(self, secondes: float, en_vol: list[str]) -> None:
        self.retards.append({"retard_s": round(secondes, 2), "en_vol": en_vol,
                             "quand": _instant(time.time())})
        _LOG.warning("boucle en retard de %.2f s — en vol : %s", secondes,
                     ", ".join(en_vol) or "rien")

    def vue(self) -> dict[str, Any]:
        return {
            "seuil_lent_s": SEUIL_LENT_S, "seuil_retard_s": SEUIL_RETARD_S,
            "appels_mesures": self.compte, "appels_lents": self.compte_lents,
            "appels_rates": self.compte_rates,
            "derniers_appels": list(self.appels)[-40:],
            "derniers_retards": list(self.retards)[-40:],
            "en_vol": self.vus_en_vol(),
            "note": "un appel est gardé ici s'il a duré au moins le seuil, ou s'il a raté ; "
                    "un retard de boucle dit que TOUT attendait, et ce qui était en vol",
        }


def _instant(epoque: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoque, timezone.utc).isoformat()


async def veiller(lenteurs: Lenteurs, pas: float = PAS_DU_VEILLEUR_S,
                  seuil: float = SEUIL_RETARD_S) -> None:
    """Le veilleur : il ne fait RIEN qu'attendre et regarder l'heure. Un réveil
    en retard ne peut vouloir dire qu'une chose — la boucle était tenue."""
    boucle = asyncio.get_running_loop()
    while True:
        avant = boucle.time()
        await asyncio.sleep(pas)
        retard = boucle.time() - avant - pas
        if retard >= seuil:
            lenteurs.retard(retard, lenteurs.vus_en_vol())


def poser(app, lenteurs: Lenteurs) -> None:
    """Brancher la mesure sur une application ASGI, sans rien changer d'autre."""

    @app.middleware("http")
    async def _mesurer(request, appel_suivant):            # noqa: ANN001
        appel = lenteurs.commence(request.method, request.url.path)
        try:
            reponse = await appel_suivant(request)
        except Exception as exc:                           # noqa: BLE001 — mesuré puis relevé
            lenteurs.finit(appel, None, f"{type(exc).__name__}: {exc}")
            raise
        lenteurs.finit(appel, reponse.status_code)
        return reponse

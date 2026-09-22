"""Rejouer une demande qui a peut-être abouti, sans créer un second job.

Un lanceur qui perd la réponse d'un `POST /v1/render` ne sait pas ce qui s'est
passé : la demande n'est peut-être jamais arrivée, ou elle a été acceptée et
c'est la réponse qui s'est perdue (un lien coupé, un proxy qui ferme). Sans
rien de plus, il n'a que deux mauvais choix — renoncer à une production, ou la
lancer deux fois (deux jobs, deux fois la carte graphique, deux livrables).

La CLÉ D'IDEMPOTENCE tranche : l'appelant met un `Idempotency-Key` dans sa
demande ; la première fois, elle crée le job et la clé retient lequel ; toute
demande ultérieure portant la même clé rend CE job, sans rien relancer. C'est
ce qui rend un réessai sûr, et donc possible.

La mémoire est celle du processus, avec un âge : une clé ne sert qu'aux
minutes qui suivent (le temps d'un réessai), jamais à rejouer une production
d'hier — et un redémarrage l'oublie, ce qui est dit sur la route.
"""

from __future__ import annotations

import threading
import time

# Combien de temps une clé retient son job. Un réessai se fait en secondes ;
# une demi-heure laisse de la marge à un lien qui rame, sans retenir
# indéfiniment ce qu'on a lancé ce matin.
DUREE_S = 1800.0

# Assez pour une soirée de travail ; au-delà, les plus vieilles s'oublient.
MEMOIRE = 500


class Cles:
    """Les clés vues, et le job que chacune a créé."""

    def __init__(self, duree_s: float = DUREE_S, memoire: int = MEMOIRE) -> None:
        self._duree = duree_s
        self._memoire = memoire
        self._verrou = threading.Lock()
        self._vues: dict[str, tuple[str, float]] = {}

    def lire(self, cle: str | None) -> str | None:
        """Le job déjà créé sous cette clé, ou None."""
        if not cle:
            return None
        with self._verrou:
            self._oublier_les_vieilles()
            vu = self._vues.get(str(cle))
            return vu[0] if vu else None

    def retenir(self, cle: str | None, job_id: str) -> None:
        if not cle:
            return
        with self._verrou:
            self._vues[str(cle)] = (str(job_id), time.monotonic())
            self._oublier_les_vieilles()

    def _oublier_les_vieilles(self) -> None:
        maintenant = time.monotonic()
        for cle, (_job, quand) in list(self._vues.items()):
            if maintenant - quand > self._duree:
                del self._vues[cle]
        # Un plafond, pour qu'une mémoire de processus reste une mémoire.
        while len(self._vues) > self._memoire:
            plus_vieille = min(self._vues, key=lambda k: self._vues[k][1])
            del self._vues[plus_vieille]

    def vue(self) -> dict:
        with self._verrou:
            return {"clés_retenues": len(self._vues), "duree_s": self._duree,
                    "note": "mémoire du processus : un redémarrage les oublie ; une clé ne "
                            "rend un job que pendant sa durée"}

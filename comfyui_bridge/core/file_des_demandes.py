"""La file des demandes : une seule demande de l'utilisateur à la fois — et
rien d'autre ne passe devant elle.

Deux demandes acceptées ensemble (mesuré le 2026-09-18 : deux chaînes à 350 ms
d'intervalle) se disputaient le moteur run après run — leurs blocs alternaient
dans sa file, chacune attendait l'autre, et le poste tenait les deux à la
fois. Antoine : « ne permets pas que deux requêtes formulées par l'utilisateur
se fassent en même temps ; une seule à la fois, avec une file d'attente ».

Le même jour, une ENQUÊTE (un agent) a envoyé ses expériences directement au
moteur : ses prompts passaient avant le rendu d'Antoine, qui a attendu vingt
minutes derrière elles. Antoine : « ne corrige pas ce cas unique, ajuste
l'outillage pour que ce type de problème n'apparaisse plus, by design ».

Ce module est cette file, à DEUX voies :

* les DEMANDES — ce que l'utilisateur formule (un rendu, une chaîne, un rejeu,
  une reprise) : dans l'ordre d'acceptation, une seule à la fois ;
* les ESSAIS — ce qu'une enquête, un banc, un agent veut faire tourner sur le
  moteur : ils passent par la même porte (``POST /v1/essais``), ne tournent
  que quand AUCUNE demande n'attend, et CÈDENT la place : une demande qui
  arrive pendant un essai l'interrompt chez le moteur ; l'essai revient en
  tête de sa voie et repart, de zéro, quand la voie des demandes est vide.

UN SEUL fil exécute. Une demande qui attend le dit (son rang, celles qui la
précèdent) ; une demande ou un essai retiré avant son tour ne tourne jamais.
Ce qu'une demande fait tourner à l'intérieur (les sous-jobs d'une chaîne, les
tours d'un montage) n'entre pas ici : c'est SA place qu'elle occupe, le temps
qu'elle dure. Ce qui, malgré tout, atteindrait le moteur SANS passer par ici
est un travail ÉTRANGER : la passerelle le retire de la file du moteur quand
une demande attend derrière lui (voir l'adaptateur), et le dit.
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

DEMANDE = "demande"
ESSAI = "essai"


@dataclass
class Entree:
    job_id: str
    genre: str
    executer: Callable[..., Any]
    args: tuple = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    # Combien de fois cet essai a cédé la place : dit dans son journal.
    cessions: int = 0


class FileDesDemandes:
    """Un fil, deux voies : la demande en cours, celles qui attendent, les essais."""

    def __init__(self, dire: Callable[[str, str], Any] | None = None,
                 sur_erreur: Callable[[str, BaseException], Any] | None = None,
                 ceder: Callable[[str], Any] | None = None,
                 sur_cession: Callable[[str, int], Any] | None = None) -> None:
        self._demandes: deque[Entree] = deque()
        self._essais: deque[Entree] = deque()
        self._en_cours: Entree | None = None
        self._cede: str | None = None            # l'essai en cours à qui on a demandé de céder
        self._lock = threading.Lock()
        self._reveil = threading.Condition(self._lock)
        self._dire = dire or (lambda job_id, message: None)
        self._sur_erreur = sur_erreur or (lambda job_id, exc: None)
        # Interrompre chez le moteur ce que l'essai en cours y fait tourner :
        # l'adaptateur sait comment (sa file, son interruption) ; ici on ne
        # sait que QUAND.
        self._ceder = ceder or (lambda job_id: None)
        self._sur_cession = sur_cession or (lambda job_id, fois: None)
        self._fil: threading.Thread | None = None
        self._arret = False

    # -- ce qu'on lit ---------------------------------------------------------

    def etat(self) -> dict[str, Any]:
        with self._lock:
            return {"en_cours": self._en_cours.job_id if self._en_cours else None,
                    "genre_en_cours": self._en_cours.genre if self._en_cours else None,
                    "en_attente": [e.job_id for e in self._demandes],
                    "essais_en_attente": [e.job_id for e in self._essais]}

    def place_de(self, job_id: str) -> dict[str, Any] | None:
        """La place d'une entrée : ``{"rang": n, "devant": [...]}`` — rang 0,
        elle tourne ; rang n, n entrées avant elle (pour un essai : la demande
        en cours, toutes les demandes qui attendent, les essais avant lui).
        ``None`` quand elle n'est plus dans la file."""
        with self._lock:
            if self._en_cours is not None and job_id == self._en_cours.job_id:
                return {"rang": 0, "devant": [], "genre": self._en_cours.genre}
            devant: list[str] = [self._en_cours.job_id] if self._en_cours else []
            for e in self._demandes:
                if e.job_id == job_id:
                    return {"rang": len(devant), "devant": devant, "genre": DEMANDE}
                devant.append(e.job_id)
            for e in self._essais:
                if e.job_id == job_id:
                    return {"rang": len(devant), "devant": devant, "genre": ESSAI}
                devant.append(e.job_id)
        return None

    def a_cede(self, job_id: str) -> bool:
        """Cet essai a-t-il reçu l'ordre de céder la place ? (lu par son
        exécuteur quand le moteur lui rend une interruption)"""
        with self._lock:
            return self._cede == job_id

    # -- ce qu'on fait --------------------------------------------------------

    def deposer(self, job_id: str, executer: Callable[..., Any], *args: Any,
                essai: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Poser une entrée à la fin de sa voie et rendre sa place. Le fil
        d'exécution naît à la première entrée, et vit tant que le processus.
        Une DEMANDE déposée pendant un ESSAI le fait céder."""
        entree = Entree(job_id, ESSAI if essai else DEMANDE, executer, args, kwargs)
        a_faire_ceder: str | None = None
        with self._lock:
            (self._essais if essai else self._demandes).append(entree)
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name="file-des-demandes", daemon=True)
                self._fil.start()
            if not essai and self._en_cours is not None and self._en_cours.genre == ESSAI \
                    and self._cede is None:
                self._cede = self._en_cours.job_id
                a_faire_ceder = self._cede
            self._reveil.notify()
        place = self.place_de(job_id) or {"rang": 0, "devant": []}
        if place["rang"]:
            quoi = "essai" if essai else "demande"
            self._dire(job_id, f"{quoi} en file d'attente : {place['rang']} entrée(s) avant "
                               f"({', '.join(x[:8] for x in place['devant'])}) — une seule à la fois"
                               + (" ; un essai ne tourne que quand aucune demande n'attend" if essai else ""))
        if a_faire_ceder is not None:
            self._dire(a_faire_ceder, f"une demande ({job_id[:8]}) arrive : cet essai cède la place "
                                      f"— interrompu chez le moteur, il repartira de zéro après elle")
            try:
                self._ceder(a_faire_ceder)
            except Exception as exc:                    # noqa: BLE001
                self._dire(a_faire_ceder, f"le moteur n'a pas interrompu l'essai ({exc}) : la demande "
                                          f"attendra sa fin")
        return place

    def retirer(self, job_id: str) -> bool:
        """Retirer une entrée qui attend encore : elle ne tournera jamais.
        Faux si elle est en cours ou déjà passée (rien à retirer ici)."""
        with self._lock:
            for voie in (self._demandes, self._essais):
                for e in list(voie):
                    if e.job_id == job_id:
                        voie.remove(e)
                        return True
        return False

    def _boucle(self) -> None:
        while not self._arret:
            with self._lock:
                while not self._demandes and not self._essais and not self._arret:
                    self._reveil.wait()
                if self._arret:
                    return
                # Les demandes d'abord, toujours ; un essai seulement quand
                # aucune demande n'attend.
                entree = self._demandes.popleft() if self._demandes else self._essais.popleft()
                self._en_cours = entree
                self._cede = None
            try:
                entree.executer(*entree.args, **entree.kwargs)
            except BaseException as exc:                    # noqa: BLE001
                # Une entrée qui casse ne doit jamais tuer la file : la
                # suivante a le droit de tourner. L'erreur est dite au job —
                # sauf si l'essai a cédé la place : ce n'est pas une erreur.
                with self._lock:
                    cedee = self._cede == entree.job_id
                if not cedee:
                    try:
                        self._sur_erreur(entree.job_id, exc)
                    except Exception:                           # noqa: BLE001
                        traceback.print_exc()
            finally:
                with self._lock:
                    cedee = self._cede == entree.job_id
                    if cedee:
                        # Il revient en tête de sa voie, pour repartir de zéro
                        # dès que la voie des demandes est vide.
                        entree.cessions += 1
                        self._essais.appendleft(entree)
                    self._en_cours = None
                    self._cede = None
                    self._reveil.notify_all()
                if cedee:
                    try:
                        self._sur_cession(entree.job_id, entree.cessions)
                    except Exception:                           # noqa: BLE001
                        traceback.print_exc()

    def arreter(self) -> None:
        with self._lock:
            self._arret = True
            self._reveil.notify_all()

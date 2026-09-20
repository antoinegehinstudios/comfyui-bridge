"""Exécuter une chaîne : les étapes, dans l'ordre, avec ce qu'elles se passent.

Le principe tient en une phrase : une étape « rendre » est UN RUN ORDINAIRE.
Elle passe par le même orchestrateur, le même Hermes, le même journal, le même
registre de jobs — le sous-job est visible dans ``/v1/jobs`` comme les autres.
Rien n'est réécrit ici de ce que la passerelle sait déjà faire ; ce module ne
tient que ce qui est PROPRE à l'enchaînement : la reprise des fichiers d'une
étape à l'autre, le relais de la progression au parent, l'arrêt, et les
opérations de montage qu'aucun workflow ne porte.

Les fichiers intermédiaires vivent sous ``<sortie>/cortex/_travail/<job>/`` :
ce sont de vrais .mp4 et de vrais .png, et sans ce dossier réservé la liste des
livrables offrait cinquante images de travail avant la vidéo commandée.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import shutil
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..core import chaine as noyau
from ..core.errors import (BridgeError, ChainControlFailedError, MediaAssemblyError,
                           to_problem)
from ..core.intention import MediaKind, RenderIntent
from ..core.jobs import JobStatus
from ..core.plan import Artifact
from . import materiel, montage_video
from .measure import measure
from .media import DOSSIER_DE_TRAVAIL, SIDECAR_SUFFIX, artifact_url, media_kind

# Ce que le journal d'un job de chaîne appelle un problème de contrôle. Nommé
# une fois : la mémoire d'Hermes et la réponse HTTP doivent dire le même mot.
CONTROLE_ECHOUE = "controle-echoue"


def etapes_initiales(chaine: noyau.Chaine, technique=None) -> list[dict[str, Any]]:
    """Les étapes telles qu'on les annonce AVANT d'en faire une seule.

    Un job de chaîne accepté doit déjà dire ce qu'il va faire : sans cela, il
    n'était qu'un identifiant muet jusqu'à la première étape finie. Une étape à
    RÔLE annonce le graphe que la technique choisie lui donne — à l'acceptation
    elle est connue ; sans technique, le rôle seul, qui dit au moins ce que
    l'étape tient.
    """
    return [{"id": e.id, "genre": e.genre, "workflow": workflow_annonce(e, technique),
             "statut": "todo", "job_id": None, "resultat": None, "note": None}
            for e in chaine.etapes]


def workflow_annonce(etape: noyau.Etape, technique=None) -> str | None:
    """Le graphe qu'une étape va lancer, tel qu'on peut l'annoncer d'avance."""
    if etape.workflow is not None or etape.role is None:
        return etape.workflow
    role = (getattr(technique, "roles", None) or {}).get(etape.role)
    return role.workflow if role is not None else etape.role


def dossier_de_travail(sortie: Path, job_id: str) -> Path:
    return Path(sortie).resolve() / "cortex" / DOSSIER_DE_TRAVAIL / job_id


def arreter_au_moteur(container, job_id: str) -> dict[str, Any]:
    """Arrêter CE run, par le moyen qui convient là où il en est.

    En file, le moteur retire ce prompt (les autres gardent leur place) ; en
    cours, c'est son interruption. Ce sont ses opérations officielles : la
    passerelle ne tient aucune file à elle qu'elle pourrait vider.
    """
    job = container.store.get(job_id)
    ref = getattr(job, "engine_ref", None)
    if not ref:
        return {"cancelled": False, "reason": "ce job n'a pas encore été pris par le moteur"}
    # L'intention d'arrêt est notée AVANT d'interroger le moteur : elle ne
    # dépend pas de lui, et c'est quand il ne répond plus qu'on veut arrêter.
    container.store.request_cancel(job_id)
    try:
        file = container.comfyui.queue()
    except Exception as exc:                        # noqa: BLE001
        # Un moteur injoignable est un cas NORMAL de l'annulation, pas une
        # erreur serveur (mesuré le 2026-09-18 : moteur mort, cancel → 500 ;
        # le run, lui, restait « en cours » pour toujours).
        return _clore_sans_moteur(container, job, f"le moteur ne répond plus ({exc})")
    if ref in file.get("pending", []):
        sortie = container.comfyui.cancel([ref])
        container.store.append_log(job_id, "annulé dans la file du moteur (il n'avait pas commencé)")
        return {"cancelled": True, "how": "queue-delete", **sortie}
    if ref in file.get("running", []):
        sortie = container.comfyui.interrupt()
        container.store.append_log(job_id, "interruption demandée au moteur (run en cours)")
        return {"cancelled": True, "how": "interrupt", **sortie}
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ACCEPTED):
        # Le moteur ne connaît plus ce run alors qu'il est encore « en cours »
        # ici : il est MORT (moteur relancé, run perdu), pas en cours.
        return _clore_sans_moteur(container, job, "le moteur ne connaît plus ce run")
    return {"cancelled": False, "reason": "le moteur ne connaît plus ce run"}


def _clore_sans_moteur(container, job, raison: str) -> dict[str, Any]:
    """Clore un run que le moteur ne tient plus : l'arrêt est noté, le job est
    fermé (« cancelled », l'arrêt étant demandé), et on dit pourquoi."""
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.ACCEPTED):
        container.store.append_log(job.id, f"arrêté sans le moteur : {raison}")
        container.store.mark_failed(job.id, {
            "type": "https://cortex/problems/cancelled",
            "title": "Run arrêté",
            "status": 499,
            "detail": f"arrêté à la demande — {raison} ; rien n'est retenu contre ce workflow",
            "problem_kind": "cancelled",
        })
    return {"cancelled": True, "how": "moteur-absent", "reason": raison}


class RunnerDeChaines:
    """Conduit une chaîne pour un job parent, dans le fil qui l'appelle."""

    def __init__(self, container: Any) -> None:
        self._c = container
        self._force = False
        # Ce que la passerelle a DÉPOSÉ chez le moteur, et d'où : un graphe ne
        # cite que le nom rendu, mais c'est sur le fichier qu'on mesure ce que
        # le graphe ne dit pas (la taille d'une vidéo d'entrée, avant le run).
        self._deposes: dict[str, Path] = {}

    # -- conduite -------------------------------------------------------------

    def executer(self, job_id: str, chaine: noyau.Chaine, valeurs: dict[str, Any],
                 label: str = "", force: bool = False,
                 reprise_de: str | None = None) -> None:
        c = self._c
        self._force = force              # passe outre un souvenir, étape par étape
        store = c.store
        technique = self.technique_voulue(chaine, valeurs)
        etapes = etapes_initiales(chaine, technique)
        store.set_etapes(job_id, etapes)
        store.set_status(job_id, JobStatus.RUNNING)
        store.append_log(job_id, f"chaîne « {chaine.nom} » : {len(etapes)} étapes")
        travail = dossier_de_travail(c.settings.comfy_output_dir, job_id)
        # Les résultats commencent par ce qu'un renvoi peut lire AVANT toute
        # étape : le fichier de la technique choisie (« $technique.<chemin> »).
        resultats: dict[str, Any] = noyau.resultats_initiaux(chaine, technique)
        produits: list[str] = []
        duree = 0.0
        repris: dict[str, dict[str, Any]] = {}
        if reprise_de:
            try:
                repris = self._etapes_reprises(job_id, chaine, reprise_de)
            except BridgeError as exc:
                self._echouer(job_id, etapes, 0, chaine, valeurs, exc, produits)
                return

        for rang, etape in enumerate(chaine.etapes):
            if store.get(job_id).cancel_requested:
                self._abandonner(job_id, etapes, rang, chaine, produits)
                return
            if etape.id in repris:
                # UNE ÉTAPE REPRISE D'UN JOB ÉCHOUÉ : son résultat est relu, pas
                # recalculé — six heures de déroulement 4K ne se perdent pas pour
                # un dépôt refusé à l'étape suivante (mesuré le 2026-09-16).
                resultat = repris[etape.id]
                statut = str(resultat.pop("_statut", "done"))
                resultats[etape.id] = resultat
                etapes[rang]["statut"] = statut
                etapes[rang]["note"] = f"repris du job {reprise_de}"
                etapes[rang]["resultat"] = _resume(resultat)
                for cle in ("job_id", "job_ids", "tranches", "tours"):
                    if cle in resultat:
                        etapes[rang][cle] = resultat[cle]
                store.set_etapes(job_id, etapes)
                quoi = ("sautée" if statut == "skipped"
                        else _dire(resultat) if resultat.get("livrable") or "livrable" not in resultat
                        else "sans livrable")
                store.append_log(job_id, f"étape {etape.id} ({etape.genre}) reprise du job "
                                         f"{reprise_de} : {quoi}")
                for cle in ("livrable", "fichier"):
                    chemin = resultat.get(cle)
                    if isinstance(chemin, str) and chemin not in produits:
                        produits.append(chemin)
                continue
            sautee = self._a_sauter(etape, valeurs, resultats, chaine, technique)
            if sautee is not None:
                # UNE ÉTAPE FACULTATIVE SANS RAISON D'ÊTRE EST SAUTÉE, ET LE DIT :
                # son « quand » désigne une valeur vide (un appel final sans
                # texte). Elle rend son média tel quel en livrable, pour que
                # l'étape suivante qui la nomme (« $appel.livrable ») reprenne
                # ce qu'elle aurait reçu — rien n'est rendu, rien n'est perdu.
                resultats[etape.id] = sautee
                etapes[rang]["statut"] = "skipped"
                etapes[rang]["note"] = sautee.get("raison")
                etapes[rang]["resultat"] = _resume(sautee)
                store.set_etapes(job_id, etapes)
                store.append_log(job_id, f"étape {etape.id} ({etape.genre}) sautée : "
                                         f"{sautee.get('raison')}")
                continue
            etapes[rang]["statut"] = "running"
            store.set_etapes(job_id, etapes)
            store.set_progress(job_id, rang, len(etapes), etape.id)
            debut = time.monotonic()
            try:
                resultat = self._executer_etape(job_id, etape, valeurs, resultats,
                                                travail, label, etapes, rang,
                                                chaine_nom=chaine.nom)
            except BridgeError as exc:
                if store.get(job_id).cancel_requested:
                    self._abandonner(job_id, etapes, rang, chaine, produits)
                    return
                self._echouer(job_id, etapes, rang, chaine, valeurs, exc, produits)
                return
            except Exception as exc:            # noqa: BLE001
                # repli: une panne imprévue d'une étape doit finir comme un
                # échec DIT (journal + problème sur le job), pas comme un fil de
                # fond qui meurt en laissant la chaîne « en cours » pour toujours.
                self._echouer(job_id, etapes, rang, chaine, valeurs,
                              MediaAssemblyError(f"étape {etape.id!r} : {exc}"), produits)
                return
            duree += resultat.pop("_duree", None) or (time.monotonic() - debut)
            resultats[etape.id] = resultat
            etapes[rang]["statut"] = "done"
            note = resultat.pop("_note", None)
            if note:
                etapes[rang]["note"] = str(note)      # ce que l'étape a à dire d'elle-même
            etapes[rang]["resultat"] = _resume(resultat)
            store.set_etapes(job_id, etapes)
            store.append_log(job_id, f"étape {etape.id} ({etape.genre}) : {_dire(resultat)}")
            for cle in ("livrable", "fichier"):
                chemin = resultat.get(cle)
                if isinstance(chemin, str) and chemin not in produits:
                    produits.append(chemin)

        final = noyau.resoudre(chaine.livrable, valeurs, resultats, strict=False) \
            if chaine.livrable else None
        store.set_progress(job_id, len(etapes), len(etapes), None)
        # UNE CHAÎNE RÉUSSIE NE LIVRE QUE SON LIVRABLE. Antoine, 2026-09-16 :
        # « maestro ne doit pas, dans son outil de visualisation des
        # productions, afficher les produits d'itérations, mais seulement la
        # production finale montée ; il doit toujours livrer l'état terminé ».
        # Ce que les étapes ont produit reste lisible dans `etapes[].resultat`
        # et dans les sous-jobs — mais ce n'est pas une livraison, et le montrer
        # à côté du montage faisait choisir au spectateur entre cinq fichiers
        # dont un seul était la vidéo commandée. En ÉCHEC, rien ne change : les
        # fichiers déjà écrits restent listés, c'est en les regardant qu'on
        # comprend.
        final_present = isinstance(final, str) and final and Path(final).is_file()
        if final_present and produits:
            store.append_log(
                job_id, f"livré : {Path(final).name} — les {len(produits)} fichiers d'étapes "
                        f"restent lisibles dans les étapes et leurs sous-jobs")
        store.mark_succeeded(job_id, self._artefacts(final, [] if final_present else produits),
                             duration_s=duree)
        c.registry.record(c.settings.host_id, chaine.nom, valeurs, status="succeeded",
                          duration_s=duree)

    # -- reprise ---------------------------------------------------------------

    def _etapes_reprises(self, job_id: str, chaine: noyau.Chaine,
                         reprise_de: str) -> dict[str, dict[str, Any]]:
        """Ce qu'un job échoué a déjà fait, et que celui-ci reprend tel quel.

        Les étapes sont reprises DANS L'ORDRE, jusqu'à la première qui n'a pas
        abouti : une étape faite (« done ») est relue — son livrable doit
        encore exister, son récit est relu sur ses sous-jobs, fusionné s'il y
        avait des tranches — ; une étape légitimement sautée l'est encore ; la
        première étape en échec (et tout ce qui la suit) est rejouée. Un job
        d'une autre chaîne, ou dont les fichiers ont disparu, ne se reprend
        pas : c'est dit, et la chaîne échoue avant d'avoir rien dépensé.
        """
        store = self._c.store
        ancien = store.get(reprise_de)                  # inconnu : dit par le magasin
        if ancien.workflow != chaine.nom:
            raise MediaAssemblyError(
                f"reprise impossible : le job {reprise_de} est un run de "
                f"« {ancien.workflow} », pas de « {chaine.nom} »")
        enregs = {e.get("id"): e for e in (ancien.etapes or []) if isinstance(e, dict)}
        repris: dict[str, dict[str, Any]] = {}
        for etape in chaine.etapes:
            e = enregs.get(etape.id)
            if not e:
                break
            if e.get("statut") == "done":
                repris[etape.id] = self._resultat_repris(reprise_de, etape, e, chaine.nom)
            elif e.get("statut") == "skipped" and isinstance(e.get("resultat"), dict):
                # Une étape SAUTÉE par sa définition (« quand » vide) porte un
                # résultat ; une étape sautée parce que la chaîne avait échoué
                # avant elle n'en porte pas — celle-là se rejoue.
                repris[etape.id] = {**e["resultat"], "_statut": "skipped"}
            else:
                break
        if repris:
            store.append_log(job_id, f"reprise du job {reprise_de} : {len(repris)} étape(s) "
                                     f"reprise(s) — {', '.join(repris)}")
        else:
            store.append_log(job_id, f"reprise du job {reprise_de} : aucune étape aboutie "
                                     f"à reprendre, la chaîne repart du début")
        return repris

    def _resultat_repris(self, ancien_id: str, etape: noyau.Etape,
                         e: dict[str, Any], chaine_nom: str = "") -> dict[str, Any]:
        """Le résultat COMPLET d'une étape faite, reconstruit depuis sa fiche.

        La fiche ne garde qu'un résumé (le récit y perd son calendrier et sa
        caméra) ; ce que les étapes suivantes lisent — « $deroulement.recit… »
        — est relu sur les sous-jobs, et fusionné comme au premier passage
        quand l'étape était rendue par tranches. Une étape reprise de la
        MÉMOIRE n'a pas de sous-job : son récit est relu dans la mémoire.
        """
        store = self._c.store
        res = dict(e.get("resultat") or {})
        if etape.genre == "rendre":
            ids = list(res.get("job_ids") or ([res["job_id"]] if res.get("job_id") else []))
            recits: list[dict[str, Any]] = []
            cle = (res.get("memoire") or {}).get("cle") if isinstance(res.get("memoire"), dict) else None
            if not ids and cle:
                souvenir = self._souvenir(chaine_nom, etape.id, str(cle))
                garde = (souvenir or {}).get("resultat") if souvenir else None
                if isinstance(garde, dict) and isinstance(garde.get("recit"), dict):
                    recits.append(garde["recit"])
            for sid in ids:
                try:
                    sous = store.get(sid)
                except BridgeError as exc:
                    raise MediaAssemblyError(
                        f"reprise impossible : le sous-job {sid} de l'étape {etape.id!r} "
                        f"du job {ancien_id} n'existe plus") from exc
                recit = _recit(sous.artifacts, lambda raison: None)
                if recit is not None:
                    recits.append(recit)
            if len(recits) > 1:
                res["recit"] = {**fusionner_recits(recits), "tranches": len(ids),
                                "tranches_au_recit": len(recits)}
            elif recits:
                res["recit"] = recits[0]
        for cle in ("livrable", "fichier"):
            chemin = res.get(cle)
            if isinstance(chemin, str) and chemin and not Path(chemin).is_file():
                raise MediaAssemblyError(
                    f"reprise impossible : le fichier de l'étape {etape.id!r} du job "
                    f"{ancien_id} n'existe plus ({Path(chemin).name}) — relancer la chaîne")
        if res.get("depot") and isinstance(res.get("fichier"), str):
            # Un dépôt repris : la passerelle sait encore d'où il vient.
            self._deposes[str(res["depot"])] = Path(res["fichier"])
        return res

    # -- fins ------------------------------------------------------------------

    def _artefacts(self, final: Any, produits: list[str]) -> list[Artifact]:
        """Le livrable d'abord, puis ce que les étapes ont produit.

        Les fichiers de travail n'y sont pas : ils vivent sous ``_travail`` et
        `is_working_file` les tient hors des livrables.
        """
        from .media import is_working_file
        sortie = Path(self._c.settings.comfy_output_dir).resolve()
        chemins: list[str] = []
        if isinstance(final, str) and final:
            chemins.append(final)
        chemins += [p for p in produits if p not in chemins]
        artefacts: list[Artifact] = []
        for chemin in chemins:
            fichier = Path(chemin)
            if not fichier.is_file() or is_working_file(fichier):
                continue
            try:
                url = artifact_url(sortie, fichier)
            except ValueError:
                url = None              # produit hors du dossier servi : pas d'URL, mais un chemin
            artefacts.append(Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                                      url=url, bytes=fichier.stat().st_size,
                                      measured=measure(fichier) or None))
        return artefacts

    def _abandonner(self, job_id: str, etapes: list[dict[str, Any]], rang: int,
                    chaine: noyau.Chaine, produits: list[str]) -> None:
        store = self._c.store
        for reste in etapes[rang:]:
            if reste["statut"] == "running":
                # Celle-là avait commencé : le dire, sinon « sautée » laisse
                # croire que rien n'a été lancé alors que le moteur a travaillé.
                reste["note"] = "interrompue par l'arrêt demandé"
            if reste["statut"] in ("todo", "running"):
                reste["statut"] = "skipped"
        store.set_etapes(job_id, etapes)
        store.set_artifacts(job_id, self._artefacts(None, produits))
        store.append_log(job_id, "chaîne arrêtée à la demande — étapes restantes non faites")
        store.mark_failed(job_id, {
            "type": "https://cortex/problems/cancelled", "title": "Chaîne arrêtée",
            "status": 499, "problem_kind": "cancelled",
            "detail": f"chaîne {chaine.nom!r} arrêtée à la demande ; "
                      f"rien n'est retenu contre elle"})

    def _echouer(self, job_id: str, etapes: list[dict[str, Any]], rang: int,
                 chaine: noyau.Chaine, valeurs: dict[str, Any], exc: BridgeError,
                 produits: list[str]) -> None:
        from ..core.problems import classify
        store = self._c.store
        genre = (CONTROLE_ECHOUE if isinstance(exc, ChainControlFailedError)
                 else classify(exc.detail))
        etapes[rang]["statut"] = "failed"
        etapes[rang]["note"] = exc.detail[:300]
        for reste in etapes[rang + 1:]:
            reste["statut"] = "skipped"
        store.set_etapes(job_id, etapes)
        # Ce qui a DÉJÀ été écrit reste livré : c'est en le regardant qu'on
        # comprend pourquoi le contrôle a dit non.
        store.set_artifacts(job_id, self._artefacts(None, produits))
        store.append_log(job_id, f"étape {etapes[rang]['id']} en échec [{genre}] : {exc.detail}")
        probleme = to_problem(exc)
        probleme["problem_kind"] = genre
        probleme["etape"] = etapes[rang]["id"]
        store.mark_failed(job_id, probleme)
        self._c.registry.record(self._c.settings.host_id, chaine.nom, valeurs,
                                status="failed", problem=genre, detail=exc.detail)

    # -- étapes ----------------------------------------------------------------

    @staticmethod
    def _a_sauter(etape: noyau.Etape, valeurs: dict[str, Any],
                  resultats: dict[str, Any], chaine: noyau.Chaine | None = None,
                  technique=None) -> dict[str, Any] | None:
        """Ce qu'une étape SAUTÉE rend — ou None quand elle doit être jouée.

        Une étape porte « quand » : un renvoi vers un champ ou un résultat
        d'amont. Vide (texte blanc, faux, zéro, liste ou objet vides, absent),
        l'étape n'a rien à faire. Son résultat est alors un PASSE-PLAT : le
        premier média qu'elle devait reprendre devient son livrable, pour que
        l'aval la nomme sans savoir qu'elle n'a pas eu lieu.

        La raison NOMME les champs qui restent sans effet : ceux que cette
        étape seule lisait, et qu'on a pourtant réglés (une police d'appel sans
        appel partait nulle part sans le dire — 2026-09-19)."""
        if not etape.quand:
            return None
        valeur = noyau.resoudre(etape.quand, valeurs, resultats)
        if isinstance(valeur, str):
            pleine = bool(valeur.strip())
        else:
            pleine = bool(valeur)
        if pleine:
            return None
        raison = f"« {etape.quand} » est vide"
        sans_effet = (noyau.sans_effet_si_sautee(chaine, etape, technique, valeurs)
                      if chaine is not None else [])
        if sans_effet:
            raison += f" — {', '.join(sans_effet)} sans effet"
        resultat: dict[str, Any] = {"sautee": True, "raison": raison}
        if sans_effet:
            resultat["sans_effet"] = sans_effet
        params = etape.params if isinstance(etape.params, dict) else {}
        sources: list[Any] = []
        media = params.get("media")
        if isinstance(media, dict):
            sources += list(media.values())
        for cle in ("video", "image", "parts"):
            if cle in params:
                sources.append(params[cle])
        for source in sources:
            chemin = noyau.resoudre(source, valeurs, resultats, strict=False)
            if isinstance(chemin, list) and chemin:
                chemin = chemin[0]
            if isinstance(chemin, str) and chemin and Path(chemin).is_file():
                resultat["livrable"] = chemin
                resultat["mesure"] = {"bytes": Path(chemin).stat().st_size}
                break
        # « sinon » PAR-DESSUS le passe-plat : ce que la définition déclare
        # l'emporte sur ce qu'on a deviné. C'est ainsi qu'un appel sauté rend
        # « aucun livrable » et « 0 image reprise », que le montage lit sans
        # savoir que l'étape n'a pas eu lieu.
        resultat.update(etape.sinon or {})
        return resultat

    def _executer_etape(self, job_id: str, etape: noyau.Etape, valeurs: dict[str, Any],
                        resultats: dict[str, Any], travail: Path, label: str,
                        etapes: list[dict[str, Any]], rang: int,
                        chaine_nom: str = "") -> dict[str, Any]:
        technique = self._technique_de(etape, valeurs, resultats)
        if etape.genre in noyau.CONTROLENT:
            return self._verifier(etape, valeurs, resultats, technique,
                                  exiger=(etape.genre == "verifier"), job_id=job_id)
        brut = etape.params
        memoire: dict[str, Any] | None = None
        if etape.memoire is not None:
            # La clé se résout À PART, sans exiger : un renvoi absent vaut
            # null dans la clé, il ne fait pas échouer l'étape — et « memoire »
            # n'est pas un réglage du run.
            memoire = self._cle_de_memoire(etape, valeurs, resultats)
            brut = {k: v for k, v in brut.items() if k != "memoire"}
        params = noyau.resoudre(brut, valeurs, resultats)
        if etape.genre == "rendre":
            if etape.role is not None:
                params = self._params_du_role(job_id, etape, params, technique,
                                              valeurs, resultats)
            return self._rendre(job_id, etape, params, label, etapes, rang,
                                travail=travail, chaine_nom=chaine_nom, memoire=memoire)
        if etape.genre == "extraire_queue":
            source = self._fichier_local(params["video"], travail)
            sortie = travail / f"{etape.id}-queue.mp4"
            fait = montage_video.extraire_queue(source, int(params["images"]), sortie)
            return {**fait, "depot": self._deposer(Path(fait["fichier"]))}
        if etape.genre == "extraire_image":
            source = self._fichier_local(params["video"], travail)
            sortie = travail / f"{etape.id}-image.png"
            fait = montage_video.extraire_image(source, params.get("position", "last"), sortie)
            return {**fait, "depot": self._deposer(Path(fait["fichier"]))}
        if etape.genre == "recoller":
            parts = self._parts_locales(params["parts"], travail)
            sortie = self._sortie(job_id, label, etape.id, ".mp4", chaine=chaine_nom)
            return montage_video.recoller(parts, sortie, fps=int(params.get("fps") or 25),
                                          largeur=int(params.get("largeur") or 1280),
                                          hauteur=int(params.get("hauteur") or 720),
                                          chevauchement=int(params.get("chevauchement") or 0),
                                          signaler=lambda dit: self._c.store.append_log(
                                              job_id, f"étape {etape.id} : {dit}"),
                                          textes=params.get("textes"))
        if etape.genre == "mesurer_raccords":
            parts = self._parts_locales(params["parts"], travail)
            return montage_video.mesurer_raccords(
                parts, travail / etape.id, chevauchement=int(params.get("chevauchement") or 0),
                signaler=lambda dit: self._c.store.append_log(
                    job_id, f"étape {etape.id} : {dit}"))
        raise MediaAssemblyError(f"genre d'étape inconnu : {etape.genre!r}")

    # -- techniques -------------------------------------------------------------
    #
    # Une étape peut nommer un RÔLE (« deroulement ») au lieu d'un graphe, et
    # une étape « verifier » une LISTE de contrôles par son nom : c'est la
    # technique choisie à l'appel qui dit quel graphe tient ce rôle et ce que
    # cette liste contient. Le plan reste ainsi agnostique — sans quoi une
    # technique de plus était une chaîne de plus, recopiée (Antoine, 2026-09-16).

    def technique_voulue(self, chaine: noyau.Chaine, valeurs: dict[str, Any]):
        """La technique que CES valeurs emploient — connue dès l'acceptation, ce
        qui permet d'annoncer les graphes des rôles avant de rien lancer."""
        return noyau.technique_choisie(chaine, valeurs or {}, self._c.catalog.techniques())

    def _technique_de(self, etape: noyau.Etape, valeurs: dict[str, Any],
                      resultats: dict[str, Any]):
        """La technique qu'une étape emploie, résolue au moment de la jouer."""
        renvoi = etape.technique
        if renvoi is None:
            return None
        nom = noyau.resoudre(renvoi, valeurs, resultats)
        technique = self._c.catalog.technique(nom)
        if technique is None:
            connues = ", ".join(sorted(self._c.catalog.techniques())) or "aucune"
            raise MediaAssemblyError(
                f"étape {etape.id!r} : la technique {str(nom)!r} n'est pas déclarée "
                f"(déclarées : {connues})")
        return technique

    def _params_du_role(self, parent_id: str, etape: noyau.Etape, params: dict[str, Any],
                        technique, valeurs: dict[str, Any],
                        resultats: dict[str, Any]) -> dict[str, Any]:
        """Ce qu'une étape à rôle lance vraiment : le graphe de la technique, et
        ses entrées.

        Les entrées du RÔLE sont résolues à part — elles sont écrites chez la
        technique, personne ne les a encore vues — puis passent SOUS celles de
        l'étape : le plan garde le dernier mot sur ce qu'il a lui-même écrit, et
        une technique ne recouvre pas en silence ce que la chaîne demande.
        """
        role = technique.roles[etape.role]
        sortis = {k: v for k, v in params.items() if k not in ("role", "technique")}
        sortis["workflow"] = role.workflow
        sortis["inputs"] = noyau.entrees_du_role(role, params, valeurs, resultats)
        self._c.store.append_log(
            parent_id, f"étape {etape.id} : technique {technique.nom} → {role.workflow}")
        return sortis

    def _verifier(self, etape: noyau.Etape, valeurs: dict[str, Any],
                  resultats: dict[str, Any], technique=None, exiger: bool = True,
                  job_id: str = "") -> dict[str, Any]:
        # Les contrôles de la CHAÎNE, puis ceux que la TECHNIQUE choisie porte
        # sous le nom demandé : deux peintures ne se jugent pas sur les mêmes
        # grandeurs, et la chaîne n'a pas à porter les deux — mais le plan se
        # juge AVANT de peindre aussi sur ce que la technique exige de lui
        # (mesuré le 2026-09-17 : un plan à un tracé sur six a coûté trente-deux
        # minutes de peinture avant d'être refusé par l'encre).
        controles = list(etape.controles_propres)
        if etape.controles_nommes is not None:
            controles += list(technique.controles[etape.controles_nommes])
        lignes = noyau.controler(controles, valeurs, resultats)
        faux = [l for l in lignes if not l["ok"]]
        if faux:
            # Le refus dit ce qu'il mesure ET quoi faire, quand le contrôle
            # porte une aide (2026-09-19 : deux plans refusés sur une mesure
            # et un seuil, sans un mot de plus).
            dit = " ; ".join(f"{l['id']} : mesuré {l['mesure']!r}, attendu "
                             f"{l['op']} {l['attendu']!r}"
                             + (f" — {l['aide']}" if l.get("aide") else "") for l in faux)
            if exiger:
                raise ChainControlFailedError(f"contrôle non tenu — {dit}", controles=lignes)
            # UN CONSTAT N'ARRÊTE RIEN. Antoine, 2026-09-17 : « il ne faut plus que
            # maestro annonce des erreurs quand la vidéo est très bien, c'est
            # l'utilisateur qui juge ». Les mesures restent écrites, au récit et
            # au journal ; la chaîne livre, et le regard tranche.
            if job_id:
                self._c.store.append_log(job_id, f"étape {etape.id} : constaté, non tenu — {dit}")
        return {"controles": lignes, "constat": not exiger,
                "non_tenus": [l["id"] for l in faux]}

    # -- mémoire d'étape --------------------------------------------------------
    #
    # Une étape « rendre » qui déclare « memoire » est GARDÉE PAR CLÉ : le
    # sha256 des valeurs que sa définition nomme (l'empreinte de l'image, les
    # réglages du plan, la graine). La clé revient : le résultat est repris —
    # récit, mesure, et le livrable recopié dans le dossier de sortie sous un
    # nom neuf — sans un run. Décidé le 2026-09-19 : « le plan est gardé par
    # clé (même image, réglages, graine → même plan) ». Le code ne sait ni ce
    # que la clé nomme ni ce que l'étape écrit : c'est la définition qui le
    # dit. La mémoire vit dans le dossier de données, jamais parmi les
    # livrables.

    def _cle_de_memoire(self, etape: noyau.Etape, valeurs: dict[str, Any],
                        resultats: dict[str, Any]) -> dict[str, Any]:
        """La clé de mémoire d'une étape : les renvois de sa définition,
        résolus sans exiger (un renvoi absent vaut null — il pèse dans la clé
        comme « rien »), en JSON canonique, hachés."""
        resolus = []
        for renvoi in etape.memoire or ():
            valeur = noyau.resoudre(renvoi, valeurs, resultats, strict=False)
            resolus.append(None if isinstance(valeur, str) and valeur.startswith("$")
                           else valeur)
        canon = json.dumps(resolus, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
        return {"cle": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
                "renvois": list(etape.memoire or ())}

    def _dossier_de_memoire(self, chaine_nom: str, etape_id: str) -> Path:
        surete = lambda s: "".join(ch for ch in str(s) if ch.isalnum() or ch in "-_") or "sans-nom"
        return (Path(self._c.settings.hermes_db).parent / "memoire"
                / surete(chaine_nom) / surete(etape_id))

    def _souvenir(self, chaine_nom: str, etape_id: str, cle: str) -> dict[str, Any] | None:
        """Ce que la mémoire garde sous cette clé — avec son livrable encore
        là ; sinon rien, et l'étape se joue (une fiche sans fichier est un
        souvenir qui ne sert plus)."""
        fiche = self._dossier_de_memoire(chaine_nom, etape_id) / f"{cle}.json"
        if not fiche.is_file():
            return None
        try:
            souvenir = json.loads(fiche.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        livrable = souvenir.get("livrable") if isinstance(souvenir, dict) else None
        if not isinstance(livrable, str) or not Path(livrable).is_file():
            return None
        return souvenir

    def _retenir(self, parent_id: str, etape: noyau.Etape, chaine_nom: str,
                 memoire: dict[str, Any], resultat: dict[str, Any]) -> None:
        """Déposer le résultat d'une étape sous sa clé : sa fiche (récit,
        mesure, d'où il vient) et une copie de son livrable, à côté."""
        livrable = resultat.get("livrable")
        if not isinstance(livrable, str) or not Path(livrable).is_file():
            return
        dossier = self._dossier_de_memoire(chaine_nom, etape.id)
        dossier.mkdir(parents=True, exist_ok=True)
        copie = dossier / f"{memoire['cle']}.livrable{Path(livrable).suffix}"
        shutil.copyfile(livrable, copie)
        fiche = {"cle": memoire["cle"], "renvois": memoire["renvois"],
                 "chaine": chaine_nom, "etape": etape.id,
                 "ecrit_le": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                 "job_id": resultat.get("job_id"), "livrable": str(copie),
                 "resultat": {k: resultat[k] for k in ("recit", "mesure") if k in resultat}}
        brouillon = dossier / f".{memoire['cle']}.part.json"
        brouillon.write_text(json.dumps(fiche, ensure_ascii=False), encoding="utf-8")
        brouillon.replace(dossier / f"{memoire['cle']}.json")
        resultat["memoire"] = {"cle": memoire["cle"], "reprise": False}
        self._c.store.append_log(
            parent_id, f"étape {etape.id} : résultat gardé en mémoire sous sa clé "
                       f"({', '.join(memoire['renvois'])})")

    def _reprendre(self, parent_id: str, etape: noyau.Etape, souvenir: dict[str, Any],
                   label: str, chaine_nom: str, memoire: dict[str, Any]) -> dict[str, Any]:
        """Le résultat repris de la mémoire : le livrable recopié dans le
        dossier de sortie sous un nom neuf (celui que ce run lui aurait donné),
        le récit et la mesure tels qu'ils ont été gardés — aucun run."""
        source = Path(str(souvenir["livrable"]))
        sortie = self._sortie(parent_id, label, etape.id, source.suffix, chaine=chaine_nom)
        shutil.copyfile(source, sortie)
        garde = souvenir.get("resultat") if isinstance(souvenir.get("resultat"), dict) else {}
        resultat: dict[str, Any] = {
            "livrable": str(sortie), "job_id": None, "artefacts": [str(sortie)],
            "mesure": {**(garde.get("mesure") or {}), "bytes": sortie.stat().st_size},
            "memoire": {"cle": memoire["cle"], "reprise": True,
                        "job_id": souvenir.get("job_id"), "ecrit_le": souvenir.get("ecrit_le")},
            "_duree": 0.0,
            "_note": "reprise de la mémoire : même clé",
        }
        if isinstance(garde.get("recit"), dict):
            resultat["recit"] = garde["recit"]
        self._c.store.append_log(
            parent_id, f"étape {etape.id} reprise : même clé "
                       f"({', '.join(memoire['renvois'])}) — le résultat du job "
                       f"{souvenir.get('job_id')} du {souvenir.get('ecrit_le')} est recopié, "
                       f"aucun run")
        return resultat

    def _rendre(self, parent_id: str, etape: noyau.Etape, params: dict[str, Any],
                label: str, etapes: list[dict[str, Any]], rang: int,
                travail: Path | None = None, chaine_nom: str = "",
                memoire: dict[str, Any] | None = None) -> dict[str, Any]:
        if memoire is not None:
            souvenir = self._souvenir(chaine_nom, etape.id, memoire["cle"])
            if souvenir is not None:
                return self._reprendre(parent_id, etape, souvenir, label, chaine_nom, memoire)
        resultat = self._rendre_sans_memoire(parent_id, etape, params, label, etapes, rang,
                                             travail, chaine_nom)
        if memoire is not None:
            self._retenir(parent_id, etape, chaine_nom, memoire, resultat)
        return resultat

    def _rendre_sans_memoire(self, parent_id: str, etape: noyau.Etape, params: dict[str, Any],
                             label: str, etapes: list[dict[str, Any]], rang: int,
                             travail: Path | None = None, chaine_nom: str = "") -> dict[str, Any]:
        c = self._c
        reglages = dict(params)
        nom = str(reglages.pop("workflow"))
        media = {k: str(v) for k, v in (reglages.pop("media", None) or {}).items() if v}
        # UNE PIÈCE JOINTE AU REPOS NE PART PAS DANS UN NŒUD. Un champ média
        # facultatif laissé vide vaut None (core/chaine.py::valeurs) ; une
        # entrée de nœud qui le renvoie (« 7.image_2_nom »: « $image_2 » — c'est
        # ainsi qu'un nœud SAIT qu'un élément est absent, sans deviner sur les
        # pixels de l'image neutre) écrirait null dans le graphe. Elle est
        # retirée, le littéral du graphe reste, et c'est dit (2026-09-20).
        entrees = reglages.get("inputs")
        if isinstance(entrees, dict):
            au_repos = sorted(k for k, v in entrees.items() if v is None)
            if au_repos:
                reglages["inputs"] = {k: v for k, v in entrees.items() if v is not None}
                c.store.append_log(
                    parent_id, f"étape {etape.id} : {', '.join('« %s »' % k for k in au_repos)} — "
                               f"renvoi sans valeur (pièce jointe absente), le littéral du graphe reste")
        for cle, valeur in media.items():
            fichier = Path(valeur)
            if fichier.is_file():
                # Le livrable d'une étape précédente est un CHEMIN local : un
                # « rendre » qui le reprend en média doit le déposer chez le
                # moteur et citer le nom rendu, comme `extraire_queue` et
                # `extraire_image` le font déjà pour ce qu'ils produisent —
                # mesuré (run 98d75906), sans quoi le moteur refusait le
                # graphe en 40 ms.
                depose = self._deposer(fichier)
                c.store.append_log(
                    parent_id,
                    f"étape {etape.id} : livrable {fichier.name} déposé chez "
                    f"le moteur sous « {depose} »")
                media[cle] = depose
        reglages.pop("label", None)
        reglages.pop("constraints", None)
        genre = reglages.pop("kind", None)
        # Un montage à un run par tour se rend ainsi, point : ses runs ne se
        # montent qu'un tour à la fois (ce qu'un run relit vient du relais du
        # précédent), et le monter d'un seul tenant pour le trancher échouait
        # en le disant — « pas de tranches (… n'est ni une sortie déclarée …) »
        # dans le journal de chaque demande (vu le 2026-09-18).
        tours = self._tours_separes(parent_id, etape, nom, reglages)
        if tours is not None:
            return self._rendre_par_tours(parent_id, etape, nom, media, genre, reglages,
                                          label, etapes, rang, tours, travail, chaine_nom)
        tranches = self._tranches_de(parent_id, etape, nom, reglages, media)
        if tranches is not None:
            return self._rendre_par_tranches(parent_id, etape, nom, media, genre, reglages,
                                             label, etapes, rang, tranches, travail, chaine_nom)
        fini = self._executer_run(parent_id, etape, nom, media, genre, reglages,
                                  etiquette_de_run(label, etape.id), etapes, rang)
        return self._resultat_du_run(parent_id, etape, nom, fini)

    def _executer_run(self, parent_id: str, etape: noyau.Etape, nom: str,
                      media: dict[str, str], genre: Any, reglages: dict[str, Any],
                      etiquette: str, etapes: list[dict[str, Any]], rang: int):
        """UN run ordinaire pour cette étape, conduit jusqu'à sa fin — et refusé
        s'il ne réussit pas. Le sous-job est visible dans /v1/jobs comme les
        autres ; le parent ne fait qu'attendre et relayer."""
        c = self._c
        intention = RenderIntent(workflow=nom, media=media,
                                 kind=MediaKind(genre) if genre else None,
                                 label=etiquette, **reglages)
        sous, plan = c.orchestrator.accept(intention, force=self._force)
        c.store.set_parent(sous.id, parent_id)
        etapes[rang]["job_id"] = sous.id
        c.store.set_etapes(parent_id, etapes)
        c.store.append_log(parent_id, f"étape {etape.id} : run {nom} → job {sous.id}")

        arret = threading.Event()
        veille = threading.Thread(target=self._veiller, daemon=True,
                                  args=(parent_id, sous.id, etape.id, rang,
                                        len(etapes), arret))
        veille.start()
        try:
            c.orchestrator.execute(sous.id, plan)
        finally:
            arret.set()
            veille.join(timeout=2.0)

        fini = c.store.get(sous.id)
        if fini.status is not JobStatus.SUCCEEDED:
            detail = (fini.problem or {}).get("detail") or f"le run a fini {fini.status.value}"
            raise MediaAssemblyError(f"étape {etape.id!r} ({nom}) : {detail}",
                                     job_id=sous.id, workflow=nom)
        return fini

    def _resultat_du_run(self, parent_id: str, etape: noyau.Etape, nom: str,
                         fini) -> dict[str, Any]:
        livrable = _principal(fini.artifacts)
        if livrable is None:
            raise MediaAssemblyError(
                f"étape {etape.id!r} ({nom}) : le run a réussi sans livrer de média",
                job_id=fini.id, workflow=nom)
        resultat = {"livrable": livrable.path, "job_id": fini.id,
                    "mesure": {**(livrable.measured or {}), "bytes": livrable.bytes},
                    "artefacts": [a.path for a in fini.artifacts],
                    "_duree": fini.duration_s}
        recit = _recit(fini.artifacts, lambda raison: self._c.store.append_log(
            parent_id, f"étape {etape.id} : récit illisible : {raison}"))
        if recit is not None:
            resultat["recit"] = recit
        return resultat

    # -- rendu par tranches -----------------------------------------------------
    #
    # Un nœud qui tient toute une vidéo en mémoire (une image RVB float32 par
    # image rendue) dépasse la mémoire du poste dès qu'un plan s'allonge :
    # mesuré le 2026-09-15, 2 258 images en 720×1280 demandaient 23,3 Gio d'un
    # coup, refusés. Ce n'est pas au demandeur de raccourcir sa vidéo ni de la
    # rendre plus petite : la passerelle demande le rendu par TRANCHES d'une
    # seule et même simulation, l'une après l'autre, et les recolle par copie de
    # flux — aucune image n'est ré-encodée, la jonction est celle des images
    # elles-mêmes. Un nœud qui sait le faire le déclare par deux entrées,
    # `segment_index` et `segment_count` (la tranche i de n rend les images
    # [n·i/N, n·(i+1)/N) de la même simulation, au grain et à la lumière près
    # de la seconde ABSOLUE), et par une borne d'allonge — `allonge_max_s`, de
    # combien au plus il allonge la durée demandée, ou `duree_max_s`, la durée
    # au-delà de laquelle il n'allonge plus : c'est elle qui borne le nombre de
    # tranches, puisque le nœud décide lui-même de la durée retenue.

    def tranches_pour(self, nom: str, reglages: dict[str, Any],
                      etiquette: str = "rendu",
                      media: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Le découpage qu'un rendu DIRECT de ce graphe demanderait — la même
        règle que pour une étape de chaîne, pour n'importe quel appelant (un
        mode de maestro de n'importe quelle catégorie, un appel d'API, un
        rejeu) : le mécanisme ne connaît que le graphe et son budget."""
        return self._tranches_de(None, noyau.Etape(id=etiquette, genre="rendre", params={}),
                                 nom, reglages, media)

    def _tranches_de(self, parent_id: str | None, etape: noyau.Etape, nom: str,
                     reglages: dict[str, Any],
                     media: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Combien de tranches il faut pour que ce run tienne dans le budget —
        ou None quand il tient d'un seul tenant, ou que le nœud ne sait pas
        trancher (et alors le run part entier, comme avant)."""
        c = self._c
        budget, provenance = budget_de_tranche(c)
        if budget <= 0:
            return None
        try:
            spec = c.catalog.get_spec(nom)
            if spec.est_chaine:
                return None
            graphe, _liaisons = c.catalog.monter(spec, reglages)
        except Exception as exc:                    # noqa: BLE001
            # repli: un graphe qui ne se lit pas ici sera refusé au run, qui le
            # dira ; on ne tranche pas ce qu'on ne sait pas lire — et on le dit.
            if parent_id is not None:
                c.store.append_log(parent_id, f"étape {etape.id} : pas de tranches ({exc})")
            return None
        noeud = noeud_de_tranches(graphe)
        if noeud is None:
            return None
        defauts = dict(spec.defaults or {})

        def nombre(cle: str) -> float:
            try:
                return float(reglages.get(cle, defauts.get(cle)) or 0)
            except (TypeError, ValueError):
                return 0.0

        largeur, hauteur, fps = nombre("width"), nombre("height"), nombre("fps")
        if largeur <= 0 or hauteur <= 0 or fps <= 0:
            # Un graphe qui prend sa taille (ou sa cadence) d'une VIDÉO
            # D'ENTRÉE ne la porte pas dans ses réglages — une conclusion
            # reprend la queue du déroulement telle qu'elle est. Elle se lit
            # alors sur la vidéo elle-même, avant le run.
            lu = self._mesure_du_media(media)
            if lu:
                largeur = largeur if largeur > 0 else float(lu.get("width") or 0)
                hauteur = hauteur if hauteur > 0 else float(lu.get("height") or 0)
                fps = fps if fps > 0 else float(lu.get("fps") or 0)
                if parent_id is not None and largeur > 0 and hauteur > 0 and fps > 0:
                    c.store.append_log(
                        parent_id,
                        f"étape {etape.id} : taille lue sur la vidéo d'entrée "
                        f"« {lu['nom']} » — {int(largeur)}×{int(hauteur)} à {fps:g} i/s")
        if largeur <= 0 or hauteur <= 0 or fps <= 0:
            if parent_id is not None:
                c.store.append_log(parent_id, f"étape {etape.id} : pas de tranches — largeur, "
                                              f"hauteur ou cadence inconnues avant le run")
            return None

        def declare(cle: str) -> float:
            valeur = (graphe.get(noeud) or {}).get("inputs", {}).get(cle)
            try:
                return 0.0 if isinstance(valeur, list) else float(valeur or 0)
            except (TypeError, ValueError):
                return 0.0

        # Deux bornes qu'un nœud déclare, en littéral : `duree_max_s`, la durée
        # ABSOLUE au-delà de laquelle il n'allonge plus ; `allonge_max_s`, de
        # combien AU PLUS il allonge au-delà de la durée demandée. Quand le
        # nœud déclare une ALLONGE et qu'une durée est demandée, c'est elle
        # qui compte : la demande fait loi, l'allonge est sa marge — le
        # plafond ne sert que sans allonge déclarée (ou sans demande). Mesuré
        # le 2026-09-19 : compté sur max(demandée, 79), le compte des tranches
        # de 10 s demandées était celui de 79 s, et la demande n'y pesait pas.
        # Jamais moins d'images que le nœud n'en rendra.
        plafond, allonge = declare("duree_max_s"), declare("allonge_max_s")
        demandee = nombre("duration_s")
        if allonge > 0 and demandee > 0:
            duree = demandee + allonge
        else:
            duree = max(demandee, plafond)
        if duree <= 0:
            return None
        images = int(math.ceil(duree * fps))
        poids = int(largeur) * int(hauteur) * OCTETS_PAR_IMAGE
        # LA MARGE DU MOMENT. Le budget est déclaré ; mais si le poste garde en
        # ce moment plus que le fichier ne dit (un voisin a chargé un modèle de
        # 26 Go — mesuré), des tranches au budget déclaré n'auraient pas leur
        # place : on les taille sur ce qui est libre MAINTENANT, en le disant.
        # Plus de tranches, jamais plus longues ; et si même ainsi ça ne tient
        # pas, c'est dit avant le premier run.
        mesure = materiel.mesure_du_poste()
        marge = materiel.marge_du_moment(mesure)
        facteur = self._facteur_de_crete()
        budget_declare = budget
        du_moment: str | None = None
        if marge is not None and int(marge / facteur) < budget:
            budget = max(1, int(marge / facteur))
            du_moment = (f"ramené à {budget / 2 ** 30:.1f} Gio par la marge du moment "
                         f"({materiel.dire_la_marge(mesure)}, facteur {facteur:g})")
            if parent_id is not None:
                c.store.append_log(parent_id, f"étape {etape.id} : budget {du_moment}")
        # Le compte est celui qui garantit qu'AUCUNE tranche ne dépasse le
        # budget, pas même d'une image : la plus longue a ceil(images / N)
        # images, et c'est elle que la garde de place mesure ensuite. Un compte
        # pris sur le poids total (ceil(images × poids / budget)) laissait la
        # dernière image d'une tranche déborder — mesuré : 84 images à 2 de
        # crête, 15,6 Gio attendus pour 15,5 de marge, 30 s d'attente pour rien.
        def compte(pour: int) -> int:
            return int(math.ceil(images / max(1, pour // poids)))

        n = compte(budget)
        if n <= 1:
            return None
        if n > TRANCHES_MAX:
            if du_moment and compte(budget_declare) <= TRANCHES_MAX:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} ({nom}) : il faudrait {n} tranches pour tenir "
                    f"{images} images de {int(largeur)}×{int(hauteur)} dans la marge du "
                    f"moment ({materiel.dire_la_marge(mesure)}), plus que les {TRANCHES_MAX} "
                    f"qu'un nœud accepte — le poste garde en ce moment plus que ce que "
                    f"{materiel.FICHIER} déclare : libérer la mémoire, puis relancer",
                    workflow=nom)
            raise MediaAssemblyError(
                f"étape {etape.id!r} ({nom}) : il faudrait {n} tranches pour tenir "
                f"{images} images de {int(largeur)}×{int(hauteur)} dans "
                f"{budget / 2 ** 30:.1f} Gio, plus que les {TRANCHES_MAX} qu'un nœud "
                f"accepte — baisser la résolution ou la durée", workflow=nom)
        return {"noeud": noeud, "nombre": n, "images": images, "largeur": int(largeur),
                "hauteur": int(hauteur), "fps": int(fps), "poids": poids, "budget": budget,
                "budget_declare": budget_declare, "du_moment": du_moment,
                "facteur": facteur, "provenance": provenance}

    def _pic_attendu(self, tranches: dict[str, Any], n: int,
                     par_tranche: int | None) -> tuple[int, int, float]:
        """Le pic de mémoire qu'une tranche demande : ses images (la borne
        d'avant le run, ou ce qu'une tranche rend vraiment) × leur poids × le
        facteur de crête. Rend (besoin, images, facteur)."""
        facteur = float(tranches.get("facteur") or self._facteur_de_crete())
        if par_tranche is None:
            par_tranche = int(math.ceil(tranches["images"] / n))
        return int(par_tranche * tranches["poids"] * facteur), par_tranche, facteur

    def _manque_de_place(self, tranches: dict[str, Any], n: int,
                         par_tranche: int | None) -> tuple[int, int] | None:
        """(marge, besoin) quand la marge du moment ne tient pas le pic attendu ;
        ``None`` quand elle le tient — ou qu'on ne sait pas la mesurer."""
        marge = materiel.marge_du_moment(materiel.mesure_du_poste())
        besoin, _images, _facteur = self._pic_attendu(tranches, n, par_tranche)
        if marge is None or marge >= besoin:
            return None
        return marge, besoin

    def _facteur_de_crete(self) -> float:
        """Le facteur de crête déclaré du poste — 1 quand rien n'est déclaré (une
        surcharge d'essai, un poste sans fichier) : le pic attendu est alors le
        poids nu des images, ce qu'on sait de plus honnête."""
        memoire = (self._c.materiel or {}).get("memoire") or {}
        try:
            return max(1.0, float(memoire.get("facteur_de_crete") or 1.0))
        except (TypeError, ValueError):
            return 1.0

    def _attendre_la_place(self, parent_id: str, etape: noyau.Etape, nom: str,
                           i: int, n: int, tranches: dict[str, Any],
                           par_tranche: int | None = None) -> None:
        """Avant une tranche, la MARGE DU MOMENT du poste doit tenir le pic
        attendu : le poids d'une tranche × le facteur de crête déclaré.

        Le fichier matériel dit ce que le poste garde d'ordinaire ; il ne peut
        pas dire qu'un voisin chargera un modèle de 26 Go au milieu d'un rendu
        de cinq heures (mesuré le 2026-09-16 : la tranche 15/31 d'un 4K a
        échoué à allouer 8,6 Gio avec 18 Gio de RAM physique libre — c'est la
        limite de COMMIT de Windows qui était atteinte). Plutôt que d'échouer
        après deux heures de rendu, on attend que la place revienne, en le
        disant, jusqu'à la borne — puis on refuse, en disant pourquoi.

        `par_tranche` : les images qu'une tranche rend VRAIMENT, sues dès que
        la première est rendue — le compte d'avant le run est une borne (le
        nœud peut retenir 48 s là où il en déclare 79), pas une mesure.
        """
        c = self._c
        store = c.store
        memoire = (c.materiel or {}).get("memoire") or {}
        besoin, par_tranche, facteur = self._pic_attendu(tranches, n, par_tranche)
        limite_s = float(getattr(c.settings, "attente_place_s", 0) or 0)
        pas_s = float(getattr(c.settings, "attente_place_pas_s", 30) or 0)
        debut = time.monotonic()
        dit = False
        while True:
            mesure = materiel.mesure_du_poste()
            marge = materiel.marge_du_moment(mesure)
            if marge is None or marge >= besoin:
                if dit:
                    store.append_log(
                        parent_id,
                        f"étape {etape.id} : la place est revenue pour la tranche {i + 1}/{n} "
                        f"({marge / 2 ** 30:.1f} Gio de marge) après "
                        f"{time.monotonic() - debut:.0f} s")
                return
            garde = int((mesure or {}).get("totale_octets") or 0) - marge
            reservee = int(memoire.get("reservee_octets") or 0)
            if not dit:
                store.append_log(
                    parent_id,
                    f"étape {etape.id} : tranche {i + 1}/{n} — le poste n'a que "
                    f"{marge / 2 ** 30:.1f} Gio de marge ({materiel.dire_la_marge(mesure)}) "
                    f"pour un pic attendu de {besoin / 2 ** 30:.1f} Gio "
                    f"({par_tranche} images × {facteur:g} de crête) ; il garde en ce moment "
                    f"{garde / 2 ** 30:.0f} Go, plus que les {reservee / 2 ** 30:.0f} Go "
                    f"déclarés réservés — attente, jusqu'à {limite_s / 60:.0f} min")
                dit = True
            ecoule = time.monotonic() - debut
            if ecoule >= limite_s:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} ({nom}) : la tranche {i + 1}/{n} n'a pas trouvé sa "
                    f"place en {limite_s / 60:.0f} min — le poste garde en ce moment "
                    f"{garde / 2 ** 30:.0f} Go, plus que les {reservee / 2 ** 30:.0f} Go déclarés "
                    f"réservés ({materiel.FICHIER}) ; libérer la mémoire, ou déclarer ce que le "
                    f"poste garde vraiment", workflow=nom)
            if store.get(parent_id).cancel_requested:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} : arrêt demandé pendant l'attente de la tranche "
                    f"{i + 1}/{n}")
            time.sleep(max(0.0, min(pas_s, limite_s - ecoule)))

    def _rendre_par_tranches(self, parent_id: str, etape: noyau.Etape, nom: str,
                             media: dict[str, str], genre: Any, reglages: dict[str, Any],
                             label: str, etapes: list[dict[str, Any]], rang: int,
                             tranches: dict[str, Any], travail: Path | None,
                             chaine_nom: str) -> dict[str, Any]:
        c = self._c
        store = c.store
        n, noeud = tranches["nombre"], tranches["noeud"]
        store.append_log(
            parent_id,
            f"étape {etape.id} : rendu en {n} tranches — jusqu'à {tranches['images']} images "
            f"de {tranches['largeur']}×{tranches['hauteur']} "
            f"({tranches['images'] * tranches['poids'] / 2 ** 30:.1f} Gio en mémoire d'un seul "
            f"tenant) ; "
            + materiel.dire(tranches.get("budget_declare", tranches["budget"]),
                            tranches.get("provenance", ""))
            + (f", {tranches['du_moment']}" if tranches.get("du_moment") else ""))
        parts: list[str] = []
        recits: list[dict[str, Any]] = []
        sous_ids: list[str] = []
        artefacts: list[str] = []
        duree = 0.0
        # Les images qu'une tranche rend VRAIMENT, sues dès la première : c'est
        # sur elles que les tranches suivantes attendent leur place, pas sur la
        # borne d'avant le run.
        reel: int | None = None
        for i in range(n):
            if store.get(parent_id).cancel_requested:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} : arrêt demandé avant la tranche {i + 1}/{n}")
            etapes[rang]["note"] = f"tranche {i + 1}/{n}"
            store.set_etapes(parent_id, etapes)
            self._attendre_la_place(parent_id, etape, nom, i, n, tranches, par_tranche=reel)
            entrees = dict(reglages.get("inputs") or {})
            entrees[f"{noeud}.segment_index"] = i
            entrees[f"{noeud}.segment_count"] = n
            essais = 0
            while True:
                try:
                    fini = self._executer_run(parent_id, etape, nom, media, genre,
                                              {**reglages, "inputs": entrees},
                                              etiquette_de_run(label, etape.id, f"{i + 1}sur{n}"),
                                              etapes, rang)
                    break
                except MediaAssemblyError as exc:
                    # Une tranche qui échoue ALORS QUE la place manque a
                    # probablement échoué de ça : le poste a changé entre la
                    # garde et l'allocation (mesuré le 2026-09-16 : un modèle de
                    # 21 Go chargé pendant le rendu). On attend la place et on
                    # reprend, en le disant — jamais plus de REPRISES_MAX fois,
                    # et jamais quand la place ne manquait pas : cet échec-là
                    # est celui du nœud, et se dit tel quel.
                    essais += 1
                    manque = self._manque_de_place(tranches, n, reel)
                    if essais > REPRISES_MAX or manque is None:
                        raise
                    marge, besoin = manque
                    store.append_log(
                        parent_id,
                        f"étape {etape.id} : la tranche {i + 1}/{n} a échoué ({exc.detail}) "
                        f"alors que le poste n'avait que {marge / 2 ** 30:.1f} Gio de marge "
                        f"pour un pic attendu de {besoin / 2 ** 30:.1f} Gio — reprise après "
                        f"attente (essai {essais}/{REPRISES_MAX})")
                    self._attendre_la_place(parent_id, etape, nom, i, n, tranches,
                                            par_tranche=reel)
            livrable = _principal(fini.artifacts)
            if livrable is None or livrable.kind != "video":
                raise MediaAssemblyError(
                    f"étape {etape.id!r} ({nom}) : la tranche {i + 1}/{n} n'a pas livré de vidéo",
                    job_id=fini.id, workflow=nom)
            parts.append(livrable.path)
            sous_ids.append(fini.id)
            reel = max(reel or 0, _images_rendues(livrable)) or None
            # Posés à CHAQUE tranche, pas à la fin : une chaîne qui échoue à la
            # tranche 2/3 doit encore dire quelles tranches sont rendues, et où.
            etapes[rang]["job_ids"] = list(sous_ids)
            etapes[rang]["tranches"] = n
            artefacts += [a.path for a in fini.artifacts]
            duree += fini.duration_s or 0.0
            recit = _recit(fini.artifacts, lambda raison, i=i: store.append_log(
                parent_id, f"étape {etape.id} : récit de la tranche {i + 1} illisible : {raison}"))
            if recit is not None:
                recits.append(recit)
        etapes[rang]["note"] = f"{n} tranches"
        store.set_etapes(parent_id, etapes)

        sortie = self._sortie(parent_id, label, etape.id, ".mp4", chaine=chaine_nom)
        try:
            fait = montage_video.concatener(parts, sortie)
        except MediaAssemblyError as exc:
            # repli: des tranches que la copie de flux ne sait pas joindre (un
            # encodeur ou des réglages qui diffèrent) sont recollées en
            # ré-encodant — une génération de perte de plus, jamais silencieuse.
            store.append_log(parent_id, f"étape {etape.id} : copie de flux impossible "
                                        f"({exc.detail}) — recollage ré-encodé")
            fait = montage_video.recoller(parts, sortie, fps=tranches["fps"],
                                          largeur=tranches["largeur"], hauteur=tranches["hauteur"])
        jonctions: dict[str, Any] | None = None
        if travail is not None:
            try:
                mesure = montage_video.mesurer_raccords(parts, Path(travail) / f"{etape.id}-jonctions")
                jonctions = {"pire": mesure["pire"], "moyenne": mesure["moyenne"],
                             "nombre": mesure["nombre"]}
            except MediaAssemblyError as exc:
                # repli: une jonction qui ne se mesure pas n'invalide pas le
                # livrable — elle se dit au journal.
                store.append_log(parent_id, f"étape {etape.id} : jonctions non mesurées "
                                            f"({exc.detail})")
        comment = "sans ré-encodage" if fait.get("reencode") is False else "en ré-encodant"
        store.append_log(
            parent_id,
            f"étape {etape.id} : {n} tranches recollées {comment}"
            + (f" — jonctions mesurées : pire {jonctions['pire']:.4f}, "
               f"moyenne {jonctions['moyenne']:.4f}" if jonctions else ""))
        chemin = Path(fait["livrable"])
        resultat: dict[str, Any] = {
            "livrable": str(chemin), "job_id": sous_ids[-1], "job_ids": sous_ids, "tranches": n,
            "mesure": {**(fait.get("mesure") or {}), "bytes": chemin.stat().st_size},
            "artefacts": artefacts, "_duree": duree}
        if jonctions is not None:
            resultat["jonctions"] = jonctions
        if recits:
            # Le compte est celui des tranches RENDUES, posé ici : la fusion ne
            # connaît que les récits lisibles, et un récit illisible sur trois
            # aurait fait dire « 2 tranches » à un contrôle qui en attend 3.
            resultat["recit"] = {**fusionner_recits(recits), "tranches": n,
                                 "tranches_au_recit": len(recits)}
        return resultat

    # -- un run par tour --------------------------------------------------------
    #
    # Un montage à blocs de boucle envoyé ENTIER au moteur tient tous ses blocs
    # dans un seul prompt : les modèles y restent en mémoire jusqu'au dernier
    # nœud (le moteur retient chaque modèle tant que le prompt court), et la
    # passerelle n'a aucun point de contrôle entre deux blocs — mesuré le
    # 2026-09-18 : un rendu de trois blocs a conduit le poste au bout de sa
    # limite de commit et le moteur est mort sans une ligne. Une boucle qui
    # déclare « un_run_par_tour » (voir core.blocs) est rendue un tour par run :
    # la garde de place joue avant chacun, comme pour une tranche, et ce qui
    # passe d'un tour au suivant (le relais : la dernière image, le plus
    # souvent) est écrit par le run, déposé chez le moteur, relu par le suivant.

    def _tours_separes(self, parent_id: str, etape: noyau.Etape, nom: str,
                       reglages: dict[str, Any]) -> int | None:
        """Combien de runs ce montage demande (un par tour), ou None."""
        c = self._c
        try:
            spec = c.catalog.get_spec(nom)
            return c.catalog.tours_separes(spec, reglages)
        except Exception as exc:                    # noqa: BLE001
            # repli: un montage qui ne se lit pas ici sera refusé au run, qui le
            # dira ; on ne découpe pas ce qu'on ne sait pas lire — et on le dit.
            c.store.append_log(parent_id, f"étape {etape.id} : pas de runs par tour ({exc})")
            return None

    def _pic_d_un_tour(self, nom: str, reglages: dict[str, Any], n: int) -> dict[str, Any] | None:
        """Ce qu'un tour pèse, pour la garde de place : les images livrées du
        rendu entier réparties sur les tours, au poids d'une image de sortie
        — la même mesure que pour une tranche. None quand la taille, la
        cadence ou la durée ne se lisent pas avant le run (pas de garde)."""
        c = self._c
        try:
            defauts = dict(c.catalog.get_spec(nom).defaults or {})
        except Exception:                           # noqa: BLE001
            defauts = {}

        def nombre(cle: str) -> float:
            try:
                return float(reglages.get(cle, defauts.get(cle)) or 0)
            except (TypeError, ValueError):
                return 0.0

        largeur, hauteur, fps, duree = (nombre("width"), nombre("height"), nombre("fps"),
                                        nombre("duration_s"))
        if largeur <= 0 or hauteur <= 0 or fps <= 0 or duree <= 0:
            return None
        images = int(math.ceil(duree * fps))
        return {"nombre": n, "images": images, "largeur": int(largeur), "hauteur": int(hauteur),
                "fps": int(fps), "poids": int(largeur) * int(hauteur) * OCTETS_PAR_IMAGE,
                "facteur": self._facteur_de_crete()}

    def _rendre_par_tours(self, parent_id: str, etape: noyau.Etape, nom: str,
                          media: dict[str, str], genre: Any, reglages: dict[str, Any],
                          label: str, etapes: list[dict[str, Any]], rang: int,
                          n: int, travail: Path | None, chaine_nom: str) -> dict[str, Any]:
        c = self._c
        store = c.store
        spec = c.catalog.get_spec(nom)
        ports = sorted(c.catalog.relais_de(spec))
        # Entre deux runs, le moteur GARDE ses modèles chargés (c'est voulu :
        # ne pas recharger). Quand les runs d'un même bloc portent des modèles
        # différents (phases : l'encodeur de texte, puis le modèle), les garder
        # rend la séparation vaine — mesuré le 2026-09-18 : 63,8 Go de commit au
        # rendu du bloc 1, l'encodeur du run précédent encore en mémoire. On
        # demande alors au moteur, par son opération officielle, de libérer ses
        # modèles avant chaque run ; le rechargement coûte des secondes, pas des
        # dizaines de Go.
        liberer = c.catalog.phases_de(spec) > 1
        pic = self._pic_d_un_tour(nom, reglages, n)
        if pic is not None:
            besoin, par_tour, facteur = self._pic_attendu(pic, n, None)
            store.append_log(
                parent_id,
                f"étape {etape.id} : rendu en {n} runs, un par tour de boucle — la place est "
                f"attendue avant chacun (jusqu'à {par_tour} images de {pic['largeur']}×"
                f"{pic['hauteur']} par tour, pic attendu {besoin / 2 ** 30:.1f} Gio à "
                f"{facteur:g} de crête)"
                + (f" ; relais entre les runs : {', '.join(ports)}" if ports else ""))
        else:
            store.append_log(
                parent_id,
                f"étape {etape.id} : rendu en {n} runs, un par tour de boucle — taille, cadence "
                f"ou durée inconnues avant le run : aucune garde de place entre les tours")
        parts: list[str] = []
        recits: list[dict[str, Any]] = []
        sous_ids: list[str] = []
        artefacts: list[str] = []
        duree = 0.0
        relais: dict[str, str] = {}       # port -> le nom du fichier chez le moteur
        for i in range(n):
            if store.get(parent_id).cancel_requested:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} : arrêt demandé avant le tour {i + 1}/{n}")
            etapes[rang]["note"] = f"tour {i + 1}/{n}"
            store.set_etapes(parent_id, etapes)
            if liberer and i > 0:
                try:
                    c.comfyui.free(unload_models=True, free_memory=True)
                    store.append_log(parent_id, f"étape {etape.id} : modèles du run précédent "
                                                f"libérés chez le moteur avant le tour {i + 1}/{n}")
                except Exception as exc:                    # noqa: BLE001
                    # repli: un moteur qui ne libère pas ne bloque pas le tour ;
                    # la garde de place, elle, dira si la place manque.
                    store.append_log(parent_id, f"étape {etape.id} : le moteur n'a pas libéré ses "
                                                f"modèles ({exc}) — la garde de place jugera")
            if pic is not None:
                self._attendre_la_place(parent_id, etape, nom, i, n, pic)
            media_i = dict(media)
            media_i.update({f"relais_{port}": fichier for port, fichier in relais.items()})
            reglages_i = {**reglages, "tour": i}
            essais = 0
            while True:
                try:
                    fini = self._executer_run(parent_id, etape, nom, media_i, genre, reglages_i,
                                              etiquette_de_run(label, etape.id, f"{i + 1}sur{n}"),
                                              etapes, rang)
                    break
                except MediaAssemblyError as exc:
                    # Même règle que pour une tranche : un tour qui échoue ALORS
                    # QUE la place manque a probablement échoué de ça ; on attend
                    # la place et on reprend, en le disant, jamais plus de
                    # REPRISES_MAX fois — et jamais quand la place ne manquait pas.
                    essais += 1
                    manque = self._manque_de_place(pic, n, None) if pic is not None else None
                    if essais > REPRISES_MAX or manque is None:
                        raise
                    marge, besoin = manque
                    store.append_log(
                        parent_id,
                        f"étape {etape.id} : le tour {i + 1}/{n} a échoué ({exc.detail}) alors "
                        f"que le poste n'avait que {marge / 2 ** 30:.1f} Gio de marge pour un "
                        f"pic attendu de {besoin / 2 ** 30:.1f} Gio — reprise après attente "
                        f"(essai {essais}/{REPRISES_MAX})")
                    self._attendre_la_place(parent_id, etape, nom, i, n, pic)
            livrable = _principal(fini.artifacts)
            relayes = [a for a in fini.artifacts if "_relais_" in Path(a.path).name]
            if livrable is not None and livrable.kind == "video":
                parts.append(livrable.path)
            elif not relayes:
                # Un run qui ne livre ni vidéo ni relais n'a rien fait pour la
                # suite. Un run d'ENCODAGE (une phase sans rendu) livre ses
                # relais seulement : il compte, sans ajouter de morceau.
                raise MediaAssemblyError(
                    f"étape {etape.id!r} ({nom}) : le tour {i + 1}/{n} n'a livré ni vidéo "
                    f"ni relais", job_id=fini.id, workflow=nom)
            sous_ids.append(fini.id)
            if i < n - 1:
                # Ce que les tours suivants relisent : ce que CE run a écrit,
                # déposé chez le moteur sous un nom que leur graphe peut citer.
                # Un run n'écrit que les relais que son dernier fragment offre
                # (une phase d'encodage : le conditionnement, pas l'image) ; le
                # reste garde ce qu'un run d'avant a déposé, et le run qui
                # relit un relais jamais écrit le dit lui-même.
                ecrits = [(port, _relais_ecrit(fini.artifacts, port)) for port in ports]
                ecrits = [(port, a) for port, a in ecrits if a is not None]
                if not ecrits:
                    raise MediaAssemblyError(
                        f"étape {etape.id!r} ({nom}) : le tour {i + 1}/{n} n'a écrit aucun "
                        f"relais ({', '.join(ports)}) parmi ses {len(fini.artifacts)} fichiers",
                        job_id=fini.id, workflow=nom)
                for port, ecrit in ecrits:
                    relais[port] = self._deposer(Path(ecrit.path))
                    store.append_log(
                        parent_id,
                        f"étape {etape.id} : relais « {port} » du tour {i + 1} "
                        f"({Path(ecrit.path).name}) déposé chez le moteur sous « {relais[port]} »")
            # Posés à CHAQUE tour, pas à la fin : une chaîne qui échoue au tour
            # 2/3 doit encore dire quels tours sont rendus, et où.
            etapes[rang]["job_ids"] = list(sous_ids)
            etapes[rang]["tours"] = n
            artefacts += [a.path for a in fini.artifacts]
            duree += fini.duration_s or 0.0
            recit = _recit(fini.artifacts, lambda raison, i=i: store.append_log(
                parent_id, f"étape {etape.id} : récit du tour {i + 1} illisible : {raison}"))
            if recit is not None:
                recits.append(recit)
        etapes[rang]["note"] = f"{n} tours"
        store.set_etapes(parent_id, etapes)
        if not parts:
            raise MediaAssemblyError(
                f"étape {etape.id!r} ({nom}) : aucun des {n} tours n'a livré de vidéo",
                workflow=nom)

        sortie = self._sortie(parent_id, label, etape.id, ".mp4", chaine=chaine_nom)
        # LE RACCORD DE CHAQUE TOUR SE CHERCHE, ET CE QUI EST REJOUÉ SE JETTE
        # (voir RACCORD_FENETRE_S). Les parts deviennent des rognages de tête.
        pieces: list[Any] = [parts[0]] if parts else []
        coupes: list[dict[str, Any]] = []
        for i in range(1, len(parts)):
            depuis = 0
            if travail is not None:
                try:
                    fenetre = int(round(RACCORD_FENETRE_S * float((pic or {}).get("fps") or
                                                                 montage_video.mesurer(parts[i]).get("fps") or 25)))
                    r = montage_video.chercher_raccord(
                        parts[i - 1], parts[i], Path(travail) / f"{etape.id}-raccord-{i}",
                        fenetre=fenetre, seuil=RACCORD_SEUIL)
                    total = montage_video.compter_images(Path(parts[i]))
                    if r["tenu"] and r["image"] > 0 and (r["image"] + 1) * 2 > total:
                        # un tour qui rejouerait plus de la moitié de lui-même
                        # n'est pas un tour rejoué : rien n'est coupé, et c'est dit
                        store.append_log(
                            parent_id,
                            f"étape {etape.id} : le tour {i + 1} ressemble au tour {i} jusqu'à "
                            f"son image {r['image'] + 1} sur {total} — plus de la moitié de "
                            f"lui-même, rien n'est coupé")
                    elif r["tenu"] and r["image"] > 0:
                        depuis = int(r["image"]) + 1
                        store.append_log(
                            parent_id,
                            f"étape {etape.id} : le tour {i + 1} rejoue la fin du tour {i} — "
                            f"ses {depuis} premières images sont jetées (ressemblance à la "
                            f"dernière image livrée : {r['premiere']:.2f} à la première, "
                            f"{r['ssim']:.2f} à l'image {r['image'] + 1})")
                    elif not r["tenu"]:
                        store.append_log(
                            parent_id,
                            f"étape {etape.id} : le tour {i + 1} ne rejoint le tour {i} nulle "
                            f"part dans ses {fenetre} premières images (au mieux {r['ssim']:.2f} "
                            f"à l'image {r['image'] + 1}, seuil {RACCORD_SEUIL}) — rien n'est coupé")
                    coupes.append({"tour": i + 1, "jetees": depuis, "ssim": r["ssim"],
                                   "premiere": r["premiere"], "tenu": bool(r["tenu"])})
                except MediaAssemblyError as exc:
                    # repli: un raccord qui ne se cherche pas ne coupe rien —
                    # et se dit au journal.
                    store.append_log(parent_id, f"étape {etape.id} : raccord du tour {i + 1} "
                                                f"non cherché ({exc.detail}) — rien n'est coupé")
            pieces.append({"fichier": parts[i], "depuis_image": depuis} if depuis else parts[i])
        if any(isinstance(piece, dict) for piece in pieces):
            # un rognage de tête ne se copie pas : les tours sont ré-encodés une fois
            fait = montage_video.recoller(pieces, sortie, fps=(pic or {}).get("fps"),
                                          largeur=(pic or {}).get("largeur"),
                                          hauteur=(pic or {}).get("hauteur"))
        else:
            try:
                fait = montage_video.concatener(pieces, sortie)
            except MediaAssemblyError as exc:
                # repli: des tours que la copie de flux ne sait pas joindre sont
                # recollés en ré-encodant — une génération de perte de plus, jamais
                # silencieuse.
                store.append_log(parent_id, f"étape {etape.id} : copie de flux impossible "
                                            f"({exc.detail}) — recollage ré-encodé")
                fait = montage_video.recoller(pieces, sortie, fps=(pic or {}).get("fps"),
                                              largeur=(pic or {}).get("largeur"),
                                              hauteur=(pic or {}).get("hauteur"))
        # Les jonctions du montage RÉEL (après les coupes) — toujours écrites :
        # un seul tour n'a pas de jonction, et une chaîne qui les constate lit
        # alors « aucune, parfaite » plutôt que « rien à désigner ».
        jonctions: dict[str, Any] = {"pire": 1.0, "moyenne": 1.0, "nombre": 0,
                                     "coupes": [c["jetees"] for c in coupes]}
        if travail is not None and len(pieces) > 1:
            try:
                mesure = montage_video.mesurer_raccords(pieces, Path(travail) / f"{etape.id}-jonctions")
                jonctions.update({"pire": mesure["pire"], "moyenne": mesure["moyenne"],
                                  "nombre": mesure["nombre"]})
            except MediaAssemblyError as exc:
                # repli: une jonction qui ne se mesure pas n'invalide pas le
                # livrable — elle se dit au journal, et « pire » reste absent.
                store.append_log(parent_id, f"étape {etape.id} : jonctions non mesurées "
                                            f"({exc.detail})")
                jonctions = {"nombre": len(pieces) - 1, "coupes": jonctions["coupes"]}
        comment = "sans ré-encodage" if fait.get("reencode") is False else "en ré-encodant"
        store.append_log(
            parent_id,
            f"étape {etape.id} : {n} tours recollés {comment}"
            + (f" — jonctions mesurées : pire {jonctions['pire']:.4f}, "
               f"moyenne {jonctions['moyenne']:.4f}" if "pire" in jonctions else "")
            + (f" ; images jetées aux raccords : {', '.join(str(j) for j in jonctions['coupes'])}"
               if any(jonctions["coupes"]) else ""))
        chemin = Path(fait["livrable"])
        resultat: dict[str, Any] = {
            "livrable": str(chemin), "job_id": sous_ids[-1], "job_ids": sous_ids, "tours": n,
            "mesure": {**(fait.get("mesure") or {}), "bytes": chemin.stat().st_size},
            "artefacts": artefacts, "_duree": duree, "jonctions": jonctions}
        if recits:
            resultat["recit"] = {**fusionner_recits(recits), "tours": n,
                                 "tours_au_recit": len(recits)}
        return resultat

    def _veiller(self, parent_id: str, sous_id: str, etape_id: str, rang: int,
                 total: int, arret: threading.Event) -> None:
        """Relayer la progression du sous-job, et porter l'arrêt jusqu'à lui.

        Le parent ne calcule rien : il attend dans `execute`. Sans ce relais, une
        chaîne n'affichait que « étape 2 sur 5 » pendant vingt minutes, et un
        arrêt demandé au parent ne parvenait jamais au run qui tournait.
        """
        arrete = False
        while not arret.wait(0.4):
            try:
                sous = self._c.store.get(sous_id)
                self._c.store.set_progress(parent_id, rang, total, etape_id,
                                           etape=sous.progress or {})
                if not arrete and self._c.store.get(parent_id).cancel_requested:
                    arrete = bool(arreter_au_moteur(self._c, sous_id).get("cancelled"))
            except Exception:
                # repli: ce fil ne fait que RELAYER. Le job a disparu ou le
                # moteur est muet : il n'a plus rien à dire, et le faire échouer
                # emporterait une chaîne qui, elle, se porte bien.
                return

    # -- fichiers ---------------------------------------------------------------

    def _sortie(self, job_id: str, label: str, etape_id: str, suffixe: str,
                chaine: str = "") -> Path:
        """Où écrire ce qu'une étape de montage produit — UN chemin par run.

        Même règle de nommage que les rendus : « <nom donné>_<type>-<étape> »,
        le type étant ici la chaîne. Mesuré le 2026-09-13 : deux runs du même
        mode portent le même label, donc écrivaient tous deux
        « cortex/<label>-final.mp4 » ; le second a effacé le livrable du premier
        (7 622 063 o à 13:32, 7 577 018 o à 13:35), et deux cartes de livraison
        montraient un seul fichier. Le début de l'identifiant du job les sépare,
        et se relit dans le nom.
        """
        base = Path(self._c.settings.comfy_output_dir).resolve() / "cortex"
        base.mkdir(parents=True, exist_ok=True)
        surete = lambda s: "".join(ch for ch in str(s) if ch.isalnum() or ch in "-_")
        nom, genre = surete(label), surete(chaine)
        tete = f"{nom}_{genre}" if nom and genre and nom != genre else (nom or genre)
        marque = "".join(ch for ch in str(job_id)[:8] if ch.isalnum())
        return base / f"{tete + '-' if tete else ''}{etape_id}_{marque}{suffixe}"

    def _fichier_local(self, valeur: Any, travail: Path) -> Path:
        """Le fichier désigné, ramené ICI s'il vit chez le moteur.

        Une pièce jointe est un NOM chez le moteur, pas un chemin : c'est lui qui
        sait où il range ses entrées. On le lui redemande par sa propre vue
        plutôt que de deviner un dossier d'installation.
        """
        texte = str(valeur)
        direct = Path(texte)
        if direct.is_file():
            return direct
        travail.mkdir(parents=True, exist_ok=True)
        dossier, _, nom = texte.replace("\\", "/").rpartition("/")
        cible = travail / f"depot-{nom}"
        if cible.is_file():
            return cible
        base = str(self._c.settings.comfyui_base_url or "").rstrip("/")
        if not base:
            raise MediaAssemblyError(
                f"« {texte} » n'est pas un fichier d'ici, et aucun moteur n'est "
                f"configuré pour le rapatrier")
        requete = base + "/view?" + urllib.parse.urlencode(
            {"filename": nom, "subfolder": dossier, "type": "input"})
        try:
            with urllib.request.urlopen(requete, timeout=120) as reponse:
                octets = reponse.read()
        except Exception as exc:
            raise MediaAssemblyError(
                f"« {texte} » : le moteur n'a pas rendu ce fichier d'entrée ({exc})") from exc
        if not octets:
            raise MediaAssemblyError(f"« {texte} » : le moteur a rendu un fichier vide")
        cible.write_bytes(octets)
        return cible

    def _parts_locales(self, parts: Any, travail: Path) -> list[Any]:
        if not isinstance(parts, (list, tuple)):
            raise MediaAssemblyError("« parts » doit être une liste")
        sorties: list[Any] = []
        for part in parts:
            if isinstance(part, dict):
                fichier = part.get("fichier") or part.get("path")
                # Une part VIDE traverse telle quelle : c'est ce qu'une étape
                # sautée rend (« sinon: {livrable: null} »), et c'est le montage
                # qui décide de l'ignorer — pas ce ramassage de fichiers, qui
                # échouait ici sur un chemin « None ».
                sorties.append({**part, "fichier": str(self._fichier_local(fichier, travail))
                                if fichier else None})
            elif isinstance(part, list):
                sorties.extend(self._parts_locales(part, travail))
            elif not part:
                sorties.append(part)
            else:
                sorties.append(str(self._fichier_local(part, travail)))
        return sorties

    def _deposer(self, fichier: Path) -> str:
        """Confier un fichier au moteur, et rendre le nom sous lequel il le connaît.

        C'est le seul nom qu'un graphe peut citer : reconstruire un chemin dans
        son dossier d'entrée revenait à parier sur son installation.
        """
        from .neutral import upload_image
        base = str(self._c.settings.comfyui_base_url or "").rstrip("/")
        if not base:
            raise MediaAssemblyError(
                f"dépôt de {fichier.name} impossible : aucun moteur n'est configuré")
        try:
            nom = upload_image(base, fichier.name, fichier.read_bytes(), True, 300.0)
        except Exception as exc:
            raise MediaAssemblyError(
                f"dépôt de {fichier.name} chez le moteur refusé : {exc}") from exc
        self._deposes[str(nom)] = fichier
        return nom

    def _mesure_du_media(self, media: dict[str, str] | None) -> dict[str, Any] | None:
        """Ce qu'une vidéo d'entrée de l'étape dit d'elle-même — taille et
        cadence — quand le graphe ne le dit pas dans ses réglages.

        Le média est un chemin local (le livrable d'une étape précédente) ou le
        nom d'un dépôt que la passerelle a fait elle-même : elle sait d'où il
        vient. Rien de lisible : None, et le run partira entier, en le disant.
        """
        for valeur in (media or {}).values():
            chemin = Path(str(valeur))
            fichier = chemin if chemin.is_file() else self._deposes.get(str(valeur))
            if fichier is None or not fichier.is_file():
                continue
            try:
                mesure = montage_video.mesurer(fichier)
            except MediaAssemblyError:
                continue
            if mesure.get("width") and mesure.get("height"):
                return {**mesure, "nom": fichier.name}
        return None


# -- le nom d'un sous-run ------------------------------------------------------

# La longueur que l'orchestrateur garde d'une étiquette (`resolve_params`,
# `intent.label[:40]`) : ce qui dépasse est perdu au nom du fichier.
LONGUEUR_D_ETIQUETTE = 40


def etiquette_de_run(label: str, etape_id: str, suffixe: str = "",
                     longueur: int = LONGUEUR_D_ETIQUETTE) -> str:
    """Le nom donné à un sous-run : « <label>-<étape>[-<suffixe>] », tenu dans
    la longueur que l'orchestrateur garde en rognant LE LABEL — jamais l'étape
    ni le suffixe. Coupé après l'ajout du suffixe, « …-rendu-1sur4 » devenait
    « …-rendu-1su », et deux caractères de plus auraient donné à tous les tours
    le même préfixe de fichier (mesuré le 2026-09-19 sur un label de trente
    caractères)."""
    queue = f"-{etape_id}" + (f"-{suffixe}" if suffixe else "")
    return str(label)[:max(0, longueur - len(queue))] + queue


# -- tranches : ce qu'un nœud déclare, et comment ses récits se fusionnent -----

# Une image rendue en mémoire : RVB en float32, ce qu'un nœud d'image rend au
# moteur. C'est ce poids, fois le nombre d'images, que le budget borne.
OCTETS_PAR_IMAGE = 3 * 4

def budget_de_tranche(container) -> tuple[int, str]:
    """Le budget d'une tranche et D'OÙ IL VIENT, pour tout ce qui découpe.

    L'ordre est écrit une seule fois (`materiel.budget_et_provenance`) : une
    surcharge d'essai, sinon le matériel déclaré du poste, sinon le repli. Deux
    lectures de cet ordre auraient fini par se contredire — l'une découpant, et
    l'autre disant pourquoi.
    """
    return materiel.budget_et_provenance(
        getattr(container, "materiel", None),
        int(getattr(container.settings, "tranche_octets", 0) or 0))


# Le plus grand nombre de tranches qu'un nœud accepte (la borne de son entrée
# `segment_count`). Au-delà, c'est la demande qui est hors de portée du poste,
# et il vaut mieux le dire que de tenter soixante-cinq runs.
TRANCHES_MAX = 64

# Combien de fois une tranche qui a échoué FAUTE DE PLACE est reprise, après
# avoir attendu que la place revienne. Trois : au-delà, ce n'est plus le poste
# qui change, c'est quelque chose qui ne passera pas.
REPRISES_MAX = 3
# LE RACCORD DES TOURS. Un bloc continué par référence rejoue la fin du bloc
# précédent avant de continuer (mesuré le 2026-09-20, job 03e675b0 : deux
# secondes au ralenti, SSIM 0,30 à la première image, 0,97 à la 61e). Le
# recollage cherche, parmi les RACCORD_FENETRE_S premières secondes de chaque
# tour, l'image qui rejoint la dernière du tour d'avant, et jette ce qui la
# précède — quand elle ressemble au moins à RACCORD_SEUIL ; sinon rien n'est
# coupé, et c'est dit. Antoine : « une coupure se fait mal, l'image semble
# revenir en arrière et puis continuer ».
RACCORD_FENETRE_S = 3.5
RACCORD_SEUIL = 0.75          # en luminance, à 320 px de large (montage_video.RACCORD_LARGEUR)


def _images_rendues(livrable: Artifact) -> int:
    """Combien d'images une tranche a rendues — sa mesure si elle la porte,
    sinon comptées sur le fichier ; 0 quand rien ne se lit (et alors la borne
    d'avant le run reste la référence)."""
    try:
        images = int((livrable.measured or {}).get("frames") or 0)
    except (TypeError, ValueError, AttributeError):
        images = 0
    if images > 0:
        return images
    try:
        return int(montage_video.compter_images(livrable.path) or 0)
    except Exception:                                    # noqa: BLE001
        # repli: une tranche qu'on ne sait pas compter garde la borne d'avant
        # le run — plus prudente, jamais moins.
        return 0


def noeud_de_tranches(graphe: dict[str, Any]) -> str | None:
    """Le nœud qui sait rendre une TRANCHE, s'il y en a un.

    C'est celui dont les entrées portent, en littéral, `segment_index` et
    `segment_count` : la convention par laquelle un nœud déclare rendre les
    images [n·i/N, n·(i+1)/N) d'une seule et même simulation. Une entrée liée
    à un autre nœud (une liste `[nœud, sortie]`) n'est pas un réglage qu'on
    puisse écrire : elle ne compte pas.
    """
    for ident, noeud in (graphe or {}).items():
        entrees = noeud.get("inputs") if isinstance(noeud, dict) else None
        if not isinstance(entrees, dict):
            continue
        if ("segment_index" in entrees and "segment_count" in entrees
                and not isinstance(entrees["segment_index"], list)
                and not isinstance(entrees["segment_count"], list)):
            return str(ident)
    return None


def fusionner_recits(recits: list[dict[str, Any]]) -> dict[str, Any]:
    """Le récit d'un rendu par tranches, fait des récits de chacune.

    Ce qui est IDENTIQUE d'une tranche à l'autre est un fait du plan — la même
    simulation l'a écrit — et reste tel quel. Ce qui DIFFÈRE est une mesure
    prise sur les images de la tranche : un nombre dont le nom finit en `_min`
    prend le minimum (et son instant `_s`, celui de la tranche qui le porte),
    en `_max` le maximum, une liste se concatène dans l'ordre des tranches (la
    série d'une grandeur suivie image par image), un objet de même ; tout autre
    scalaire qui diffère garde la valeur de la première tranche et se nomme
    dans `tranches_divergentes`, pour qu'un contrôle sache ce qu'il lit.
    """
    presents = [r for r in recits if isinstance(r, dict)]
    if not presents:
        return {}
    if len(presents) == 1:
        return dict(presents[0])
    divergents: list[str] = []

    def nombres(valeurs: list[Any]) -> bool:
        return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in valeurs)

    def fusion(parties: list[dict[str, Any]], prefixe: str) -> dict[str, Any]:
        sortie: dict[str, Any] = {}
        for cle in parties[0]:
            if cle.endswith("_min_s") or cle.endswith("_max_s"):
                continue                      # posé avec son extrême, ci-dessous
            valeurs = [p.get(cle) for p in parties]
            if all(v == valeurs[0] for v in valeurs[1:]):
                sortie[cle] = valeurs[0]
                continue
            nom = f"{prefixe}{cle}"
            if nombres(valeurs) and (cle.endswith("_min") or cle.endswith("_max")):
                rang = (valeurs.index(min(valeurs)) if cle.endswith("_min")
                        else valeurs.index(max(valeurs)))
                sortie[cle] = valeurs[rang]
                if f"{cle}_s" in parties[0]:
                    sortie[f"{cle}_s"] = parties[rang].get(f"{cle}_s")
                continue
            if all(isinstance(v, list) for v in valeurs):
                sortie[cle] = [x for v in valeurs for x in v]
                continue
            if all(isinstance(v, dict) for v in valeurs):
                sortie[cle] = fusion(valeurs, nom + ".")
                continue
            sortie[cle] = valeurs[0]
            divergents.append(nom)
        # les instants des extrêmes qui n'ont pas trouvé leur extrême : la
        # première tranche fait foi, et l'écart se nomme
        for cle in parties[0]:
            if (cle.endswith("_min_s") or cle.endswith("_max_s")) and cle not in sortie:
                valeurs = [p.get(cle) for p in parties]
                sortie[cle] = valeurs[0]
                if any(v != valeurs[0] for v in valeurs[1:]):
                    divergents.append(f"{prefixe}{cle}")
        # Une clé qu'une tranche SUIVANTE est seule à porter n'a rien à quoi se
        # fusionner — la première ne l'a pas écrite. La perdre en silence
        # laissait un contrôle chercher une clé qu'un nœud avait pourtant
        # écrite, alors que la clé manquante de l'autre côté (portée par la
        # première et non par les suivantes) se nommait déjà : elle se nomme
        # aussi.
        for partie in parties[1:]:
            for cle in partie:
                if cle not in sortie and f"{prefixe}{cle}" not in divergents:
                    divergents.append(f"{prefixe}{cle}")
        return sortie

    fusionne = fusion(presents, "")
    fusionne["tranches"] = len(presents)
    if divergents:
        fusionne["tranches_divergentes"] = divergents
    return fusionne


def _relais_ecrit(artefacts: list[Artifact], port: str) -> Artifact | None:
    """Le fichier qu'un run a écrit pour ce relais : une image dont le nom
    porte la marque « _relais_<port> » (le préfixe que l'assembleur donne au
    nœud qui l'écrit). Plusieurs images sous la même marque : la dernière
    écrite, celle qui compte pour une suite."""
    vus = [a for a in artefacts if f"_relais_{port}" in Path(a.path).name]
    if not vus:
        return None
    return sorted(vus, key=lambda a: Path(a.path).name)[-1]


def _principal(artefacts: list[Artifact]) -> Artifact | None:
    """Ce qu'un run a livré de PRINCIPAL, parmi ce qu'il a écrit.

    Un graphe peut livrer une analyse à côté de sa vidéo : c'est le MÉDIA que
    l'étape suivante reprend, jamais le fichier de nombres. Mais un run peut
    aussi n'avoir QUE des nombres à livrer — une étape qui documente, une étape
    qui écrit un plan : il a réussi, et son livrable est ce fichier. Le refuser
    faisait échouer l'étape sur « le run a réussi sans livrer de média », alors
    que c'est son récit, et non son média, que la suite attend.
    """
    medias = [a for a in artefacts if a.kind in ("video", "image", "audio", "3d")]
    videos = [a for a in medias if a.kind == "video"]
    choisis = videos or medias or list(artefacts)
    return max(choisis, key=lambda a: a.bytes or 0) if choisis else None


def _recit(artefacts: list[Artifact], signaler: Callable[[str], None]) -> dict[str, Any] | None:
    """Ce qu'un run a écrit SUR LUI-MÊME, à côté de son média.

    Un graphe qui met en scène peut livrer, avec sa vidéo, le récit de ce
    qu'il a décidé : à quelle seconde tel temps commence, ce qui est visible
    quand. C'est le premier fichier de nombres (JSON) que le run a livré, lu
    tel quel — une étape « verifier » le contrôle ensuite par
    « $etape.recit.cle », sans que ce module sache ce que le récit raconte ni
    quel nœud l'a écrit. Le compagnon d'origine est aussi un JSON, posé à côté
    de chaque livrable : ce n'est pas un récit, il est écarté par son suffixe.

    Un récit illisible ne se tait pas : il est dit au journal, et la clé reste
    absente — le contrôle qui la lit échoue alors en nommant ce qui manque,
    au lieu de passer sur un objet vide que personne n'a écrit.
    """
    for art in artefacts:
        chemin = Path(art.path)
        if (art.kind != "text" or chemin.suffix.lower() != ".json"
                or chemin.name.lower().endswith(SIDECAR_SUFFIX)):
            continue
        try:
            contenu = json.loads(chemin.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            signaler(f"{chemin.name} : {exc}")
            return None
        if not isinstance(contenu, dict):
            signaler(f"{chemin.name} : un objet JSON était attendu, "
                     f"trouvé {type(contenu).__name__}")
            return None
        return contenu
    return None


def _resume(resultat: dict[str, Any]) -> dict[str, Any]:
    """Ce qu'on garde d'une étape dans la fiche du job : de quoi comprendre,
    pas la totalité (une mesure de raccords porte une ligne par frontière)."""
    garde = {}
    for cle in ("livrable", "fichier", "depot", "images", "job_id", "job_ids", "tranches",
                "tours", "jonctions", "mesure", "pire", "moyenne", "nombre", "parts",
                "memoire", "sans_effet"):
        if cle in resultat:
            garde[cle] = resultat[cle]
    if "controles" in resultat:
        # …avec l'aide du contrôle quand il en porte une : ce qu'un constat n'a
        # pas tenu se lit sur la fiche, sans rouvrir la chaîne (2026-09-19).
        # …et son opérateur : la ligne d'un constat se lit comme celle d'un
        # refus (« mesuré 31.58, attendu lte 12.0 »), un lanceur la rend telle
        # quelle (2026-09-20 : sans lui, maestro écrivait « attendu undefined 12 »).
        garde["controles"] = [{"id": l["id"], "ok": l["ok"], "op": l.get("op"),
                               "mesure": l["mesure"], "attendu": l["attendu"],
                               **({"aide": l["aide"]} if l.get("aide") else {})}
                              for l in resultat["controles"]]
        # un CONSTAT dit qu'il en est un, et ce qu'il n'a pas tenu : c'est ce
        # qu'un lanceur montre sans en faire une erreur (« c'est l'utilisateur
        # qui juge », 2026-09-17)
        for cle in ("constat", "non_tenus"):
            if cle in resultat:
                garde[cle] = resultat[cle]
    if isinstance(resultat.get("recit"), dict):
        # Du récit, la fiche ne garde que ce qui se lit d'un coup d'œil : les
        # valeurs simples de son premier niveau (un nom, une seconde, un
        # verdict). Son calendrier et sa caméra pèsent des dizaines de milliers
        # d'octets (mesuré : ≈ 90 Ko sur un récit réel) et restent dans le
        # résultat complet, là où les étapes suivantes les lisent.
        garde["recit"] = {k: v for k, v in resultat["recit"].items()
                          if isinstance(v, (bool, int, float))
                          or (isinstance(v, str) and len(v) <= 80)}
    return garde


def _dire(resultat: dict[str, Any]) -> str:
    if "livrable" in resultat:
        return f"livrable {Path(resultat['livrable']).name}"
    if "depot" in resultat:
        return f"{resultat.get('images', '')} images déposées sous « {resultat['depot']} »".strip()
    if "pire" in resultat:
        return f"pire raccord {resultat['pire']:.4f} sur {resultat['nombre']} frontière(s)"
    if "controles" in resultat:
        if resultat.get("non_tenus"):
            return (f"constaté : {len(resultat['non_tenus'])} non tenu(s) sur "
                    f"{len(resultat['controles'])} — {', '.join(resultat['non_tenus'])}")
        return f"{len(resultat['controles'])} contrôle(s) tenus"
    return "fait"

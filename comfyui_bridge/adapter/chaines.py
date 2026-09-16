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

import json
import math
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
from . import montage_video
from .measure import measure
from .media import DOSSIER_DE_TRAVAIL, SIDECAR_SUFFIX, artifact_url, media_kind

# Ce que le journal d'un job de chaîne appelle un problème de contrôle. Nommé
# une fois : la mémoire d'Hermes et la réponse HTTP doivent dire le même mot.
CONTROLE_ECHOUE = "controle-echoue"


def etapes_initiales(chaine: noyau.Chaine) -> list[dict[str, Any]]:
    """Les étapes telles qu'on les annonce AVANT d'en faire une seule.

    Un job de chaîne accepté doit déjà dire ce qu'il va faire : sans cela, il
    n'était qu'un identifiant muet jusqu'à la première étape finie.
    """
    return [{"id": e.id, "genre": e.genre, "workflow": e.workflow, "statut": "todo",
             "job_id": None, "resultat": None, "note": None} for e in chaine.etapes]


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
    file = container.comfyui.queue()
    container.store.request_cancel(job_id)
    if ref in file.get("pending", []):
        sortie = container.comfyui.cancel([ref])
        container.store.append_log(job_id, "annulé dans la file du moteur (il n'avait pas commencé)")
        return {"cancelled": True, "how": "queue-delete", **sortie}
    if ref in file.get("running", []):
        sortie = container.comfyui.interrupt()
        container.store.append_log(job_id, "interruption demandée au moteur (run en cours)")
        return {"cancelled": True, "how": "interrupt", **sortie}
    return {"cancelled": False, "reason": "le moteur ne connaît plus ce run"}


class RunnerDeChaines:
    """Conduit une chaîne pour un job parent, dans le fil qui l'appelle."""

    def __init__(self, container: Any) -> None:
        self._c = container
        self._force = False

    # -- conduite -------------------------------------------------------------

    def executer(self, job_id: str, chaine: noyau.Chaine, valeurs: dict[str, Any],
                 label: str = "", force: bool = False) -> None:
        c = self._c
        self._force = force              # passe outre un souvenir, étape par étape
        store = c.store
        etapes = etapes_initiales(chaine)
        store.set_etapes(job_id, etapes)
        store.set_status(job_id, JobStatus.RUNNING)
        store.append_log(job_id, f"chaîne « {chaine.nom} » : {len(etapes)} étapes")
        travail = dossier_de_travail(c.settings.comfy_output_dir, job_id)
        resultats: dict[str, Any] = {}
        produits: list[str] = []
        duree = 0.0

        for rang, etape in enumerate(chaine.etapes):
            if store.get(job_id).cancel_requested:
                self._abandonner(job_id, etapes, rang, chaine, produits)
                return
            sautee = self._a_sauter(etape, valeurs, resultats)
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
        store.mark_succeeded(job_id, self._artefacts(final, produits), duration_s=duree)
        c.registry.record(c.settings.host_id, chaine.nom, valeurs, status="succeeded",
                          duration_s=duree)

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
                  resultats: dict[str, Any]) -> dict[str, Any] | None:
        """Ce qu'une étape SAUTÉE rend — ou None quand elle doit être jouée.

        Une étape porte « quand » : un renvoi vers un champ ou un résultat
        d'amont. Vide (texte blanc, faux, zéro, liste ou objet vides, absent),
        l'étape n'a rien à faire. Son résultat est alors un PASSE-PLAT : le
        premier média qu'elle devait reprendre devient son livrable, pour que
        l'aval la nomme sans savoir qu'elle n'a pas eu lieu."""
        if not etape.quand:
            return None
        valeur = noyau.resoudre(etape.quand, valeurs, resultats)
        if isinstance(valeur, str):
            pleine = bool(valeur.strip())
        else:
            pleine = bool(valeur)
        if pleine:
            return None
        resultat: dict[str, Any] = {"sautee": True,
                                    "raison": f"« {etape.quand} » est vide"}
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
        return resultat

    def _executer_etape(self, job_id: str, etape: noyau.Etape, valeurs: dict[str, Any],
                        resultats: dict[str, Any], travail: Path, label: str,
                        etapes: list[dict[str, Any]], rang: int,
                        chaine_nom: str = "") -> dict[str, Any]:
        if etape.genre == "verifier":
            return self._verifier(etape, valeurs, resultats)
        params = noyau.resoudre(etape.params, valeurs, resultats)
        if etape.genre == "rendre":
            return self._rendre(job_id, etape, params, label, etapes, rang,
                                travail=travail, chaine_nom=chaine_nom)
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
                                          chevauchement=int(params.get("chevauchement") or 0))
        if etape.genre == "mesurer_raccords":
            parts = self._parts_locales(params["parts"], travail)
            return montage_video.mesurer_raccords(
                parts, travail / etape.id, chevauchement=int(params.get("chevauchement") or 0))
        raise MediaAssemblyError(f"genre d'étape inconnu : {etape.genre!r}")

    def _verifier(self, etape: noyau.Etape, valeurs: dict[str, Any],
                  resultats: dict[str, Any]) -> dict[str, Any]:
        lignes = noyau.controler(etape.params, valeurs, resultats)
        faux = [l for l in lignes if not l["ok"]]
        if faux:
            dit = " ; ".join(f"{l['id']} : mesuré {l['mesure']!r}, attendu "
                             f"{l['op']} {l['attendu']!r}" for l in faux)
            raise ChainControlFailedError(f"contrôle non tenu — {dit}", controles=lignes)
        return {"controles": lignes}

    def _rendre(self, parent_id: str, etape: noyau.Etape, params: dict[str, Any],
                label: str, etapes: list[dict[str, Any]], rang: int,
                travail: Path | None = None, chaine_nom: str = "") -> dict[str, Any]:
        c = self._c
        reglages = dict(params)
        nom = str(reglages.pop("workflow"))
        media = {k: str(v) for k, v in (reglages.pop("media", None) or {}).items() if v}
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
        tranches = self._tranches_de(parent_id, etape, nom, reglages)
        if tranches is None:
            fini = self._executer_run(parent_id, etape, nom, media, genre, reglages,
                                      f"{label}-{etape.id}"[:40], etapes, rang)
            return self._resultat_du_run(parent_id, etape, nom, fini)
        return self._rendre_par_tranches(parent_id, etape, nom, media, genre, reglages,
                                         label, etapes, rang, tranches, travail, chaine_nom)

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
    # de la seconde ABSOLUE), et par `duree_max_s`, la durée au-delà de laquelle
    # il n'allonge plus : c'est elle qui borne le nombre de tranches, puisque le
    # nœud décide lui-même de la durée retenue.

    def tranches_pour(self, nom: str, reglages: dict[str, Any],
                      etiquette: str = "rendu") -> dict[str, Any] | None:
        """Le découpage qu'un rendu DIRECT de ce graphe demanderait — la même
        règle que pour une étape de chaîne, pour n'importe quel appelant (un
        mode de maestro de n'importe quelle catégorie, un appel d'API, un
        rejeu) : le mécanisme ne connaît que le graphe et son budget."""
        return self._tranches_de(None, noyau.Etape(id=etiquette, genre="rendre", params={}),
                                 nom, reglages)

    def _tranches_de(self, parent_id: str | None, etape: noyau.Etape, nom: str,
                     reglages: dict[str, Any]) -> dict[str, Any] | None:
        """Combien de tranches il faut pour que ce run tienne dans le budget —
        ou None quand il tient d'un seul tenant, ou que le nœud ne sait pas
        trancher (et alors le run part entier, comme avant)."""
        c = self._c
        budget = int(getattr(c.settings, "tranche_octets", 0) or 0)
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
            if parent_id is not None:
                c.store.append_log(parent_id, f"étape {etape.id} : pas de tranches — largeur, "
                                              f"hauteur ou cadence inconnues avant le run")
            return None
        plafond = (graphe.get(noeud) or {}).get("inputs", {}).get("duree_max_s")
        try:
            plafond = 0.0 if isinstance(plafond, list) else float(plafond or 0)
        except (TypeError, ValueError):
            plafond = 0.0
        duree = max(nombre("duration_s"), plafond)
        if duree <= 0:
            return None
        images = int(math.ceil(duree * fps))
        poids = int(largeur) * int(hauteur) * OCTETS_PAR_IMAGE
        n = int(math.ceil(images * poids / budget))
        if n <= 1:
            return None
        if n > TRANCHES_MAX:
            raise MediaAssemblyError(
                f"étape {etape.id!r} ({nom}) : il faudrait {n} tranches pour tenir "
                f"{images} images de {int(largeur)}×{int(hauteur)} dans "
                f"{budget / 2 ** 30:.1f} Gio, plus que les {TRANCHES_MAX} qu'un nœud "
                f"accepte — baisser la résolution ou la durée", workflow=nom)
        return {"noeud": noeud, "nombre": n, "images": images, "largeur": int(largeur),
                "hauteur": int(hauteur), "fps": int(fps), "poids": poids, "budget": budget}

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
            f"tenant) pour un budget de {tranches['budget'] / 2 ** 30:.1f} Gio par tranche")
        parts: list[str] = []
        recits: list[dict[str, Any]] = []
        sous_ids: list[str] = []
        artefacts: list[str] = []
        duree = 0.0
        for i in range(n):
            if store.get(parent_id).cancel_requested:
                raise MediaAssemblyError(
                    f"étape {etape.id!r} : arrêt demandé avant la tranche {i + 1}/{n}")
            etapes[rang]["note"] = f"tranche {i + 1}/{n}"
            store.set_etapes(parent_id, etapes)
            entrees = dict(reglages.get("inputs") or {})
            entrees[f"{noeud}.segment_index"] = i
            entrees[f"{noeud}.segment_count"] = n
            fini = self._executer_run(parent_id, etape, nom, media, genre,
                                      {**reglages, "inputs": entrees},
                                      f"{label}-{etape.id}-{i + 1}sur{n}"[:40], etapes, rang)
            livrable = _principal(fini.artifacts)
            if livrable is None or livrable.kind != "video":
                raise MediaAssemblyError(
                    f"étape {etape.id!r} ({nom}) : la tranche {i + 1}/{n} n'a pas livré de vidéo",
                    job_id=fini.id, workflow=nom)
            parts.append(livrable.path)
            sous_ids.append(fini.id)
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
                sorties.append({**part, "fichier": str(self._fichier_local(fichier, travail))})
            elif isinstance(part, list):
                sorties.extend(self._parts_locales(part, travail))
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
            return upload_image(base, fichier.name, fichier.read_bytes(), True, 300.0)
        except Exception as exc:
            raise MediaAssemblyError(
                f"dépôt de {fichier.name} chez le moteur refusé : {exc}") from exc


# -- tranches : ce qu'un nœud déclare, et comment ses récits se fusionnent -----

# Une image rendue en mémoire : RVB en float32, ce qu'un nœud d'image rend au
# moteur. C'est ce poids, fois le nombre d'images, que le budget borne.
OCTETS_PAR_IMAGE = 3 * 4

# Le plus grand nombre de tranches qu'un nœud accepte (la borne de son entrée
# `segment_count`). Au-delà, c'est la demande qui est hors de portée du poste,
# et il vaut mieux le dire que de tenter soixante-cinq runs.
TRANCHES_MAX = 64


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
    série de l'encre dans le cadre), un objet se fusionne de même ; tout autre
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
                "jonctions", "mesure", "pire", "moyenne", "nombre", "parts"):
        if cle in resultat:
            garde[cle] = resultat[cle]
    if "controles" in resultat:
        garde["controles"] = [{"id": l["id"], "ok": l["ok"], "mesure": l["mesure"],
                               "attendu": l["attendu"]} for l in resultat["controles"]]
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
        return f"{len(resultat['controles'])} contrôle(s) tenus"
    return "fait"

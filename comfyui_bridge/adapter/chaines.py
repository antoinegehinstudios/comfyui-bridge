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

import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..core import chaine as noyau
from ..core.errors import (BridgeError, ChainControlFailedError, MediaAssemblyError,
                           to_problem)
from ..core.intention import MediaKind, RenderIntent
from ..core.jobs import JobStatus
from ..core.plan import Artifact
from . import montage_video
from .measure import measure
from .media import DOSSIER_DE_TRAVAIL, artifact_url, media_kind

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
            etapes[rang]["statut"] = "running"
            store.set_etapes(job_id, etapes)
            store.set_progress(job_id, rang, len(etapes), etape.id)
            debut = time.monotonic()
            try:
                resultat = self._executer_etape(job_id, etape, valeurs, resultats,
                                                travail, label, etapes, rang)
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

    def _executer_etape(self, job_id: str, etape: noyau.Etape, valeurs: dict[str, Any],
                        resultats: dict[str, Any], travail: Path, label: str,
                        etapes: list[dict[str, Any]], rang: int) -> dict[str, Any]:
        if etape.genre == "verifier":
            return self._verifier(etape, valeurs, resultats)
        params = noyau.resoudre(etape.params, valeurs, resultats)
        if etape.genre == "rendre":
            return self._rendre(job_id, etape, params, label, etapes, rang)
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
            sortie = self._sortie(job_id, label, etape.id, ".mp4")
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
                label: str, etapes: list[dict[str, Any]], rang: int) -> dict[str, Any]:
        c = self._c
        reglages = dict(params)
        nom = str(reglages.pop("workflow"))
        media = {k: str(v) for k, v in (reglages.pop("media", None) or {}).items() if v}
        reglages.pop("label", None)
        reglages.pop("constraints", None)
        genre = reglages.pop("kind", None)
        intention = RenderIntent(workflow=nom, media=media,
                                 kind=MediaKind(genre) if genre else None,
                                 label=f"{label}-{etape.id}"[:40], **reglages)
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
        livrable = _principal(fini.artifacts)
        if livrable is None:
            raise MediaAssemblyError(
                f"étape {etape.id!r} ({nom}) : le run a réussi sans livrer de média",
                job_id=sous.id, workflow=nom)
        return {"livrable": livrable.path, "job_id": sous.id,
                "mesure": {**(livrable.measured or {}), "bytes": livrable.bytes},
                "artefacts": [a.path for a in fini.artifacts],
                "_duree": fini.duration_s}

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

    def _sortie(self, job_id: str, label: str, etape_id: str, suffixe: str) -> Path:
        """Où écrire ce qu'une étape de montage produit — UN chemin par run.

        Mesuré le 2026-09-13 : deux runs du même mode portent le même label,
        donc écrivaient tous deux « cortex/<label>-final.mp4 » ; le second a
        effacé le livrable du premier (7 622 063 o à 13:32, 7 577 018 o à 13:35),
        et deux cartes de livraison montraient un seul fichier. Le début de
        l'identifiant du job les sépare, et se relit dans le nom.
        """
        base = Path(self._c.settings.comfy_output_dir).resolve() / "cortex"
        base.mkdir(parents=True, exist_ok=True)
        propre = "".join(ch for ch in f"{label}-{etape_id}" if ch.isalnum() or ch in "-_")
        marque = "".join(ch for ch in str(job_id)[:8] if ch.isalnum())
        return base / f"{propre or etape_id}_{marque}{suffixe}"

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


def _principal(artefacts: list[Artifact]) -> Artifact | None:
    """Le média d'un run, parmi ce qu'il a écrit.

    Un graphe peut livrer une analyse à côté de sa vidéo : c'est le MÉDIA que
    l'étape suivante reprend, jamais le fichier de nombres.
    """
    medias = [a for a in artefacts if a.kind in ("video", "image", "audio", "3d")]
    videos = [a for a in medias if a.kind == "video"]
    choisis = videos or medias
    return max(choisis, key=lambda a: a.bytes or 0) if choisis else None


def _resume(resultat: dict[str, Any]) -> dict[str, Any]:
    """Ce qu'on garde d'une étape dans la fiche du job : de quoi comprendre,
    pas la totalité (une mesure de raccords porte une ligne par frontière)."""
    garde = {}
    for cle in ("livrable", "fichier", "depot", "images", "job_id", "mesure",
                "pire", "moyenne", "nombre", "parts"):
        if cle in resultat:
            garde[cle] = resultat[cle]
    if "controles" in resultat:
        garde["controles"] = [{"id": l["id"], "ok": l["ok"], "mesure": l["mesure"],
                               "attendu": l["attendu"]} for l in resultat["controles"]]
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

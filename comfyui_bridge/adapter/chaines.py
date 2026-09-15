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
                label: str, etapes: list[dict[str, Any]], rang: int) -> dict[str, Any]:
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
        resultat = {"livrable": livrable.path, "job_id": sous.id,
                    "mesure": {**(livrable.measured or {}), "bytes": livrable.bytes},
                    "artefacts": [a.path for a in fini.artifacts],
                    "_duree": fini.duration_s}
        recit = _recit(fini.artifacts, lambda raison: c.store.append_log(
            parent_id, f"étape {etape.id} : récit illisible : {raison}"))
        if recit is not None:
            resultat["recit"] = recit
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
    for cle in ("livrable", "fichier", "depot", "images", "job_id", "mesure",
                "pire", "moyenne", "nombre", "parts"):
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

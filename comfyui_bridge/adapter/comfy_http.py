"""ComfyUI HTTP backend — the real, universal interface.

Drives a running ComfyUI server over its standard HTTP API, mirroring the
proven Cortex `comfyui` connector:

    POST /prompt            submit the injected graph  -> prompt_id
    GET  /history/{id}      poll until outputs / error / timeout
    GET  /view?filename=…   fetch the produced media bytes
    GET  /system_stats      liveness probe

Pure stdlib (``urllib``) — no extra dependency. The graph is built the same way
as the CLI backend: an external mapping injects the resolved params, so nothing
here is ComfyUI-workflow-specific. If the server is down, ``submit`` raises a
clear ``BackendExecutionError`` (the job fails honestly) rather than hanging.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ..config import Settings
from ..core.errors import BackendExecutionError
from ..core.plan import Artifact, BackendResult, ExecutionPlan
from . import morceaux
from .catalog import WorkflowCatalog, build_injection
from .comfyui_client import ComfyUIClient
from .inflight import InflightLog
from .injector import apply_overrides, inject
from .measure import measure
from .media import DELIVERY_MECHANISM, artifact_url, media_kind, output_refs


def _execution_seconds(entry: dict) -> float | None:
    """How long ComfyUI itself says the run took.

    Its history stamps execution_start and execution_success; the difference is
    compute time WITHOUT the queue wait. Measuring it from our side instead gave
    1999 s for a run the engine did in 121 s — the wait was counted as work.
    """
    stamps: dict[str, int] = {}
    for m in ((entry.get("status") or {}).get("messages") or []):
        if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict):
            ts = m[1].get("timestamp")
            if isinstance(ts, (int, float)):
                stamps[m[0]] = ts
    start = stamps.get("execution_start")
    end = stamps.get("execution_success") or stamps.get("execution_error")
    if start and end and end >= start:
        return (end - start) / 1000.0
    return None


def _served_from_cache(entry: dict) -> bool:
    """True when every delivering node was reused rather than computed.

    ComfyUI says it itself (``execution_cached``). Measured: an identical
    intent came back in 0.3 s with 48 of 51 nodes reused — a real file, and a
    duration that says nothing about the cost of producing one.
    """
    cached: set[str] = set()
    for m in ((entry.get("status") or {}).get("messages") or []):
        if isinstance(m, list) and len(m) > 1 and m[0] == "execution_cached":
            cached.update(str(n) for n in (m[1].get("nodes") or []))
    producing = {str(n) for n in (entry.get("outputs") or {})}
    return bool(producing) and producing.issubset(cached)


class ComfyUIHttpBackend:
    def __init__(self, settings: Settings, catalog: WorkflowCatalog,
                 inflight: InflightLog | None = None) -> None:
        self._settings = settings
        self._catalog = catalog
        # Runs handed to the engine are written down so a restart cannot lose
        # what ComfyUI is still computing.
        self._inflight = inflight
        self._base = settings.comfyui_base_url.rstrip("/")
        # One implementation of "is the server up" — the client owns it.
        self._client = ComfyUIClient(self._base, settings.comfyui_request_timeout_s)

    def preview(self, plan: ExecutionPlan) -> dict[str, Any]:
        """Show the ComfyUI graph the named workflow WOULD run — no HTTP call."""
        return build_injection(self._catalog, plan)

    # -- helpers --------------------------------------------------------------

    def _post_json(self, path: str, obj: dict, timeout: float) -> dict:
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(
            self._base + path, data=data,
            headers={"content-type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get_json(self, path: str, timeout: float) -> dict:
        with urllib.request.urlopen(self._base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get_bytes(self, path: str, timeout: float) -> bytes:
        with urllib.request.urlopen(self._base + path, timeout=timeout) as r:
            return r.read()

    # -- liveness -------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        return self._client.probe()

    # -- execution ------------------------------------------------------------

    def submit(self, plan: ExecutionPlan, on_enqueued=None, on_progress=None,
               on_note=None, on_started=None) -> BackendResult:
        """Queue the graph in ComfyUI and wait for it.

        ``on_enqueued(prompt_id)`` fires as soon as ComfyUI accepts the job, so
        the caller can surface the id the run has IN COMFYUI'S OWN QUEUE — the
        run is visible and manageable there, not only here.

        ``on_progress(value, max, node)`` relays the progress ComfyUI itself
        broadcasts on its websocket (same channel its own UI listens to). If the
        websocket is unavailable we simply poll the history — no invented
        progress is ever reported.
        """
        spec = self._catalog.get_spec(plan.workflow)
        params = self._with_neutral_media(spec, dict(plan.params))
        # `monter` déplie les gabarits de montage : le nombre de blocs sort des
        # paramètres, donc les liaisons ne sont connues qu'après le dépliage.
        gabarit, liaisons = self._catalog.monter(spec, params)
        graph = apply_overrides(inject(gabarit, liaisons, params), plan.overrides)
        out_dir = self._settings.comfy_output_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        req_t = self._settings.comfyui_request_timeout_s

        # Connect first: a socket opened after queueing can miss early messages.
        # The engine says nothing while it loads a model, then starts reporting
        # steps: the gap between the two IS the setup, and it is what makes two
        # runs of the same size take 110 s or 73 s. Measured, not guessed.
        marks: dict[str, float] = {}

        def _started() -> None:
            marks.setdefault("start", time.monotonic())
            if on_started is not None:
                on_started()

        def _progress(value: int, maximum: int, node: str) -> None:
            marks.setdefault("first_step", time.monotonic())
            if on_progress is not None:
                on_progress(value, maximum, node)

        # UN identifiant de client PAR RUN. ComfyUI n'garde qu'une socket par
        # clientId (« Reusing existing session, remove old ») : deux runs lancés
        # coup sur coup avec le même identifiant faisaient remplacer la socket du
        # premier par celle du second. Mesuré : le premier run, terminé en 1 s
        # d'après l'historique du moteur, restait « en cours » côté passerelle.
        client_id = uuid.uuid4().hex
        ws, ws_reason = self._open_ws(client_id)
        try:
            prompt_id = self._enqueue(graph, req_t, client_id)
            self._note_inflight(prompt_id, plan)
            if on_enqueued is not None:
                on_enqueued(prompt_id, ws_reason)
            if ws is not None:
                self._watch_ws(ws, prompt_id, _progress, _started)
            entry = self._await_outputs(prompt_id, req_t, on_note)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        artifacts = self._download(entry, out_dir, req_t, plan)
        artifacts = self._recoller(artifacts, spec, out_dir, plan, on_note)
        self._forget_inflight(prompt_id)
        if not artifacts:
            raise BackendExecutionError("ComfyUI finished but produced no media", prompt_id=prompt_id)
        # The outputs can appear a moment before ComfyUI stamps
        # execution_success. Re-read once rather than lose its measure.
        measured = _execution_seconds(entry)
        if measured is None:
            time.sleep(max(0.5, self._settings.comfyui_poll_interval_s))
            try:
                again = self._get_json("/history/" + urllib.parse.quote(prompt_id), req_t)
                measured = _execution_seconds(again.get(prompt_id) or {})
            except Exception:
                pass
        setup = None
        if "start" in marks and "first_step" in marks:
            setup = max(0.0, marks["first_step"] - marks["start"])
        return BackendResult(artifacts=artifacts, raw_stdout=f"comfyui prompt {prompt_id}",
                             execution_s=measured, setup_s=setup,
                             cached=_served_from_cache(entry))

    # Ce qui ramasse le résultat porte une version : un échec peut venir de
    # LUI et non du moteur, et Hermes ne doit pas retenir contre un workflow le
    # verdict d'un mécanisme qui n'existe plus.
    delivery_mechanism = DELIVERY_MECHANISM

    def load_of(self, plan):
        """See ``RenderBackend.load_of`` — read from the graph, never assumed."""
        from .work import WORK_MODEL, effective_values, work_units
        try:
            return work_units(effective_values(self._catalog, plan)), WORK_MODEL
        except Exception:
            return None, None

    def _note_inflight(self, prompt_id: str, plan: ExecutionPlan) -> None:
        if self._inflight is None:
            return
        from datetime import datetime, timezone
        try:
            self._inflight.add(prompt_id, workflow=plan.workflow, config=plan.config,
                               work=getattr(plan, "work", None), params=dict(plan.params),
                               at=datetime.now(timezone.utc).isoformat(),
                               work_model=getattr(plan, "work_model", None),
                               kind=plan.kind)
        except Exception:
            pass        # bookkeeping must never break a run

    def _forget_inflight(self, prompt_id: str) -> None:
        if self._inflight is None:
            return
        try:
            self._inflight.remove(prompt_id)
        except Exception:
            pass

    def collect(self, prompt_id: str,
                plan: ExecutionPlan | None = None) -> tuple[list[Artifact], float | None] | None:
        """Fetch what the engine already produced for a prompt, after the fact.

        Returns None while ComfyUI has nothing final for it (still queued or
        running). Raises nothing of its own: the engine's history is the source.
        """
        req_t = self._settings.comfyui_request_timeout_s
        history = self._get_json("/history/" + urllib.parse.quote(prompt_id), req_t)
        entry = history.get(prompt_id)
        if not entry or not (entry.get("status") or {}).get("completed"):
            return None
        out_dir = self._settings.comfy_output_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        return self._download(entry, out_dir, req_t, plan), _execution_seconds(entry)

    def _with_neutral_media(self, spec, params: dict[str, Any]) -> dict[str, Any]:
        """A media input the caller left empty gets a neutral element, never the
        content the workflow happens to carry."""
        if not self._settings.neutral_media:
            return params
        from ..core.intention import media_category
        from .neutral import ensure_neutral, has_neutral
        deposes: dict[str, str] = {}        # une catégorie, un seul dépôt
        for param in sorted(spec.bindings):
            if params.get(param) or not has_neutral(param):
                continue
            categorie = media_category(param) or ""
            try:
                nom = deposes.get(categorie) or ensure_neutral(self._base, param)
            except Exception:
                continue        # engine refused the upload: leave the graph as-is
            if nom:
                deposes[categorie] = nom
                params[param] = nom
        return params

    def _enqueue(self, graph: dict, req_t: float, client_id: str) -> str:
        try:
            resp = self._post_json("/prompt", {"prompt": graph, "client_id": client_id}, req_t)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                j = json.loads(e.read().decode("utf-8"))
                detail = (j.get("error") or {}).get("message", "")
                if j.get("node_errors"):
                    detail += " " + json.dumps(j["node_errors"])[:300]
            except Exception:
                pass
            raise BackendExecutionError(
                f"ComfyUI rejected the graph (HTTP {e.code})"
                + (f": {detail.strip()[:300]}" if detail else ""),
                status=e.code,
            ) from e
        except urllib.error.URLError as e:
            raise BackendExecutionError(
                f"ComfyUI unreachable at {self._base}: {e.reason} (serveur lancé ?)"
            ) from e
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise BackendExecutionError("ComfyUI returned no prompt_id", response=str(resp)[:200])
        return str(prompt_id)

    def _open_ws(self, client_id: str):
        """ComfyUI's own progress channel (see its script_examples).

        Returns ``(socket_or_None, reason)``. The reason is reported to the
        caller: a channel that silently fails to attach is indistinguishable
        from an engine doing nothing.
        """
        try:
            import websocket  # websocket-client, the lib ComfyUI's examples use
        except Exception:
            return None, "websocket-client absent (pip install websocket-client)"
        url = self._base.replace("http://", "ws://").replace("https://", "wss://")
        try:
            ws = websocket.WebSocket()
            ws.connect(f"{url}/ws?clientId={client_id}", timeout=10)
            return ws, "attached"
        except Exception as e:
            return None, f"indisponible: {e}"

    def _watch_ws(self, ws, prompt_id: str, on_progress, on_started=None) -> None:
        """Read ComfyUI's broadcast until this prompt finishes.

        Message shapes are ComfyUI's: ``progress`` carries value/max, and
        ``executing`` with ``node: None`` marks the end of that prompt.
        """
        deadline = time.monotonic() + self._settings.comfyui_total_timeout_s
        ws.settimeout(5)
        quiet = 0
        while time.monotonic() < deadline:
            try:
                out = ws.recv()
            except Exception as e:
                # Silence is normal: loading a 20 GB model says nothing for
                # minutes. Only a CLOSED socket ends the watch — a read timeout
                # must not be mistaken for the end of the run.
                if type(e).__name__.endswith("TimeoutException"):
                    quiet += 1
                    # …but a run cancelled from the console is silent forever,
                    # et une socket que le moteur a remplacée l'est tout autant.
                    # Toutes les ~30 s, on demande au moteur s'il a encore
                    # quelque chose à dire sur ce run ; sinon on passe la main à
                    # l'interrogation de l'historique, qui, elle, conclut.
                    if quiet % 6 == 0 and self.settled(prompt_id):
                        return
                    continue
                return  # socket closed/broken: the history poll takes over
            if not isinstance(out, str):
                continue  # binary frame = a preview image, not our business
            try:
                msg = json.loads(out)
            except Exception:
                continue
            data = msg.get("data") or {}
            if data.get("prompt_id") not in (None, prompt_id):
                continue  # another client's run
            if msg.get("type") == "execution_start" and on_started is not None:
                on_started()
            elif msg.get("type") == "progress" and on_progress is not None:
                on_progress(int(data.get("value", 0)), int(data.get("max", 0)),
                            str(data.get("node") or ""))
            elif msg.get("type") == "executing" and data.get("node") is None                     and data.get("prompt_id") == prompt_id:
                return  # ComfyUI says this prompt is done

    def settled(self, prompt_id: str) -> bool:
        """Le moteur n'a plus rien à annoncer sur ce run : fini, ÉCHOUÉ, ou oublié.

        Une socket devenue muette ressemble trait pour trait à un moteur qui
        charge un modèle ; un run que le moteur a terminé PAR UNE ERREUR
        ressemble, lui, à un run qui n'a pas encore produit. Seul l'historique
        tranche, et il le dit : mesuré sur un vrai échec de nœud, `status_str`
        vaut « error » tandis que `completed` reste faux et que `outputs` reste
        vide. Ne lire que `completed` laissait donc l'échec passer pour une
        attente — la socket remplacée attendait le budget entier, et la trace du
        run traînait dans le journal des runs en vol à chaque reprise.
        """
        try:
            q = self._client.queue()
            if prompt_id in q.get("running", []) + q.get("pending", []):
                return False
            entry = self._get_json("/history/" + urllib.parse.quote(prompt_id),
                                   self._settings.comfyui_request_timeout_s).get(prompt_id)
            if entry is None:
                return True         # ni en file, ni connu : disparu
            statut = entry.get("status") or {}
            return (bool(entry.get("outputs")) or bool(statut.get("completed"))
                    or statut.get("status_str") == "error")
        except Exception:
            return False        # unsure: never conclude on a silent engine

    def failure(self, prompt_id: str) -> str | None:
        """Le message du moteur quand SON historique dit que ce run a fini en erreur.

        `None` tant que rien n'est définitif — encore en file, en cours, ou
        moteur muet. Une erreur laisse `completed: false` dans l'historique de
        ComfyUI, exactement comme un run qui continue : sans cette lecture, la
        reprise attendait indéfiniment un run que le moteur avait abandonné, et
        son échec n'était jamais consigné.
        """
        try:
            hist = self._get_json("/history/" + urllib.parse.quote(prompt_id),
                                  self._settings.comfyui_request_timeout_s)
        except Exception:
            return None             # moteur muet : on ne conclut pas
        entry = hist.get(prompt_id)
        if not entry:
            return None
        status = entry.get("status") or {}
        if status.get("completed") or status.get("status_str") != "error":
            return None
        # Le verdict du moteur est GARDÉ avec son message : classer « string
        # indices must be integers » sans lui donnait un échec non classé,
        # c'est-à-dire quelque chose que la passerelle aurait pu causer.
        # `execution_error` dit que c'est le graphe qui a échoué, pas nous.
        return "execution_error" + (self._error_detail(entry)
                                    or " (le moteur n'a pas dit lequel)")

    def vanished(self, prompt_id: str) -> bool:
        """True when the engine knows this prompt neither in its queue nor in
        its history — cancelled, or the engine restarted."""
        try:
            q = self._client.queue()
            if prompt_id in q.get("running", []) + q.get("pending", []):
                return False
            hist = self._get_json("/history/" + urllib.parse.quote(prompt_id),
                                  self._settings.comfyui_request_timeout_s)
            return prompt_id not in hist
        except Exception:
            return False        # unsure: never conclude on a silent engine

    def _await_outputs(self, prompt_id: str, req_t: float, on_note=None) -> dict:
        deadline = time.monotonic() + self._settings.comfyui_total_timeout_s
        unreachable = 0
        lost = 0
        while time.monotonic() < deadline:
            time.sleep(self._settings.comfyui_poll_interval_s)
            try:
                hist = self._get_json("/history/" + urllib.parse.quote(prompt_id), req_t)
                unreachable = 0
            except Exception:
                # NOT a verdict. A saturated ComfyUI stops answering HTTP while
                # it computes (measured: a heavy model pegs it for minutes) and
                # then delivers normally. Declaring failure here made the bridge
                # report "failed" on a run ComfyUI actually completed — and fed
                # Hermes a false problem. Only ComfyUI's history decides.
                unreachable += 1
                # Say it out loud after a while: a silent engine looks exactly
                # like a slow one, and ours does crash (CUDA fault, measured).
                if on_note is not None and unreachable in (60, 300, 900):
                    on_note(f"moteur muet depuis ~{unreachable}s — saturé ou arrêté ; "
                            f"le run reste suivi ({prompt_id})")
                continue
            entry = hist.get(prompt_id)
            if not entry:
                # The engine ANSWERS but does not know this prompt: it is neither
                # queued nor done. That happens when ComfyUI restarts mid-run
                # (measured: a CUDA fault killed it). Waiting the whole budget
                # for a run that no longer exists would be a lie of omission.
                try:
                    q = self._client.queue()
                    if prompt_id not in q.get("running", []) + q.get("pending", []):
                        lost += 1
                        if lost >= 5:
                            raise BackendExecutionError(
                                "le moteur ne connaît plus ce run (ComfyUI a redémarré "
                                "ou l'a perdu) — relancer",
                                prompt_id=prompt_id,
                            )
                    else:
                        lost = 0
                except BackendExecutionError:
                    raise
                except Exception:
                    pass
                continue
            status = (entry.get("status") or {}).get("status_str")
            if status == "error":
                raise BackendExecutionError(
                    "ComfyUI workflow error" + self._error_detail(entry),
                    stderr_tail=json.dumps(entry.get("status", {}))[:800],
                )
            if entry.get("outputs"):
                return entry
        # Budget spent: ask ComfyUI one last time before concluding anything —
        # the run may have finished while the server was unresponsive.
        try:
            entry = self._get_json("/history/" + urllib.parse.quote(prompt_id), req_t).get(prompt_id)
            if entry and entry.get("outputs"):
                return entry
        except Exception:
            pass
        raise BackendExecutionError(
            f"ComfyUI timed out after {self._settings.comfyui_total_timeout_s}s"
            + (f" (serveur muet sur {unreachable} sondages)" if unreachable else ""),
            prompt_id=prompt_id,
        )

    @staticmethod
    def _error_detail(entry: dict) -> str:
        try:
            for m in entry.get("status", {}).get("messages", []):
                if m and m[0] == "execution_error":
                    return ": " + str(m[1].get("exception_message", ""))[:300]
        except Exception:
            pass
        return ""

    def _recoller(self, artifacts: list[Artifact], spec, out_dir: Path,
                  plan: ExecutionPlan | None, on_note=None) -> list[Artifact]:
        """Joindre les morceaux d'un montage en un seul livrable.

        Fin de chaine : ce qui est rendu doit etre la video commandee, pas la
        collection de blocs qui a servi a la produire. Si le recollage echoue,
        les morceaux sont rendus tels quels avec la raison — mieux vaut un
        livrable en pieces et dit, qu'un run declare perdu.
        """
        declare = self._catalog.livrable(spec) if hasattr(self._catalog, "livrable") else {}
        prefixe = declare.get("morceaux")
        if not prefixe or len(artifacts) < 2:
            return artifacts
        pieces = morceaux.a_recoller([a.path for a in artifacts], prefixe)
        if len(pieces) < 2:
            return artifacts
        nom = (plan.params.get("filename_prefix") if plan else None) or "cortex/video"
        sortie = out_dir / (str(nom).replace("\\", "/") + "_recolle.mp4")
        try:
            morceaux.joindre(pieces, sortie)
        except Exception as exc:
            if on_note:
                on_note("morceaux non recolles (%s) — les blocs sont livres tels quels" % exc)
            return artifacts
        if on_note:
            on_note("%d morceaux recolles en un livrable : %s" % (len(pieces), sortie.name))
        joint = Artifact(kind=media_kind(sortie), path=str(sortie.resolve()),
                         url=artifact_url(out_dir, sortie),
                         bytes=sortie.stat().st_size, measured=measure(sortie) or None)
        # Le livrable d'abord : c'est LUI dont le journal et le demandeur parlent.
        return [joint] + artifacts

    def _download(self, entry: dict, out_dir: Path, req_t: float,
                  plan: ExecutionPlan | None = None) -> list[Artifact]:
        refs = output_refs(entry)
        artifacts: list[Artifact] = []
        for ref in refs:
            qs = urllib.parse.urlencode({
                "filename": ref["filename"],
                "subfolder": ref.get("subfolder", ""),
                "type": ref.get("type", "output"),
            })
            try:
                data = self._get_bytes("/view?" + qs, req_t)
            except Exception as e:
                raise BackendExecutionError(f"ComfyUI /view failed: {e}") from e
            # Keep ComfyUI's subfolder: without it, same-named outputs from
            # different subfolders overwrite each other.
            dest = out_dir / (ref.get("subfolder") or "") / ref["filename"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            artifacts.append(Artifact(
                kind=media_kind(dest),
                path=str(dest.resolve()),
                url=artifact_url(out_dir, dest),
                bytes=len(data),
                measured=measure(dest) or None,
            ))
        return artifacts

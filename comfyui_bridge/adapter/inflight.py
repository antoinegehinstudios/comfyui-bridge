"""Runs handed to the engine but not yet collected.

The engine keeps working when this service restarts: the render finishes, the
media is written, and nobody comes to fetch it. Measured here — a run completed
in ComfyUI while the bridge was restarting, and it left no artifact and no
measure, exactly the "a run delivered nothing" the console must never show.

So the prompt id is written down the moment ComfyUI accepts it, and erased when
the run is collected. What remains at startup is asked of ComfyUI itself: its
history is the authority on what happened. Nothing is reconstructed here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class InflightLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, prompt_id: str, *, workflow: str, config: str,
            work: float | None, params: dict[str, Any], at: str,
            work_model: int | None = None, kind: str = "") -> None:
        data = self._read()
        # `kind` est noté : sans lui, le plan refait à la reprise retombe sur
        # son défaut ("image") et fait dire à un livrable vidéo qu'il est une
        # image. Le relire ici, c'est le tenir de la source, pas le redéduire.
        data[prompt_id] = {"workflow": workflow, "config": config, "work": work,
                           "work_model": work_model, "params": params, "at": at,
                           "kind": kind}
        self._write(data)

    def remove(self, prompt_id: str) -> None:
        data = self._read()
        if data.pop(prompt_id, None) is not None:
            self._write(data)

    def entries(self) -> dict[str, Any]:
        return self._read()


def _load_of(backend, meta: dict[str, Any]) -> tuple[Any, Any]:
    """The load of a recovered run, recomputed if it was never written down."""
    if meta.get("work") is not None:
        return meta["work"], meta.get("work_model")
    weigh = getattr(backend, "load_of", None)
    if weigh is None:
        return None, None
    try:
        from ..core.plan import ExecutionPlan
        return weigh(_plan_from(meta))
    except Exception:
        return None, None


def _plan_from(meta: dict[str, Any]):
    """Le plan tel qu'il a été noté — pas un plan appauvri reconstruit de mémoire.

    Ce qu'on avait noté du run sert au poids ET au fichier compagnon : le nom du
    workflow et sa nature n'existent que de ce côté-ci.
    """
    from ..core.plan import ExecutionPlan
    return ExecutionPlan(intent=None, params=meta.get("params") or {},
                         workflow=meta.get("workflow", ""),
                         kind=meta.get("kind") or "image")


def recover(backend, log: InflightLog, registry, host: str) -> list[dict[str, Any]]:
    """Collect what the engine finished while nobody was listening.

    For each run still written down, ComfyUI's own history says whether it
    completed. What it produced is fetched and recorded with the engine's own
    measure, exactly as a live run would have been; what it never finished is
    simply forgotten — no invented outcome, no invented duration.
    """
    recovered: list[dict[str, Any]] = []
    for prompt_id, meta in list(log.entries().items()):
        try:
            result = backend.collect(prompt_id, _plan_from(meta))
        except Exception as exc:                     # engine down, or history gone
            recovered.append({"prompt_id": prompt_id, "state": "unknown", "detail": str(exc)[:200]})
            continue
        if result is None:
            # Rien de définitif POUR LE MOTEUR — mais « rien à collecter » ne
            # veut pas dire « ça continue ». Un run qu'il a terminé en erreur
            # laisse la même trace qu'un run en cours (`completed: false`) :
            # mesuré, une exception dans un nœud a laissé le run en vol
            # indéfiniment, redemandé à chaque démarrage, et son échec n'a
            # jamais été consigné. Ce que le moteur a abandonné, on le retient
            # comme un run vivant l'aurait été.
            echec = getattr(backend, "failure", None)
            detail = echec(prompt_id) if echec else None
            if detail:
                from ..core.problems import classify
                log.remove(prompt_id)
                registry.record(host, meta.get("workflow", "?"), meta.get("params") or {},
                                status="failed", problem=classify(detail), detail=detail,
                                mechanism=getattr(backend, "delivery_mechanism", None))
                recovered.append({"prompt_id": prompt_id, "state": "failed",
                                  "workflow": meta.get("workflow"), "detail": detail[:200]})
                continue
            # Sinon : soit ça tourne encore, soit le moteur n'en sait plus rien
            # (annulé depuis la console, moteur redémarré) — et l'attendre pour
            # toujours serait un mensonge.
            #
            # La question posée ici est « nous doit-on encore quelque chose ? »,
            # et NON « le moteur a-t-il fini ? ». Les deux se confondent presque,
            # et la nuance coûte un livrable : `settled()` — la bonne question
            # pour la veille par socket — dit vrai dès qu'il y a des sorties,
            # or ComfyUI les publie parfois AVANT d'estampiller la fin. Dans cet
            # instant, `collect()` rend None faute de tampon alors que le média
            # est là : sortir la ligne du journal le perdrait, et le marquerait
            # « échoué » par-dessus le marché. Seule l'ignorance du moteur
            # libère : ce qu'il ne connaît plus, il ne le rendra jamais.
            perdu = getattr(backend, "vanished", None)
            if perdu and perdu(prompt_id):
                log.remove(prompt_id)
                recovered.append({"prompt_id": prompt_id, "state": "lost",
                                  "workflow": meta.get("workflow")})
            continue
        artifacts, measured = result
        log.remove(prompt_id)
        if artifacts:
            work, work_model = _load_of(backend, meta)
            registry.record(host, meta.get("workflow", "?"), meta.get("params") or {},
                            status="succeeded", duration_s=measured, work=work,
                            work_model=work_model,
                            mechanism=getattr(backend, "delivery_mechanism", None))
            recovered.append({"prompt_id": prompt_id, "state": "recovered",
                              "workflow": meta.get("workflow"),
                              "artifacts": [a.path for a in artifacts]})
        else:
            recovered.append({"prompt_id": prompt_id, "state": "no-media",
                              "workflow": meta.get("workflow")})
    return recovered

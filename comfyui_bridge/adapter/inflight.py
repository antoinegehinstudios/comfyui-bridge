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
            work_model: int | None = None) -> None:
        data = self._read()
        data[prompt_id] = {"workflow": workflow, "config": config, "work": work,
                           "work_model": work_model, "params": params, "at": at}
        self._write(data)

    def remove(self, prompt_id: str) -> None:
        data = self._read()
        if data.pop(prompt_id, None) is not None:
            self._write(data)

    def entries(self) -> dict[str, Any]:
        return self._read()


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
            result = backend.collect(prompt_id)
        except Exception as exc:                     # engine down, or history gone
            recovered.append({"prompt_id": prompt_id, "state": "unknown", "detail": str(exc)[:200]})
            continue
        if result is None:
            # Nothing final. Either it is still going, or the engine has no
            # idea about it (cancelled from the console, engine restarted) —
            # in which case waiting for it forever would be a lie.
            if getattr(backend, "vanished", None) and backend.vanished(prompt_id):
                log.remove(prompt_id)
                recovered.append({"prompt_id": prompt_id, "state": "lost",
                                  "workflow": meta.get("workflow")})
            continue
        artifacts, measured = result
        log.remove(prompt_id)
        if artifacts:
            registry.record(host, meta.get("workflow", "?"), meta.get("params") or {},
                            status="succeeded", duration_s=measured, work=meta.get("work"),
                            work_model=meta.get("work_model"))
            recovered.append({"prompt_id": prompt_id, "state": "recovered",
                              "workflow": meta.get("workflow"),
                              "artifacts": [a.path for a in artifacts]})
        else:
            recovered.append({"prompt_id": prompt_id, "state": "no-media",
                              "workflow": meta.get("workflow")})
    return recovered

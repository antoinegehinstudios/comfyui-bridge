"""Thin ComfyUI HTTP client for its userdata API (saved workflows).

Independent of the render backend, so the workflow-management subsystem can list
what's saved in ComfyUI and fetch a source workflow even when rendering runs via
the CLI backend. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def source_hash(data: Any) -> str:
    """Stable short hash of a workflow's content — for update detection."""
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class ComfyUIClient:
    def __init__(self, base_url: str, request_timeout_s: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._t = request_timeout_s

    def _get_json(self, path: str) -> Any:
        with urllib.request.urlopen(self._base + path, timeout=self._t) as r:
            return json.loads(r.read().decode("utf-8"))

    def probe(self) -> dict[str, Any]:
        try:
            self._get_json("/system_stats")
            return {"available": True, "base_url": self._base}
        except urllib.error.HTTPError as e:
            return {"available": False, "base_url": self._base, "reason": f"HTTP {e.code}"}
        except Exception as e:
            return {"available": False, "base_url": self._base,
                    "reason": f"unreachable: {e} (ComfyUI Desktop lancé ?)"}

    def list_saved_workflows(self) -> list[str]:
        """Names of workflows saved in ComfyUI (UI format), via the userdata API.

        An absent workflows folder is not an error: it honestly means "none".
        """
        try:
            data = self._get_json("/api/userdata?dir=workflows&recurse=true&split=false")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise
        out: list[str] = []
        for x in data if isinstance(data, list) else []:
            # entries are either "name.json" or ["name.json", size, mtime]
            name = x[0] if isinstance(x, list) and x else x
            if isinstance(name, str) and name.endswith(".json"):
                out.append(name)
        return out

    def queue(self) -> dict[str, Any]:
        """ComfyUI's OWN queue, relayed as-is.

        ComfyUI already tracks what is running and what waits; we relay its
        answer rather than keeping a second queue that could disagree.
        Shape: {"running": [prompt_id, …], "pending": [prompt_id, …]}.
        """
        data = self._get_json("/queue")

        def ids(entries) -> list[str]:
            out = []
            for e in entries or []:
                # each entry is [number, prompt_id, graph, extra, outputs]
                if isinstance(e, list) and len(e) > 1:
                    out.append(str(e[1]))
            return out

        def rows(entries, state):
            out = []
            for e in entries or []:
                if isinstance(e, list) and len(e) > 1:
                    out.append({"prompt_id": str(e[1]), "number": e[0], "state": state})
            return out

        return {
            "running": ids(data.get("queue_running")),
            "pending": ids(data.get("queue_pending")),
            "items": rows(data.get("queue_running"), "running")
                     + rows(data.get("queue_pending"), "pending"),
        }

    def _post_json(self, path: str, obj: dict) -> Any:
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(self._base + path, data=data,
                                     headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self._t) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            return json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            return {"raw": body[:200]}

    # -- official lifecycle endpoints, relayed (never reimplemented) ----------

    def free(self, unload_models: bool = True, free_memory: bool = True) -> dict[str, Any]:
        """POST /api/free — ComfyUI's own way to release VRAM without a restart."""
        self._post_json("/api/free", {"unload_models": unload_models, "free_memory": free_memory})
        return {"ok": True, "unload_models": unload_models, "free_memory": free_memory}

    def interrupt(self) -> dict[str, Any]:
        """POST /api/interrupt — ComfyUI's own cancel of the running job."""
        self._post_json("/api/interrupt", {})
        return {"ok": True}

    def manager_reboot(self) -> dict[str, Any]:
        """POST /v2/manager/reboot — ComfyUI-Manager's own restart, when present."""
        try:
            self._post_json("/v2/manager/reboot", {})
            return {"ok": True, "via": "comfyui-manager"}
        except Exception as e:
            return {"ok": False, "reason": f"manager reboot indisponible: {e}"}

    def clear_queue(self) -> dict[str, Any]:
        """POST /api/queue {clear:true} — ComfyUI's own way to drop pending jobs."""
        self._post_json("/api/queue", {"clear": True})
        return {"ok": True, "cleared": "pending"}

    def cancel(self, prompt_ids: list[str]) -> dict[str, Any]:
        """POST /api/queue {delete:[…]} — cancel specific queued jobs."""
        self._post_json("/api/queue", {"delete": list(prompt_ids)})
        return {"ok": True, "cancelled": list(prompt_ids)}

    def get_object_info(self) -> dict[str, Any]:
        """ComfyUI's node registry: per class, the declared inputs (type,
        tooltip, options) and whether the node delivers a file."""
        data = self._get_json("/object_info")
        return data if isinstance(data, dict) else {}

    def get_saved_workflow(self, name: str) -> Any:
        """Fetch a saved workflow's raw JSON (UI format) by its userdata name."""
        key = urllib.parse.quote("workflows/" + name, safe="")
        return self._get_json("/api/userdata/" + key)


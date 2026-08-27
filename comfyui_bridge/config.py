"""Runtime configuration.

Everything the bridge needs to talk to a host is read from the environment so
the same code runs in dry-run on a laptop and against a real ComfyUI box in
Cortex without edits. Kept as a frozen dataclass: config is data, not behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_RESOURCES = Path(__file__).resolve().parent / "adapter" / "resources"
# Private data dir owned by THIS pipeline (not CWD-relative) — Hermes knowledge
# lives here so it can't be shared/leaked by accident.
_DATA = Path(__file__).resolve().parent.parent / "_data"


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- ComfyUI CLI adapter -------------------------------------------------
    comfy_cli_bin: str = os.getenv("COMFY_CLI_BIN", "comfy")
    # Placeholders {workflow} and {timeout} are substituted at call time.
    # Default matches the official `comfy-cli` run syntax.
    comfy_run_args: tuple[str, ...] = tuple(
        os.getenv(
            "COMFY_RUN_ARGS",
            "run --workflow {workflow} --wait --timeout {timeout}",
        ).split()
    )
    comfy_output_dir: Path = Path(os.getenv("COMFY_OUTPUT_DIR", "./_comfy_output"))
    exec_timeout_s: int = int(os.getenv("COMFY_TIMEOUT", "600"))
    dry_run: bool = _flag("COMFY_DRY_RUN", True)

    # Backend selection:
    #   "http" (default) — ComfyUI HTTP API, the real universal interface. The
    #                      pipeline delivers MEDIA on success, or an error fed to
    #                      Hermes on failure. Needs a running ComfyUI server.
    #   "cli"            — comfy-cli subprocess; honours dry_run (True => offline
    #                      manifest, a simulation stub, NOT a deliverable)
    comfy_backend: str = os.getenv("COMFY_BACKEND", "http")

    # --- ComfyUI HTTP API (backend "http") -----------------------------------
    # WHERE ComfyUI runs is reconciled from engines.json (attach vs managed).
    # COMFYUI_BASE_URL still wins when set, for a one-off override.
    engines_file: Path = Path(os.getenv("COMFY_ENGINES", str(_RESOURCES / "engines.json")))
    engine: str = os.getenv("COMFY_ENGINE", "")          # "" -> the file's default
    comfyui_base_url: str = os.getenv("COMFYUI_BASE_URL", "")
    # Video on a modest GPU offloads heavily: a run legitimately takes tens of
    # minutes. Measured here: 300s was far too short and cut a live render.
    comfyui_total_timeout_s: int = int(os.getenv("COMFYUI_TIMEOUT", "3600"))
    comfyui_request_timeout_s: int = int(os.getenv("COMFYUI_REQUEST_TIMEOUT", "15"))
    comfyui_poll_interval_s: float = float(os.getenv("COMFYUI_POLL_INTERVAL", "1.0"))

    # Temporary headless browser (reusable capability; used by one-click extraction).
    browser_timeout_ms: int = int(os.getenv("BROWSER_TIMEOUT_MS", "90000"))

    # Media input not supplied by the caller -> send a NEUTRAL element (a plain
    # white image) instead of letting the workflow's leftover content through.
    neutral_media: bool = _flag("COMFY_NEUTRAL_MEDIA", True)

    # Reconciliation file: declares the named workflows a pipeline may call.
    # The ONLY place ComfyUI node ids / graphs live. Add a model = add an entry.
    catalog_file: Path = Path(os.getenv("COMFY_CATALOG", str(_RESOURCES / "reconciliation.json")))

    # Drop folder: any API-format workflow saved here is auto-discovered and
    # auto-bound (no hand-written bindings). Self-service ingestion from ComfyUI.
    # Ce qu'on extrait du ComfyUI de CETTE machine : des données, pas du code.
    # Les garder dans le paquet publiait les workflows — et les prompts — de son
    # propriétaire.
    workflows_dir: Path = Path(os.getenv("COMFY_WORKFLOWS_DIR", str(_DATA / "workflows")))

    # --- Hermes (hardware reconciliation) ------------------------------------
    # Knowledge base is LOCAL to this pipeline (private _data dir) and SCOPED to
    # the tool it concerns, so it never leaks to / mixes with other pipelines.
    hermes_db: Path = Path(os.getenv("HERMES_DB", str(_DATA / "hermes.sqlite3")))
    hermes_scope: str = os.getenv("HERMES_SCOPE", "comfyui")
    host_id: str = os.getenv("HERMES_HOST", "localhost")
    # "local" consults the local problem registry ; "off" disables reconciliation
    hermes_mode: str = os.getenv("HERMES_MODE", "local")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

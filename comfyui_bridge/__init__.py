"""ComfyUI Bridge — decoupled orchestration service for ComfyUI.

A small hexagonal service that turns declarative *render intents* (prompt,
constraints, duration, format, fps) into ComfyUI executions, without the
high-level business logic ever knowing that ComfyUI exists.

Layers
------
- ``core``    : pure domain + orchestration. Depends on nothing external.
- ``adapter`` : low-level ComfyUI CLI driver + external JSON param mapping.
- ``hermes``  : hardware reconciliation against an execution history (SQLite).
- ``api``     : FastAPI REST surface (RFC 7807 errors).
- ``cli``     : operator CLI, a second thin driver over the same ``core``.
"""

try:  # the distribution metadata is the single source of the version
    from importlib.metadata import version as _v
    __version__ = _v("comfyui-bridge")
except Exception:  # not installed (running from a checkout)
    __version__ = "0+unknown"

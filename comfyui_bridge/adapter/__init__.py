"""Adapter layer — the only code that knows ComfyUI exists.

``mapping`` loads the external param→node binding table, ``injector`` writes
resolved params onto a workflow graph, and ``comfy_cli`` shells out to the
official ComfyUI CLI (or synthesises a manifest in dry-run).
"""

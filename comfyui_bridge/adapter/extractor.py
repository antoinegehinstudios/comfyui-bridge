"""One-click extraction: a saved ComfyUI workflow (UI format) -> API graph.

Runs ComfyUI's OWN ``graphToPrompt`` inside a temporary headless browser — the
reference conversion (correctly flattens subgraphs), never a reimplementation.
ComfyUI-specific glue on top of the generic ``browser`` module.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import BackendExecutionError

# graphToPrompt exists only once the frontend app has finished initialising.
_WAIT_APP = "() => !!(window.app && window.app.graphToPrompt)"

# Fetch the saved UI workflow via ComfyUI's userdata API, load it into the
# graph, and serialise to API format. Returns the {node_id: {class_type,inputs}}.
_EXTRACT_JS = """
async (name) => {
  const key = 'workflows%2F' + encodeURIComponent(name);
  const res = await fetch('/api/userdata/' + key);
  if (!res.ok) throw new Error('userdata fetch ' + res.status);
  const ui = await res.json();
  await window.app.loadGraphData(ui, true, false, 'bridge-extract');
  const p = await window.app.graphToPrompt();
  return p.output;
}
"""


def extract_api_graph(base_url: str, workflow_name: str, timeout_ms: int = 60000) -> dict[str, Any]:
    from ..browser.headless import TemporaryBrowser

    try:
        with TemporaryBrowser(timeout_ms) as b:
            b.goto(base_url.rstrip("/"))
            b.wait_for_function(_WAIT_APP)
            graph = b.evaluate(_EXTRACT_JS, workflow_name)
    except Exception as e:  # navigation / eval / timeout
        raise BackendExecutionError(
            f"headless extraction failed for {workflow_name!r}: {e}"
        ) from e
    if not isinstance(graph, dict) or not graph:
        raise BackendExecutionError(f"graphToPrompt produced no API graph for {workflow_name!r}")
    return graph

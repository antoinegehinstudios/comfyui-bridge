"""Author-given names for a workflow's inputs.

ComfyUI's API export drops what the author wrote: node titles and the labels of
a subgraph's declared inputs. Those are exactly the fields ComfyUI Desktop shows
("Duration", "Width", "Frame Rate"…), so without them our discovery is complete
but unreadable — the user sees `PrimitiveInt · value` instead of `Duration`.

They live in the SAVED (UI-format) workflow, which we can read back from
ComfyUI. Flattened API ids are ``<instance>:<inner_id>``, so the inner id is
enough to find the title again.
"""

from __future__ import annotations

from typing import Any


def titles_from_ui_workflow(ui: Any) -> dict[str, str]:
    """{node id (as written in the UI graph) -> author's title}."""
    titles: dict[str, str] = {}

    def scan(nodes) -> None:
        for n in nodes or []:
            if isinstance(n, dict) and n.get("title") and n.get("id") is not None:
                titles[str(n["id"])] = str(n["title"])

    if not isinstance(ui, dict):
        return titles
    scan(ui.get("nodes"))
    for sub in ((ui.get("definitions") or {}).get("subgraphs") or []):
        scan(sub.get("nodes"))
    return titles


def declared_interface(ui: Any) -> list[dict[str, Any]]:
    """The inputs a subgraph author deliberately exposed, with their labels."""
    out: list[dict[str, Any]] = []
    if not isinstance(ui, dict):
        return out
    for sub in ((ui.get("definitions") or {}).get("subgraphs") or []):
        for i in (sub.get("inputs") or []):
            if not isinstance(i, dict):
                continue
            out.append({
                "name": i.get("name"),
                "label": i.get("label") or i.get("name"),
                "type": i.get("type"),
                "subgraph": sub.get("name"),
            })
    return out


def label_for(api_node_id: str, titles: dict[str, str]) -> str | None:
    """Title of a flattened node: `320:301` -> the title of inner node `301`."""
    if api_node_id in titles:
        return titles[api_node_id]
    inner = api_node_id.rsplit(":", 1)[-1]
    return titles.get(inner)

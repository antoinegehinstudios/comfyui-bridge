"""Workflow catalog — loads the reconciliation file and resolves named workflows.

The reconciliation file (``reconciliation.json``) DECLARES the workflows a
pipeline may call, keyed by name:

    {
      "version": 1,
      "default": "sd15-txt2img",
      "workflows": {
        "sd15-txt2img": {
          "kind": "image",
          "workflow": "workflow_template.json",
          "defaults": { "steps": 20, "cfg": 7.0, "width": 768, "height": 768 },
          "limits":   { "max_signature": 1310720 },
          "bindings": { "prompt": { "node": "6", "input": "text" }, ... }
        },
        "z-image-turbo": { ... }
      }
    }

A caller selects a workflow by name; the adapter resolves it here. Adding a new
model = adding an entry (workflow file + bindings + defaults) — no code change.
This object is BOTH the domain-facing ``WorkflowRegistry`` (abstract profiles)
and the adapter-facing source of graphs/bindings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re

from ..core.errors import IntentValidationError, WorkflowMappingError
from ..core.workflow import WorkflowProfile
from . import autobind
from .mapping import Binding

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    kind: str
    workflow_path: Path
    bindings: dict[str, Binding]
    defaults: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    # Maintained manifest metadata (managed/extracted workflows):
    source: str | None = None                       # ComfyUI workflow it came from
    source_hash: str | None = None                  # source hash at extraction (MAJ detection)
    extracted_at: str | None = None                 # WHEN this analysis was made
    dependencies: dict[str, list[str]] = field(default_factory=dict)  # {models, node_types}

    carried: dict[str, Any] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)

    @property
    def profile(self) -> WorkflowProfile:
        from .neutral import NEUTRAL_NAMES
        return WorkflowProfile(self.name, self.kind, dict(self.defaults),
                               dict(self.limits), dict(self.carried),
                               tuple(sorted(self.bindings)),
                               # Media inputs for which a neutral element exists:
                               # for the others, saying "neutral was sent" would
                               # be false and the workflow's own content is used.
                               tuple(sorted(set(NEUTRAL_NAMES) & set(self.bindings))))


class WorkflowCatalog:
    # (méthodes de lecture plus bas ; l'ingestion et le retrait encadrent le cycle
    #  de vie d'un extrait : ce qui s'ajoute doit pouvoir se retirer.)
    def __init__(self, default: str, specs: dict[str, WorkflowSpec],
                 workflows_dir: Path | None = None) -> None:
        self._default = default
        self._specs = specs
        self._workflows_dir = Path(workflows_dir) if workflows_dir else None
        self._templates: dict[str, dict[str, Any]] = {}
        # Noms servis par une entrée déclarée alors qu'un graphe enregistré
        # porte le même : ce qui est masqué doit pouvoir être dit.
        self.shadowed: tuple[str, ...] = ()

    # -- maintained manifest (provenance + dependencies) ----------------------

    def _index_path(self) -> Path | None:
        return (self._workflows_dir / "index.json") if self._workflows_dir else None

    def _read_index(self) -> dict[str, Any]:
        p = self._index_path()
        if p and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("workflows", {})
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _write_index(self, workflows: dict[str, Any]) -> None:
        p = self._index_path()
        if p:
            p.write_text(json.dumps({"version": 1, "workflows": workflows}, indent=2), encoding="utf-8")

    def manifest(self, name: str) -> dict[str, Any]:
        return self._read_index().get(name, {})

    # -- self-service ingestion ----------------------------------------------

    def register(self, name: str, graph: dict[str, Any], source: str | None = None,
                 source_hash: str | None = None, extracted_at: str | None = None,
                 titles: dict[str, str] | None = None) -> WorkflowSpec:
        """Ingest a ComfyUI API graph: auto-bind it, derive its dependencies,
        record its provenance, persist it, make it callable.

        No hand-written bindings — ``autobind`` derives them from the graph. The
        drop folder + ``index.json`` are the maintained reconciliation/dependency
        manifest: name, kind, source, source hash (for update detection), and
        the models + node types the workflow needs.
        """
        if not autobind.looks_like_api_graph(graph):
            raise WorkflowMappingError(
                "not a ComfyUI API graph — in ComfyUI use 'Export (API)', not a plain save "
                "(UI format with 'nodes'/'links' is not accepted)"
            )
        name = _SAFE_NAME.sub("-", name).strip("-") or "workflow"
        meta = {
            "source": source,
            "source_hash": source_hash,
            "extracted_at": extracted_at,
            # The author's own node names, lost by the API export. Kept because
            # they are what ComfyUI Desktop shows and what makes the discovered
            # inputs readable ("Duration", not "PrimitiveInt · value").
            "titles": dict(titles or {}),
        }
        if self._workflows_dir is not None:
            self._workflows_dir.mkdir(parents=True, exist_ok=True)
            path = self._workflows_dir / f"{name}.json"
            path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
            index = self._read_index()
            index[name] = meta
            self._write_index(index)
        else:
            path = Path(f"{name}.json")
        spec = _spec_from_graph(name, path, graph, meta)
        self._specs[name] = spec
        self._templates[name] = graph
        return spec

    def unregister(self, name: str) -> dict[str, Any]:
        """Retirer un extrait du catalogue — l'inverse de ``register``.

        Ce qui s'ingère doit pouvoir se retirer : une source supprimée dans
        ComfyUI laissait sinon un extrait orphelin, toujours appelable et que
        rien ne pouvait sortir de la liste. Seul ce que NOUS avons écrit est
        effacé ; une entrée déclarée à la main dans le fichier de réconciliation
        n'est pas à nous et est refusée.
        """
        spec = self._specs.get(name)
        if spec is None:
            raise IntentValidationError(f"unknown workflow {name!r}", available=self.names())
        if self._workflows_dir is None or not spec.source:
            raise WorkflowMappingError(
                f"{name!r} n'a pas été ingéré ici : il vient du fichier de "
                f"réconciliation et ne peut pas être retiré par l'API", workflow=name)
        index = self._read_index()
        index.pop(name, None)
        self._write_index(index)
        fichier = self._workflows_dir / f"{name}.json"
        fichier.unlink(missing_ok=True)
        self._specs.pop(name, None)
        self._templates.pop(name, None)
        return {"removed": name, "graph_file": str(fichier), "source": spec.source}

    # -- WorkflowRegistry port (domain-facing) --------------------------------

    def get_profile(self, name: str | None) -> WorkflowProfile:
        return self.get_spec(name).profile

    def names(self) -> list[str]:
        return list(self._specs)

    def default_name(self) -> str:
        return self._default

    # -- adapter-facing -------------------------------------------------------

    def get_spec(self, name: str | None) -> WorkflowSpec:
        key = name or self._default
        spec = self._specs.get(key)
        if spec is None:
            raise IntentValidationError(
                f"unknown workflow {key!r}", available=self.names(),
            )
        return spec

    def load_template(self, spec: WorkflowSpec) -> dict[str, Any]:
        if spec.name not in self._templates:
            try:
                self._templates[spec.name] = json.loads(spec.workflow_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowMappingError(
                    f"workflow {spec.name!r}: cannot read {spec.workflow_path}: {exc}"
                ) from exc
        return self._templates[spec.name]


_MEDIA_PARAMS = ("image", "audio", "video")


def _carried_media(graph: dict[str, Any], bindings: dict[str, Binding]) -> dict[str, Any]:
    """Media values already sitting in the graph, per bound media param."""
    out: dict[str, Any] = {}
    for param in _MEDIA_PARAMS:
        b = bindings.get(param)
        if not b:
            continue
        val = ((graph.get(b.node) or {}).get("inputs") or {}).get(b.input)
        if isinstance(val, str) and val:
            out[param] = val
    return out


def _spec_from_graph(name: str, path: Path, graph: dict[str, Any],
                     meta: dict[str, Any] | None = None) -> WorkflowSpec:
    meta = meta or {}
    titles = dict(meta.get("titles") or {})
    bindings = autobind.derive_bindings(graph, titles)
    return WorkflowSpec(
        name=name,
        # Derived from the graph on every read — a stored copy would go stale
        # the moment the file is replaced in the drop folder.
        kind=autobind.infer_kind(graph),
        workflow_path=Path(path),
        bindings=bindings,
        # No defaults: the graph already holds its author's values. Copying
        # them here would be a second source that can disagree with it.
        defaults={},
        limits={},
        source=meta.get("source"),
        source_hash=meta.get("source_hash"),
        extracted_at=meta.get("extracted_at"),
        # Always read from the graph (the single source); never a stored copy.
        dependencies=autobind.derive_dependencies(graph),
        carried=_carried_media(graph, bindings),
        titles=titles,
    )


def build_injection(catalog: "WorkflowCatalog", plan) -> dict[str, Any]:
    """Resolve a plan's workflow and inject its params — the shared preview.

    Used by both backends' ``preview`` so the graph view is identical whether
    the engine is the CLI or the HTTP API.
    """
    from .injector import apply_overrides, inject

    spec = catalog.get_spec(plan.workflow)
    template = catalog.load_template(spec)
    graph = apply_overrides(inject(template, spec.bindings, plan.params), plan.overrides)
    applied = [
        {
            "param": key, "node": b.node, "input": b.input,
            "class_type": (template.get(b.node) or {}).get("class_type"),
            "value": plan.params[key],
        }
        for key, b in spec.bindings.items()
        if key in plan.params
    ]
    touched = {a["node"] for a in applied}
    nodes = [
        {"id": nid, "class_type": node.get("class_type"), "touched": nid in touched}
        for nid, node in graph.items()
    ]
    return {
        "workflow_name": spec.name,
        "workflow_source": str(spec.workflow_path),
        "bindings_applied": applied,
        "nodes": nodes,
        "workflow": graph,
    }


def _graph_of(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_catalog(path: str | Path, workflows_dir: str | Path | None = None,
                 data_dir: str | Path | None = None) -> WorkflowCatalog:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"cannot read reconciliation file {path}: {exc}") from exc

    # Les workflows propres à CETTE machine vivent à côté, jamais dans le paquet.
    from .local_overlay import merge, read_overlay
    try:
        data = merge(data, read_overlay(Path(data_dir) if data_dir else None, path), "workflows")
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowMappingError(f"cannot read local reconciliation overlay: {exc}") from exc

    workflows = data.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise WorkflowMappingError(f"{path}: 'workflows' must be a non-empty object")

    specs: dict[str, WorkflowSpec] = {}
    for name, entry in workflows.items():
        if not isinstance(entry, dict) or "workflow" not in entry or "bindings" not in entry:
            raise WorkflowMappingError(f"workflow {name!r}: needs 'workflow' and 'bindings'")
        bindings = {}
        for k, spec in entry["bindings"].items():
            if not isinstance(spec, dict) or "node" not in spec or "input" not in spec:
                raise WorkflowMappingError(f"workflow {name!r}: binding {k!r} needs 'node' and 'input'")
            bindings[k] = Binding(node=str(spec["node"]), input=str(spec["input"]))
        wf_path = (path.parent / entry["workflow"]).resolve()
        # Derive dependencies from the declared graph too (for deps checks).
        deps: dict[str, list[str]] = {}
        try:
            deps = autobind.derive_dependencies(json.loads(wf_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
        specs[name] = WorkflowSpec(
            name=name,
            kind=str(entry.get("kind", "image")),
            workflow_path=wf_path,
            bindings=bindings,
            defaults=dict(entry.get("defaults", {})),
            limits=dict(entry.get("limits", {})),
            dependencies=deps,
            carried=_carried_media(_graph_of(wf_path), bindings),
        )

    default = data.get("default") or next(iter(specs))
    if default not in specs:
        raise WorkflowMappingError(f"{path}: default {default!r} not among declared workflows")

    # Les graphes déposés dans le dossier des workflows sont découverts seuls.
    # Sur un nom déjà DÉCLARÉ, l'entrée déclarée l'emporte — ses liaisons sont
    # écrites à la main, donc voulues. Mais le masquage était silencieux : un
    # workflow enregistré par l'API répondait « praticable » jusqu'au
    # redémarrage, puis cédait la place sans un mot. Les collisions sont donc
    # retenues et exposées.
    masques: list[str] = []
    if workflows_dir:
        wd = Path(workflows_dir)
        index: dict[str, Any] = {}
        idx_file = wd / "index.json"
        if idx_file.exists():
            try:
                index = json.loads(idx_file.read_text(encoding="utf-8")).get("workflows", {})
            except (OSError, json.JSONDecodeError):
                index = {}
        if wd.is_dir():
            for f in sorted(wd.glob("*.json")):
                if f.name == "index.json":
                    continue
                if f.stem in specs:
                    masques.append(f.stem)
                    continue
                try:
                    graph = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if autobind.looks_like_api_graph(graph):
                    specs[f.stem] = _spec_from_graph(f.stem, f, graph, index.get(f.stem))

    catalogue = WorkflowCatalog(default=default, specs=specs,
                                workflows_dir=Path(workflows_dir) if workflows_dir else None)
    catalogue.shadowed = tuple(masques)
    return catalogue

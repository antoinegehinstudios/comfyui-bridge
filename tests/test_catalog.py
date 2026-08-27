from comfyui_bridge.adapter.catalog import build_injection, load_catalog
from comfyui_bridge.config import Settings
from comfyui_bridge.container import build_container
from comfyui_bridge.core.errors import IntentValidationError
from comfyui_bridge.core.intention import RenderIntent
from comfyui_bridge.core.plan import ExecutionPlan


def _catalog():
    return load_catalog(Settings().catalog_file)


def test_catalog_declares_named_workflows():
    cat = _catalog()
    assert "sd15-txt2img" in cat.names()
    assert "z-image-turbo" in cat.names()
    assert cat.default_name() == "sd15-txt2img"
    prof = cat.get_profile("z-image-turbo")
    assert prof.kind == "image"
    assert prof.defaults["steps"] == 8 and prof.defaults["cfg"] == 1.0


def test_unknown_workflow_raises():
    try:
        _catalog().get_spec("does-not-exist")
    except IntentValidationError as e:
        assert "does-not-exist" in str(e)
        assert e.extensions.get("available")
    else:
        raise AssertionError("expected IntentValidationError")


def test_named_workflow_injects_its_own_graph():
    cat = _catalog()
    params = {"prompt": "hi there", "width": 1024, "height": 1024, "latent_batch": 1,
              "steps": 8, "cfg": 1.0, "seed": 0, "filename_prefix": "t", "negative_prompt": "", "fps": 8}
    plan = ExecutionPlan(RenderIntent(prompt="hi there", workflow="z-image-turbo"),
                         params, kind="image", workflow="z-image-turbo")
    pv = build_injection(cat, plan)
    assert pv["workflow_name"] == "z-image-turbo"
    # z-image encodes the prompt at node 27 (sd15 uses node 6) -> proves the right graph
    assert any(b["node"] == "27" for b in pv["bindings_applied"])
    assert pv["workflow"]["27"]["inputs"]["text"] == "hi there"


def test_workflow_defaults_apply_when_intent_omits_them(tmp_path):
    c = build_container(Settings(comfy_backend="cli", dry_run=True, comfy_output_dir=tmp_path, hermes_db=tmp_path / "h.sqlite3"))
    plan = c.orchestrator.build_plan(RenderIntent(prompt="x", workflow="z-image-turbo"))
    assert plan.workflow == "z-image-turbo" and plan.kind == "image"
    assert plan.params["steps"] == 8      # inherited from the workflow's defaults
    assert plan.params["cfg"] == 1.0
    assert plan.params["width"] == 1024


def test_workflow_kind_drives_frame_count(tmp_path):
    """A video workflow must resolve to video: frames ride the batch axis."""
    from comfyui_bridge.adapter.catalog import load_catalog
    # workflows_dir MUST be a temp dir: without it this test wrote `vid.json`
    # into the shipped catalog, and a test fixture showed up as a real workflow.
    c = build_container(Settings(comfy_backend="cli", dry_run=True,
                                 comfy_output_dir=tmp_path, hermes_db=tmp_path / "h.sqlite3",
                                 workflows_dir=tmp_path / "wf"))
    graph = {
        "1": {"class_type": "EmptyLTXVLatentVideo",
              "inputs": {"width": 512, "height": 512, "length": 25, "batch_size": 1}},
        "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "v", "fps": 24}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    c.catalog.register("vid", graph)
    plan = c.orchestrator.build_plan(RenderIntent(prompt="x", workflow="vid", duration_s=2, fps=12))
    assert plan.kind == "video"
    assert plan.params["latent_batch"] == 24   # 2 s x 12 fps
    assert plan.config.endswith("x24")


def test_a_registered_graph_shadowed_by_a_declaration_is_named(tmp_path):
    """Une entrée déclarée l'emporte sur un graphe enregistré du même nom. Le
    masquage était silencieux : l'enregistrement semblait réussir, puis cédait
    la place au redémarrage."""
    import json

    from comfyui_bridge.adapter.catalog import load_catalog

    graphe = {"3": {"class_type": "KSampler",
                    "inputs": {"seed": 1, "steps": 20, "cfg": 7.0, "denoise": 1.0}},
              "9": {"class_type": "SaveImage",
                    "inputs": {"filename_prefix": "x", "images": ["3", 0]}}}
    dossier = tmp_path / "workflows"
    dossier.mkdir()
    (dossier / "collision.json").write_text(json.dumps(graphe), encoding="utf-8")
    (tmp_path / "libre.json").write_text(json.dumps(graphe), encoding="utf-8")
    (dossier / "libre.json").write_text(json.dumps(graphe), encoding="utf-8")

    declare = tmp_path / "reconciliation.json"
    declare.write_text(json.dumps({
        "default": "collision",
        "workflows": {"collision": {"kind": "image", "workflow": "libre.json",
                                    "bindings": {"seed": {"node": "3", "input": "seed"}}}},
    }), encoding="utf-8")

    cat = load_catalog(declare, dossier)
    assert cat.shadowed == ("collision",)          # nommé…
    assert "libre" in cat.names()                  # …et le reste est bien découvert

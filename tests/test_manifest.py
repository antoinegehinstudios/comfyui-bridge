from comfyui_bridge.adapter import autobind
from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.config import Settings


def test_derive_dependencies_from_graph():
    cat = load_catalog(Settings().catalog_file)
    zimg = cat.load_template(cat.get_spec("z-image-turbo"))
    deps = autobind.derive_dependencies(zimg)
    assert "z_image_turbo_bf16.safetensors" in deps["models"]
    assert "qwen_3_4b.safetensors" in deps["models"]
    assert "ae.safetensors" in deps["models"]
    assert "UNETLoader" in deps["node_types"]
    assert "KSampler" in deps["node_types"]


def test_register_records_provenance_and_deps_and_persists(tmp_path):
    cat = load_catalog(Settings().catalog_file, workflows_dir=tmp_path)
    graph = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "foo.safetensors"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0,
              "positive": ["6", 0], "negative": ["6", 0], "latent_image": ["4", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["8", 0]}},
    }
    spec = cat.register("flow", graph, source="video_x.json", source_hash="abc123")
    assert spec.source == "video_x.json" and spec.source_hash == "abc123"
    assert "foo.safetensors" in spec.dependencies["models"]
    assert "CheckpointLoaderSimple" in spec.dependencies["node_types"]
    assert (tmp_path / "index.json").exists()

    # Provenance + deps survive a reload (the manifest is maintained on disk).
    reloaded = load_catalog(Settings().catalog_file, workflows_dir=tmp_path)
    s2 = reloaded.get_spec("flow")
    assert s2.source == "video_x.json" and s2.source_hash == "abc123"
    assert "foo.safetensors" in s2.dependencies["models"]


def test_comfyui_own_validation_is_the_source_of_truth():
    """We do not re-check dependencies: ComfyUI validates and we classify ITS answer."""
    from comfyui_bridge.core.problems import MISSING_MODEL, classify
    real = ("ComfyUI rejected the graph (HTTP 400): Prompt outputs failed validation "
            "{'4': {'errors': [{'type': 'value_not_in_list', 'details': "
            "\"ckpt_name: 'v1-5-pruned-emaonly.safetensors' not in [...]\"}]}}")
    assert classify(real) == MISSING_MODEL

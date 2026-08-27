from comfyui_bridge.adapter import autobind
from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.config import Settings
from comfyui_bridge.core.errors import WorkflowMappingError


def _templates():
    cat = load_catalog(Settings().catalog_file)
    sd15 = cat.load_template(cat.get_spec("sd15-txt2img"))
    zimg = cat.load_template(cat.get_spec("z-image-turbo"))
    return sd15, zimg


def test_autobind_sd15():
    sd15, _ = _templates()
    b = autobind.derive_bindings(sd15)
    assert b["prompt"].node == "6" and b["prompt"].input == "text"
    assert b["negative_prompt"].node == "7"
    assert b["width"].node == "5" and b["height"].node == "5"
    assert b["latent_batch"].node == "5" and b["latent_batch"].input == "batch_size"
    assert b["seed"].node == "3" and b["steps"].node == "3" and b["cfg"].node == "3"
    assert b["filename_prefix"].node == "9"
    assert autobind.infer_kind(sd15) == "image"


def test_autobind_zimage_traces_positive_only():
    _, zimg = _templates()
    b = autobind.derive_bindings(zimg)
    # z-image encodes the prompt at node 27; its negative is a ZeroOut of the same
    # conditioning -> no separate negative prompt should be bound.
    assert b["prompt"].node == "27"
    assert "negative_prompt" not in b
    assert b["width"].node == "13" and b["latent_batch"].input == "batch_size"


def test_infer_kind_video_from_length():
    graph = {"5": {"class_type": "EmptyLTXVLatentVideo",
                   "inputs": {"width": 704, "height": 448, "length": 49, "batch_size": 1}}}
    assert autobind.infer_kind(graph) == "video"
    b = autobind.derive_bindings(graph)
    assert b["latent_batch"].node == "5" and b["latent_batch"].input == "length"


def test_looks_like_api_graph_rejects_ui_format():
    assert autobind.looks_like_api_graph({"6": {"class_type": "X", "inputs": {}}})
    assert not autobind.looks_like_api_graph({"nodes": [], "links": []})
    assert not autobind.looks_like_api_graph([1, 2, 3])


def test_register_ingests_and_makes_callable(tmp_path):
    cat = load_catalog(Settings().catalog_file, workflows_dir=tmp_path)
    graph = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0,
              "positive": ["6", 0], "negative": ["6", 0], "latent_image": ["5", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["8", 0]}},
    }
    spec = cat.register("my imported flow!", graph)
    assert spec.name == "my-imported-flow"          # sanitised
    assert (tmp_path / "my-imported-flow.json").exists()
    assert cat.get_spec("my-imported-flow").bindings["prompt"].node == "6"
    # An imported workflow declares NO defaults: its graph already holds them.
    assert spec.defaults == {}


def test_register_rejects_ui_format(tmp_path):
    cat = load_catalog(Settings().catalog_file, workflows_dir=tmp_path)
    try:
        cat.register("bad", {"nodes": [], "links": []})
    except WorkflowMappingError as e:
        assert "API" in str(e)
    else:
        raise AssertionError("expected WorkflowMappingError")


def test_prompt_is_never_bound_to_the_negative_side():
    """Measured on a real LTX workflow: the caller's prompt was injected into
    the node feeding `negative`, so the model was told to AVOID what was asked
    and the video had nothing to do with the request."""
    graph = {
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": ["gen", 0]}},   # built elsewhere
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly, cartoon"}},
        "cond": {"class_type": "LTXVConditioning",
                 "inputs": {"positive": ["pos", 0], "negative": ["neg", 0]}},
    }
    b = autobind.derive_bindings(graph)
    # the only literal text node is the negative one -> no prompt binding at all
    assert "prompt" not in b
    assert b["negative_prompt"].node == "neg"


def test_negative_derived_from_the_positive_still_allows_a_prompt():
    """z-image style: the negative is a ZeroOut of the positive text, so that
    text node must stay usable for the prompt."""
    graph = {
        "27": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "33": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["27", 0]}},
        "3": {"class_type": "KSampler",
              "inputs": {"positive": ["27", 0], "negative": ["33", 0], "seed": 0}},
    }
    b = autobind.derive_bindings(graph)
    assert b["prompt"].node == "27"


def test_prompt_found_on_any_node_wired_to_the_positive_side():
    """Measured on a real LTX workflow: the prompt lived in a
    PrimitiveStringMultiline feeding the positive side, so a CLIPTextEncode-only
    scan missed it and the workflow silently ran its own baked prompt."""
    graph = {
        "319": {"class_type": "PrimitiveStringMultiline",
                "inputs": {"value": "Egyptian royal in blue-and-gold headdress"}},
        "327": {"class_type": "TextGenerateLTX2Prompt", "inputs": {"prompt": ["319", 0]}},
        "313": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly, cartoon"}},
        "304": {"class_type": "LTXVConditioning",
                "inputs": {"positive": ["327", 0], "negative": ["313", 0]}},
        "269": {"class_type": "LoadImage", "inputs": {"image": "poster.png"}},
    }
    b = autobind.derive_bindings(graph)
    assert (b["prompt"].node, b["prompt"].input) == ("319", "value")
    assert (b["negative_prompt"].node, b["negative_prompt"].input) == ("313", "text")
    assert (b["image"].node, b["image"].input) == ("269", "image")


def test_frames_never_land_on_a_video_batch_size():
    """A video latent node has `length` (frames) AND `batch_size` (how many
    clips). When `length` is computed by the workflow, the frame count is not
    ours to set — binding it to batch_size turned "2 s at 24 fps" into 48
    simultaneous videos, which killed the engine and delivered nothing."""
    graph = {
        "295": {"class_type": "EmptyLTXVLatentVideo",
                "inputs": {"width": ["a", 0], "height": ["a", 1],
                           "length": ["calc", 0], "batch_size": 1}},
    }
    assert "latent_batch" not in autobind.derive_bindings(graph)


def test_frames_bind_to_length_when_the_workflow_exposes_it():
    graph = {"5": {"class_type": "EmptyLTXVLatentVideo",
                   "inputs": {"width": 512, "height": 512, "length": 25, "batch_size": 1}}}
    b = autobind.derive_bindings(graph)
    assert (b["latent_batch"].node, b["latent_batch"].input) == ("5", "length")


def test_image_batch_still_binds_to_batch_size():
    graph = {"5": {"class_type": "EmptyLatentImage",
                   "inputs": {"width": 512, "height": 512, "batch_size": 1}}}
    b = autobind.derive_bindings(graph)
    assert (b["latent_batch"].node, b["latent_batch"].input) == ("5", "batch_size")


def test_author_titles_drive_the_semantic_binding():
    """The API export drops the author's node titles, so a heuristic could not
    see that a PrimitiveInt IS the video duration — ComfyUI Desktop shows it
    because the author named it. Reading those titles closes the gap."""
    graph = {
        "320:301": {"class_type": "PrimitiveInt", "inputs": {"value": 5}},
        "320:300": {"class_type": "PrimitiveInt", "inputs": {"value": 25}},
        "320:312": {"class_type": "PrimitiveInt", "inputs": {"value": 1280}},
    }
    titles = {"301": "Duration", "300": "Frame Rate", "312": "Width"}
    b = autobind.derive_bindings(graph, titles)
    assert (b["duration_s"].node, b["duration_s"].input) == ("320:301", "value")
    assert b["fps"].node == "320:300"
    assert b["width"].node == "320:312"


def test_titles_of_the_form_type_parenthesis_name_are_read():
    """ComfyUI titles a converted widget "Float (duration)": the meaning is in
    the parentheses. Reading only the whole string left a real workflow without
    any duration field — the UI then offered less than ComfyUI itself."""
    graph = {
        "140:133": {"class_type": "PrimitiveFloat", "inputs": {"value": 12}},
        "140:130": {"class_type": "CreateVideo", "inputs": {"fps": 24}},
    }
    b = autobind.derive_bindings(graph, {"133": "Float (duration)"})
    assert (b["duration_s"].node, b["duration_s"].input) == ("140:133", "value")


def test_a_titled_node_whose_inputs_are_all_links_is_not_bound():
    """"If/Else Switch (Steps)" names a switch, not a settable value: every one
    of its inputs comes from another node. Binding it would send the caller's
    value nowhere."""
    graph = {
        "140:136": {"class_type": "ComfySwitchNode",
                    "inputs": {"switch": ["140:139", 0], "on_false": ["140:137", 0]}},
    }
    b = autobind.derive_bindings(graph, {"136": "If/Else Switch (Steps)"})
    assert "steps" not in b


def test_a_workflow_that_starts_from_a_video_exposes_that_input():
    """A frame-interpolation workflow accepted nothing at all: its source video
    lives in a LoadVideo node, which the image-only scan never looked at."""
    graph = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": "clip.mp4"}},
        "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "out", "video": ["1", 0]}},
    }
    b = autobind.derive_bindings(graph, {})
    assert (b["video"].node, b["video"].input) == ("1", "file")


def test_an_audio_workflow_exposes_its_duration():
    """Its length lives in `EmptyLatentAudio.seconds` — ComfyUI's own name for
    it. Unbound, the main parameter of an audio render could not be set."""
    graph = {
        "4": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": 8.0, "batch_size": 1}},
        "7": {"class_type": "SaveAudio", "inputs": {"filename_prefix": "audio/x",
                                                    "audio": ["6", 0]}},
    }
    b = autobind.derive_bindings(graph, {})
    assert (b["duration_s"].node, b["duration_s"].input) == ("4", "seconds")
    assert autobind.infer_kind(graph) == "audio"

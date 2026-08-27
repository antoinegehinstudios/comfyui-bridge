from comfyui_bridge.adapter.injector import inject
from comfyui_bridge.adapter.mapping import Binding
from comfyui_bridge.core.errors import WorkflowMappingError

WF = {
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 768, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
}
BINDINGS = {
    "prompt": Binding("6", "text"),
    "width": Binding("5", "width"),
    "latent_batch": Binding("5", "batch_size"),
    "fps": Binding("99", "frame_rate"),  # intentionally dangling
}


def test_inject_sets_bound_params_and_ignores_unmapped():
    out = inject(WF, {k: BINDINGS[k] for k in ("prompt", "width", "latent_batch")},
                 {"prompt": "hello", "width": 512, "latent_batch": 4, "steps": 20})
    assert out["6"]["inputs"]["text"] == "hello"
    assert out["5"]["inputs"]["width"] == 512
    assert out["5"]["inputs"]["batch_size"] == 4
    # source workflow untouched (deep copy)
    assert WF["5"]["inputs"]["width"] == 768


def test_missing_param_is_skipped_not_raised():
    out = inject(WF, {"prompt": BINDINGS["prompt"]}, {})  # no 'prompt' in params
    assert out["6"]["inputs"]["text"] == ""


def test_dangling_binding_raises():
    try:
        inject(WF, {"fps": BINDINGS["fps"]}, {"fps": 24})
    except WorkflowMappingError as exc:
        assert exc.status == 500
    else:
        raise AssertionError("expected WorkflowMappingError")


def test_a_raw_input_that_points_nowhere_blames_the_caller_not_the_server():
    """Addressing a node the workflow does not have is a request mistake: it
    used to come back as 500, blaming the service for a typo."""
    import pytest
    from comfyui_bridge.adapter.injector import apply_overrides
    from comfyui_bridge.core.errors import UnknownWorkflowInputError

    graph = {"3": {"class_type": "KSampler", "inputs": {"steps": 20}}}
    with pytest.raises(UnknownWorkflowInputError) as absent_node:
        apply_overrides(graph, {"99.steps": 5})
    assert absent_node.value.status == 422
    with pytest.raises(UnknownWorkflowInputError):
        apply_overrides(graph, {"3.nope": 5})
    # …and a real target is simply applied.
    assert apply_overrides(graph, {"3.steps": 5})["3"]["inputs"]["steps"] == 5


def test_the_output_name_reaches_every_saving_node():
    """Un workflow qui délivre une image ET une mesure a deux nœuds de
    sauvegarde. N'en piloter qu'un laissait la moitié des livrables hors du nom
    demandé — introuvables pour l'appelant qui les cherche."""
    from comfyui_bridge.adapter.injector import inject
    from comfyui_bridge.adapter.mapping import Binding

    graphe = {
        "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "regard/annote"}},
        "5": {"class_type": "SaveText", "inputs": {"filename_prefix": "faits"}},
        "6": {"class_type": "KSampler", "inputs": {"steps": 20}},
    }
    sorti = inject(graphe, {"filename_prefix": Binding("4", "filename_prefix"),
                            "steps": Binding("6", "steps")},
                   {"filename_prefix": "cortex/analyse", "steps": 8})
    assert sorti["4"]["inputs"]["filename_prefix"] == "cortex/analyse"
    assert sorti["5"]["inputs"]["filename_prefix"] == "cortex/analyse"   # celui-ci était oublié
    assert sorti["6"]["inputs"]["steps"] == 8        # les autres restent liés à leur nœud

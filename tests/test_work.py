"""How the load of a run is counted — the shape the estimate is fitted on."""

import pytest

from comfyui_bridge.adapter.work import _total_steps, work_units


def test_steps_are_summed_over_every_sampling_pass():
    """A real LTX workflow runs two passes driven by explicit sigma lists and
    carries no `steps` input at all: read through the bindings only, it looked
    like a single step and the estimate never moved when they changed."""
    graph = {
        "1": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.85, 0.72, 0.42, 0.0"}},
        "2": {"class_type": "SamplerCustomAdvanced", "inputs": {"sigmas": ["1", 0]}},
        "3": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1.0, 0.5, 0.0"}},
        "4": {"class_type": "SamplerCustomAdvanced", "inputs": {"sigmas": ["3", 0]}},
        "5": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
    }
    assert _total_steps(graph) == 3 + 2       # N sigmas describe N-1 steps


def test_a_schedule_split_across_two_samplers_is_not_counted_twice():
    """Wan 2.2 hands one 20-step schedule to two samplers, 0->10 then 10->end."""
    graph = {
        "1": {"class_type": "KSamplerAdvanced",
              "inputs": {"steps": 20, "start_at_step": 0, "end_at_step": 10}},
        "2": {"class_type": "KSamplerAdvanced",
              "inputs": {"steps": 20, "start_at_step": 10, "end_at_step": 10000}},
    }
    assert _total_steps(graph) == 20


def test_a_step_count_behind_a_switch_is_read_from_the_branch_taken():
    """The step count sat behind a scheduler AND a boolean switch: unread, a
    workflow's whole load looked like one step."""
    graph = {
        "sampler": {"class_type": "SamplerCustomAdvanced", "inputs": {"sigmas": ["sched", 0]}},
        "sched": {"class_type": "BasicScheduler", "inputs": {"steps": ["switch", 0], "denoise": 1}},
        "switch": {"class_type": "ComfySwitchNode",
                   "inputs": {"switch": ["flag", 0], "on_false": ["slow", 0], "on_true": ["fast", 0]}},
        "flag": {"class_type": "PrimitiveBoolean", "inputs": {"value": False}},
        "slow": {"class_type": "PrimitiveInt", "inputs": {"value": 20}},
        "fast": {"class_type": "PrimitiveInt", "inputs": {"value": 8}},
    }
    assert _total_steps(graph) == 20
    graph["flag"]["inputs"]["value"] = True
    assert _total_steps(graph) == 8


def test_an_unreadable_factor_is_neutral_not_annulling():
    """A workflow whose size is fixed in its graph still varies with the number
    of frames: 24 frames took 134 s and 96 frames took 411 s, while the estimate
    said the same thing because the load came out as nothing at all."""
    small = work_units({"width": None, "height": None, "frames": 24, "steps": 20})
    big = work_units({"width": None, "height": None, "frames": 96, "steps": 20})
    assert small and big and big == 4 * small
    # Surface counts when it is readable…
    assert work_units({"width": 1024, "height": 1024, "frames": 1, "steps": 1}) == pytest.approx(1.048576)
    # …and nothing readable at all stays silent.
    assert work_units({"width": None, "height": None, "frames": None, "steps": None}) is None


def test_a_machine_limit_is_not_shown_as_a_bound():
    """A Primitive node declares min=-2^63: true, and useless. Shown as a bound
    it filled the form with ±9223372036854775808."""
    from comfyui_bridge.adapter.mapping import Binding
    from comfyui_bridge.adapter.workflow_io import intent_inputs

    io = [{"node": "1", "input": "value", "type": "INT", "value": 512,
           "min": -2 ** 63, "max": 2 ** 63 - 1},
          {"node": "2", "input": "width", "type": "INT", "value": 512,
           "min": 16, "max": 16384}]
    contract = {e["field"]: e for e in intent_inputs(io, {"width": Binding("1", "value"),
                                                         "height": Binding("2", "width")}, "image")}
    assert "min" not in contract["width"] and "max" not in contract["width"]
    assert (contract["height"]["min"], contract["height"]["max"]) == (16, 16384)


def test_seconds_of_audio_are_a_produced_quantity_like_frames():
    """A 30 s track and a 5 s one weighed the same: only frames were counted,
    and an audio latent has none."""
    from comfyui_bridge.adapter.work import _produced_quantity

    audio = {"4": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": 30, "batch_size": 1}}}
    video = {"4": {"class_type": "EmptyLatentVideo", "inputs": {"length": 48}}}
    assert _produced_quantity(audio) == 30
    assert _produced_quantity(video) == 48

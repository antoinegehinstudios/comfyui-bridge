"""The websocket watcher relays ComfyUI's own progress — and only that."""

import json

from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.adapter.comfy_http import ComfyUIHttpBackend
from comfyui_bridge.config import Settings


class _WSTimeout(Exception):
    """Stands in for websocket's WebSocketTimeoutException (matched by name)."""


class WebSocketTimeoutException(_WSTimeout):
    pass


class FakeWS:
    """Replays a scripted stream; a `None` entry means 'read timed out'."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def settimeout(self, _t):
        pass

    def recv(self):
        if not self._frames:
            raise WebSocketTimeoutException()
        f = self._frames.pop(0)
        if f is None:
            raise WebSocketTimeoutException()
        return f


def _backend(tmp_path):
    s = Settings(comfy_backend="http", comfy_output_dir=tmp_path, comfyui_total_timeout_s=5)
    return ComfyUIHttpBackend(s, load_catalog(s.catalog_file))


def _msg(kind, data):
    return json.dumps({"type": kind, "data": data})


def test_silence_is_not_the_end_of_the_run(tmp_path):
    """Loading a big model emits nothing for minutes: a read timeout must not
    be mistaken for completion (the bug this test pins)."""
    seen = []
    frames = [
        None, None,                                            # long silence
        _msg("progress", {"value": 3, "max": 8, "node": "3", "prompt_id": "p1"}),
        None,                                                  # more silence
        _msg("progress", {"value": 8, "max": 8, "node": "3", "prompt_id": "p1"}),
        _msg("executing", {"node": None, "prompt_id": "p1"}),  # ComfyUI: done
    ]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1",
                                 lambda v, m, n: seen.append((v, m, n)))
    assert seen == [(3, 8, "3"), (8, 8, "3")]


def test_other_prompt_progress_is_ignored(tmp_path):
    seen = []
    frames = [
        _msg("progress", {"value": 1, "max": 4, "node": "9", "prompt_id": "other"}),
        _msg("executing", {"node": None, "prompt_id": "p1"}),
    ]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1", lambda v, m, n: seen.append((v, m, n)))
    assert seen == []


def test_binary_preview_frames_are_skipped(tmp_path):
    seen = []
    frames = [b"\x00\x01binary-preview",
              _msg("progress", {"value": 2, "max": 2, "node": "1", "prompt_id": "p1"}),
              _msg("executing", {"node": None, "prompt_id": "p1"})]
    _backend(tmp_path)._watch_ws(FakeWS(frames), "p1", lambda v, m, n: seen.append((v, m, n)))
    assert seen == [(2, 2, "1")]

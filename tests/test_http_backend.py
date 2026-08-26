import json
import urllib.error

import comfyui_bridge.adapter.comfy_http as H
from comfyui_bridge.adapter.catalog import load_catalog
from comfyui_bridge.adapter.comfy_http import ComfyUIHttpBackend
from comfyui_bridge.config import Settings
from comfyui_bridge.core.intention import RenderIntent
from comfyui_bridge.core.plan import ExecutionPlan

PNG = b"\x89PNG\r\n\x1a\nFAKEDATA"


class _Resp:
    def __init__(self, data): self._d = data
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _plan():
    params = {"prompt": "x", "negative_prompt": "", "width": 768, "height": 768,
              "steps": 20, "cfg": 7.0, "seed": 0, "latent_batch": 1, "filename_prefix": "t"}
    return ExecutionPlan(RenderIntent(prompt="x"), params, kind="image", workflow="sd15-txt2img")


def _backend(tmp_path):
    # A closed port on purpose: a unit test must never reach a real service
    # (a half-alive server on the default port made the suite hang).
    s = Settings(comfy_backend="http", comfy_output_dir=tmp_path, comfyui_poll_interval_s=0.0,
                 comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                 neutral_media=False)
    return ComfyUIHttpBackend(s, load_catalog(s.catalog_file))


def test_http_submit_downloads_media(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            return _Resp(json.dumps({"p1": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
            }}).encode())
        if "/view" in url:
            return _Resp(PNG)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    res = _backend(tmp_path).submit(_plan())
    assert len(res.artifacts) == 1
    a = res.artifacts[0]
    assert a.kind == "image" and a.bytes == len(PNG)
    assert a.url == "/artifacts/out.png"
    assert (tmp_path / "out.png").read_bytes() == PNG


def test_http_reports_workflow_error(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            return _Resp(json.dumps({"p1": {"status": {
                "status_str": "error",
                "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]],
            }}}).encode())
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    from comfyui_bridge.core.errors import BackendExecutionError
    try:
        _backend(tmp_path).submit(_plan())
    except BackendExecutionError as e:
        assert "out of memory" in e.detail.lower()
        # the raw text travels so the domain can classify the real cause
        from comfyui_bridge.core.problems import OOM, classify
        assert classify(e.detail) == OOM
    else:
        raise AssertionError("expected BackendExecutionError")


def test_http_probe_unreachable(tmp_path, monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(H.urllib.request, "urlopen", boom)
    p = _backend(tmp_path).probe()
    assert p["available"] is False and "unreachable" in p["reason"]


def test_saturated_engine_is_not_declared_dead(tmp_path, monkeypatch):
    """A ComfyUI busy enough to stop answering HTTP must NOT be reported as a
    failure: it finishes and delivers. Reporting 'failed' there fed Hermes a
    false problem on a run ComfyUI had actually completed."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            calls["n"] += 1
            if calls["n"] <= 40:                      # long silence while computing
                raise urllib.error.URLError("timed out")
            return _Resp(json.dumps({"p1": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}},
            }}).encode())
        if "/view" in url:
            return _Resp(PNG)
        if "/ws" in url:
            raise urllib.error.URLError("no ws")
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    res = _backend(tmp_path).submit(_plan())
    assert len(res.artifacts) == 1                    # delivered, not failed


def test_execution_time_is_reread_when_the_stamp_lands_late(tmp_path, monkeypatch):
    """Outputs can appear just before ComfyUI stamps execution_success. Reading
    only once lost the engine's measure and left the run un-timed."""
    calls = {"hist": 0}
    done = {"status": {"status_str": "success", "messages": [
                ["execution_start", {"timestamp": 1000}]]},
            "outputs": {"9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}
    complete = json.loads(json.dumps(done))
    complete["status"]["messages"].append(["execution_success", {"timestamp": 43000}])

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/prompt"):
            return _Resp(json.dumps({"prompt_id": "p1"}).encode())
        if "/history/" in url:
            calls["hist"] += 1
            return _Resp(json.dumps({"p1": done if calls["hist"] == 1 else complete}).encode())
        if "/view" in url:
            return _Resp(PNG)
        raise AssertionError(url)

    monkeypatch.setattr(H.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    res = _backend(tmp_path).submit(_plan())
    assert res.execution_s == 42.0        # 43000 - 1000 ms, from the second read


def test_a_run_left_in_flight_is_collected_after_a_restart(tmp_path):
    """Measured: the engine finished a render while the service restarted, and
    the media was never fetched — the run "delivered nothing". What is written
    down before the restart lets ComfyUI's own history close the loop."""
    from comfyui_bridge.adapter.inflight import InflightLog, recover
    from comfyui_bridge.core.plan import Artifact

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p-1", workflow="wf", config="512x512", work=2.0,
            params={"width": 512, "height": 512}, at="2026-08-26T00:00:00+00:00")
    log.add("p-2", workflow="wf", config="512x512", work=2.0, params={}, at="2026-08-26T00:00:00+00:00")

    produced = Artifact(kind="video", path=str(tmp_path / "v.mp4"), url="/artifacts/v.mp4", bytes=10)

    class _Backend:
        def collect(self, prompt_id):
            return ([produced], 42.0) if prompt_id == "p-1" else None   # p-2 still running

    class _Registry:
        def __init__(self): self.rows = []
        def record(self, host, workflow, params, status, problem=None, detail=None,
                   duration_s=None, work=None):
            self.rows.append((workflow, status, duration_s, work))

    registry = _Registry()
    out = recover(_Backend(), log, registry, host="h")
    assert [o["state"] for o in out] == ["recovered"]
    assert registry.rows == [("wf", "succeeded", 42.0, 2.0)]
    # The collected one is forgotten; the one still running stays written down.
    assert list(log.entries()) == ["p-2"]

"""Engine reconciliation: declared, never duplicated."""

import json

import pytest

from comfyui_bridge.adapter import engines as E
from comfyui_bridge.config import Settings


def _file(tmp_path, data):
    p = tmp_path / "engines.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_shipped_file_declares_desktop_and_local():
    default, profiles = E.load_engines(Settings().engines_file)
    assert default in profiles
    assert profiles["desktop"].manage is False          # attach only
    assert profiles["local"].manage is True             # bridge starts it
    assert "--lowvram" in profiles["local"].command     # the stability flag


def test_attaches_instead_of_starting_a_second_instance(tmp_path, monkeypatch):
    """The rule that matters: a live server is joined, never duplicated."""
    started = []
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: True)
    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: started.append(a))
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    state = E.ensure_engine(p)
    assert state["state"] == "attached" and state["started"] is False
    assert started == []                                # nothing was launched


def test_attach_profile_never_starts_anything(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: started.append(a))
    p = E.EngineProfile("desktop", "http://127.0.0.1:8189", manage=False)
    state = E.ensure_engine(p)
    assert state["state"] == "absent" and started == []


def test_managed_profile_starts_when_nothing_is_there(tmp_path, monkeypatch):
    calls = {"n": 0}

    def alive(url, timeout=2.0):
        calls["n"] += 1
        return calls["n"] > 2          # dead at first, up after launch
    monkeypatch.setattr(E, "is_alive", alive)
    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.ensure_engine(p)["state"] == "started"


def test_unknown_or_empty_file_is_refused(tmp_path):
    with pytest.raises(E.EngineError):
        E.load_engines(_file(tmp_path, {"engines": {}}))


def test_concurrent_starts_do_not_launch_two_engines(tmp_path, monkeypatch):
    """Two bridge processes starting at once both saw 'nothing there' and each
    launched a ComfyUI — observed as two servers fighting for one port."""
    import os
    launches = []
    class _P:
        pid = 4321

    def _popen(*a, **k):
        launches.append(a)
        return _P()
    monkeypatch.setattr(E.subprocess, "Popen", _popen)
    monkeypatch.setattr(E.time, "sleep", lambda s: None)

    seq = iter([False, False, True])          # dead, dead, then up
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: next(seq, True))
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    first = E.ensure_engine(p, lock_dir=tmp_path)
    assert first["state"] == "started" and len(launches) == 1

    # A second process arrives while the first one's lock is still held.
    lock = tmp_path / "engine-local.lock"
    assert lock.exists() and lock.read_text().startswith(str(os.getpid()))
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    seq2 = iter([False, True])
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: next(seq2, True))
    second = E.ensure_engine(p, lock_dir=tmp_path)
    assert second["started"] is False          # joined, did not launch
    assert len(launches) == 1                  # still exactly one engine


def test_managed_engine_is_launched_detached(monkeypatch, tmp_path):
    """A bridge restart must not take the engine down with it: as a plain child
    it died on every restart and took minutes to come back."""
    seen = {}

    class _P:
        pid = 4321

    def fake_popen(cmd, **kw):
        seen.update(kw)
        return _P()
    monkeypatch.setattr(E.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    seq = iter([False, True])
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: next(seq, True))
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    E.ensure_engine(p, lock_dir=tmp_path)
    detached = seen.get("creationflags", 0) or seen.get("start_new_session")
    assert detached, "the engine must be detached from the bridge process"


def test_stop_refuses_to_touch_an_attached_engine(tmp_path):
    """A Desktop server belongs to the user: the bridge never kills it."""
    p = E.EngineProfile("desktop", "http://127.0.0.1:8189", manage=False)
    with pytest.raises(E.EngineError):
        E.stop_engine(p, tmp_path)


def test_stop_uses_the_pid_we_recorded(tmp_path, monkeypatch):
    killed = []
    (tmp_path / "engine-local.pid").write_text("777", encoding="utf-8")
    monkeypatch.setattr(E.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.stop_engine(p, tmp_path)["stopped"] is True
    assert killed == [777]

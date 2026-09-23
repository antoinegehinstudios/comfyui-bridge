"""Engine reconciliation: declared, never duplicated."""

import json
import os
import types

import pytest

from comfyui_bridge.adapter import engines as E
from comfyui_bridge.config import Settings


def _file(tmp_path, data):
    p = tmp_path / "engines.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_the_shipped_file_only_attaches_and_names_no_machine():
    """Le fichier livré dit COMMENT se connecter, jamais OÙ ComfyUI est installé :
    un profil qui LANCE a besoin d'un chemin, et un chemin décrit une machine."""
    default, profiles = E.load_engines(Settings().engines_file)
    assert default in profiles
    assert all(p.manage is False for p in profiles.values())
    assert all(not p.command for p in profiles.values())


def test_the_example_overlay_shows_how_to_declare_a_launching_engine():
    """La connaissance acquise ici — --lowvram évite les fautes CUDA sur 12 Go —
    ne doit pas se perdre en sortant les chemins du paquet."""
    import json
    from pathlib import Path
    exemple = json.loads(
        (Path(Settings().engines_file).parent / "engines.local.exemple.json")
        .read_text(encoding="utf-8"))
    local = exemple["engines"]["local"]
    assert local["manage"] is True
    assert "--lowvram" in local["command"]


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


def test_an_old_startup_lock_does_not_block_a_restart(tmp_path, monkeypatch):
    """Observed 2026-09-15: the lock written at the previous start stayed; once
    the engine was killed, every restart waited 240 s for 'another process' —
    the bridge itself, alive. A lock older than the startup window is a
    leftover, whoever wrote it: the restart goes ahead and rewrites it."""
    import os
    from comfyui_bridge.adapter import engines as eng
    profile = eng.EngineProfile(name="local", base_url="http://127.0.0.1:1", manage=True,
                                command=["python", "-c", "pass"], cwd=str(tmp_path))
    alive = {"n": 0}

    def fake_alive(url):
        alive["n"] += 1
        return alive["n"] > 1  # dead at first probe, alive right after the launch

    monkeypatch.setattr(eng, "is_alive", fake_alive)
    monkeypatch.setattr(eng.time, "sleep", lambda s: None)
    lock = eng._lock_path(profile, tmp_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{os.getpid()} {profile.base_url}", encoding="utf-8")  # a leftover, its writer alive
    vieux = eng.time.time() - 3600
    os.utime(lock, (vieux, vieux))  # written an hour ago: no startup lasts that long
    state = eng.ensure_engine(profile, startup_timeout_s=5.0, lock_dir=tmp_path)
    assert state["started"] is True
    assert lock.exists() and lock.stat().st_mtime > vieux + 1000  # rewritten by this start


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
    # Ce que l’arrêt frappe est capturé des deux côtés : sous Windows il passe
    # désormais par taskkill, et un vrai taskkill dans un test tuerait un PID
    # de cette machine.
    vus = _arrets_observes(monkeypatch)
    (tmp_path / "engine-local.pid").write_text("777", encoding="utf-8")
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.stop_engine(p, tmp_path)["stopped"] is True
    vises = vus["racine"] + [int(c[-1]) for c in vus["arbre"]]
    assert vises == [777]


def test_the_liveness_probe_never_kills_what_it_probes():
    """La sonde de vivacité REGARDE, elle ne touche pas. Sur Windows,
    os.kill(pid, 0) appelle TerminateProcess : la sonde tuait le processus
    sondé — et un PID recyclé faisait tomber un processus étranger."""
    import subprocess
    import sys
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(3):                     # le bug tuait dès le premier appel
            assert E._pid_exists(child.pid) is True
            assert child.poll() is None, "la sonde a tué le processus sondé"
    finally:
        child.kill()
        child.wait(timeout=10)
    assert E._pid_exists(child.pid) is False   # une fois mort, elle le dit


def test_the_lock_probe_reads_the_pid_without_touching_it(tmp_path):
    """Le verrou garde le PID d'un AUTRE processus : le lire ne doit rien tuer."""
    import subprocess
    import sys
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lock = tmp_path / "engine-local.lock"
    lock.write_text(f"{child.pid} http://127.0.0.1:8188", encoding="utf-8")
    try:
        assert E._starter_alive(lock) is True
        assert child.poll() is None, "lire le verrou a tué le processus qu'il nomme"
    finally:
        child.kill()
        child.wait(timeout=10)


def test_an_unreadable_or_dead_lock_is_not_a_running_starter(tmp_path):
    lock = tmp_path / "engine-local.lock"
    assert E._starter_alive(lock) is False              # absent
    lock.write_text("pas un pid", encoding="utf-8")
    assert E._starter_alive(lock) is False              # illisible
    assert E._pid_exists(0) is False and E._pid_exists(-1) is False


# --- L'arrêt emporte-t-il l'ARBRE, ou seulement sa racine ? -----------------


class _TaskkillOk:
    """Ce que rend `subprocess.run` quand taskkill a fait son travail."""

    returncode = 0
    stdout = ""
    stderr = ""


def _arrets_observes(monkeypatch, systeme=None, issue=None):
    """Regarder QUI l'arrêt frappe, sans jamais toucher un processus réel.

    `systeme` remplace le module `os` VU PAR engines.py, et lui seul : poser
    `os.name` à la main dérègle pathlib, qui choisit dessus sa saveur de chemin
    — constaté ici, plus aucun tmp_path ne s'instanciait.
    """
    vus: dict[str, list] = {"arbre": [], "racine": []}
    faux_os = types.SimpleNamespace(name=systeme or os.name,
                                    kill=lambda pid, sig: vus["racine"].append(pid))
    monkeypatch.setattr(E, "os", faux_os)
    monkeypatch.setattr(E.subprocess, "run",
                        lambda cmd, **kw: vus["arbre"].append(list(cmd)) or (issue or _TaskkillOk()))
    return vus


def test_l_arret_emporte_l_arbre_pas_seulement_sa_racine(tmp_path, monkeypatch):
    """ComfyUI se ré-exécute dans un enfant. Arbre constaté sur la machine :
    la passerelle lance 25092 (working set nul — c'est le PID que nous gardons),
    qui lance 22928, et c'est 22928 qui ÉCOUTE le port. Tuer la seule racine
    laissait le fils vivant avec le port, et l'arrêt rendait « toujours vivant
    après l'arrêt demandé »."""
    vus = _arrets_observes(monkeypatch, systeme="nt")
    (tmp_path / "engine-local.pid").write_text("25092", encoding="utf-8")
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.stop_engine(p, tmp_path)["stopped"] is True
    assert vus["arbre"] == [["taskkill", "/T", "/F", "/PID", "25092"]]   # /T : la descendance
    assert vus["racine"] == []                     # la racine seule ne rendait pas le port
    assert not (tmp_path / "engine-local.pid").exists()


def test_hors_windows_l_arret_reste_le_signal(tmp_path, monkeypatch):
    """Ailleurs, le groupe de processus fait ce travail : rien à changer."""
    vus = _arrets_observes(monkeypatch, systeme="posix")
    (tmp_path / "engine-local.pid").write_text("25092", encoding="utf-8")
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: False)
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.stop_engine(p, tmp_path)["stopped"] is True
    assert vus["racine"] == [25092] and vus["arbre"] == []


def test_un_arret_qui_ne_part_pas_se_declare(tmp_path, monkeypatch):
    """Un arrêt manqué ne se tait pas : il dit ce que le système a répondu."""
    class _Rate:
        returncode = 128
        stdout = ""
        stderr = "Le processus 25092 est introuvable."

    _arrets_observes(monkeypatch, systeme="nt", issue=_Rate())
    (tmp_path / "engine-local.pid").write_text("25092", encoding="utf-8")
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    issue = E.stop_engine(p, tmp_path)
    assert issue["stopped"] is False and "introuvable" in issue["reason"]


# --- Le journal du moteur porte-t-il une date ? -----------------------------


def test_le_journal_du_moteur_s_ouvre_sur_une_ligne_datee(tmp_path, monkeypatch):
    """ComfyUI n'horodate rien : dater sa mort a demandé de la reconstruire
    depuis les durées de prompts (« Prompt executed in 595.24 seconds »)
    recoupées avec les fiches de jobs. Le démarrage, lui, est daté par nous."""
    from datetime import datetime

    class _P:
        pid = 25092

    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: _P())
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    seq = iter([False, True])                      # mort au premier regard, debout après
    monkeypatch.setattr(E, "is_alive", lambda url, timeout=2.0: next(seq, True))
    p = E.EngineProfile("local", "http://127.0.0.1:8188", manage=True, command=["x"])
    assert E.ensure_engine(p, lock_dir=tmp_path)["started"] is True

    premiere = (tmp_path / "moteur-local.log").read_text(encoding="utf-8").splitlines()[0]
    marque, quand, suite = premiere.split(" ", 2)
    assert marque == "[passerelle]"                # jamais confondue avec la sortie du moteur
    date = datetime.fromisoformat(quand)           # une vraie date ISO 8601…
    assert date.utcoffset().total_seconds() == 0   # …en UTC, pas en heure d'ici
    assert "'local'" in suite and "25092" in suite  # le profil, et le PID lancé

"""Engine reconciliation — WHERE ComfyUI runs, declared instead of hardcoded.

Same idea as the workflow catalog, applied to the engine itself: profiles are
declared in a file, the pipeline picks one by name. Two shapes:

* ``attach``  — a server someone else runs (ComfyUI Desktop). We only connect.
* ``managed`` — the bridge starts a headless ComfyUI itself, with its own flags.

The rule that matters: **never start a second instance**. If the profile's URL
already answers, we attach to what is there. Running two ComfyUI on two ports
once made the UI and the engine disagree about which queue a job was in.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import BridgeError


class EngineError(BridgeError):
    problem_type = "https://cortex/problems/engine"
    title = "Engine unavailable"
    status = 503


@dataclass(frozen=True)
class EngineProfile:
    name: str
    base_url: str
    manage: bool = False
    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    description: str = ""


def load_engines(path: str | Path,
                 data_dir: str | Path | None = None) -> tuple[str, dict[str, EngineProfile]]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(f"cannot read engines file {path}: {exc}") from exc

    # Le chemin d'une installation ComfyUI décrit UNE machine : il se déclare
    # à côté, pas dans le paquet, qui ne doit publier l'arborescence de personne.
    from .local_overlay import merge, read_overlay
    try:
        data = merge(data, read_overlay(Path(data_dir) if data_dir else None, path), "engines")
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(f"cannot read local engines overlay: {exc}") from exc
    profiles: dict[str, EngineProfile] = {}
    for name, entry in (data.get("engines") or {}).items():
        if not isinstance(entry, dict) or "base_url" not in entry:
            raise EngineError(f"engine {name!r}: needs 'base_url'")
        profiles[name] = EngineProfile(
            name=name,
            base_url=str(entry["base_url"]).rstrip("/"),
            manage=bool(entry.get("manage", False)),
            command=[str(c) for c in (entry.get("command") or [])],
            cwd=entry.get("cwd"),
            description=str(entry.get("description", "")),
        )
    if not profiles:
        raise EngineError(f"{path}: no engine declared")
    default = data.get("default") or next(iter(profiles))
    if default not in profiles:
        raise EngineError(f"{path}: default {default!r} not declared")
    return default, profiles


def is_alive(base_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/system_stats", timeout=timeout):
            return True
    except Exception:
        return False


def _lock_path(profile: EngineProfile, lock_dir: Path) -> Path:
    return Path(lock_dir) / f"engine-{profile.name}.lock"


def _pid_exists(pid: int) -> bool:
    """Ce PID est-il vivant ? Sonde qui REGARDE, sans jamais toucher.

    Sur Windows, os.kill(pid, 0) ne teste rien : CPython l'implémente par
    TerminateProcess(handle, 0) pour tout signal autre que CTRL_C/CTRL_BREAK.
    La « sonde » TUAIT donc le processus sondé — et comme le verrou garde le PID
    d'un ancien processus du pont, un PID recyclé entre-temps faisait tomber un
    innocent au simple démarrage du pont.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_TIMEOUT = 0x102                       # non signalé == tourne encore
        ERROR_ACCESS_DENIED = 5                    # existe, mais pas à nous
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = k32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            # Un processus terminé dont on tient encore un handle s'ouvre
            # toujours : c'est l'état signalé, pas l'ouverture, qui tranche.
            return k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            k32.CloseHandle(handle)
    try:  # POSIX : signal 0 == « ce processus existe-t-il ? », sans le toucher
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                # existe, mais pas à nous
    except OSError:
        return False
    return True


def _starter_alive(lock: Path, max_age_s: float = 300.0) -> bool:
    """Is another bridge process already starting this engine?

    A lock older than the startup window is a leftover, whoever wrote it
    (observed 2026-09-15: the lock written at the previous start outlived the
    engine, and every restart request waited 240 s for "another process" —
    the bridge itself, still alive). No startup lasts hours."""
    try:
        if time.time() - lock.stat().st_mtime > max_age_s:
            return False
        pid = int(lock.read_text(encoding="utf-8").split()[0])
    except Exception:
        return False
    return _pid_exists(pid)


def _pid_path(profile: EngineProfile, lock_dir: Path) -> Path:
    return Path(lock_dir) / f"engine-{profile.name}.pid"


def _journal_path(profile: EngineProfile, lock_dir: Path) -> Path:
    """Où la sortie du moteur s'écrit (voir le lancement) : « moteur-<nom>.log »."""
    return Path(lock_dir) / f"moteur-{profile.name}.log"


def _ligne_de_journal(message: str) -> bytes:
    """Une ligne À NOUS dans le journal du moteur, datée en UTC.

    ComfyUI n'horodate rien de ce qu'il écrit : dater sa mort du 2026-09-22 a
    demandé de la reconstruire depuis les durées de prompts (« Prompt executed
    in 595.24 seconds ») recoupées avec les fiches de jobs — une heure
    d'enquête pour ce que deux lignes auraient dit. Le préfixe tient notre
    ligne à part de la sienne, qui n'en porte aucun.
    """
    quand = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"[passerelle] {quand} {message}\n".encode("utf-8")


def _arreter_l_arbre(pid: int) -> None:
    """Arrêter le processus lancé ET sa descendance.

    ComfyUI se ré-exécute dans un enfant : l'arbre constaté sur la machine est
    « passerelle → lanceur (le PID que nous gardons, working set nul) → le vrai
    moteur, celui qui tient le port ». Tuer la seule racine laissait le fils
    vivant avec le port, et l'arrêt rendait « toujours vivant après l'arrêt
    demandé » — l'arrêt échouait en disant qu'il avait réussi à frapper.
    """
    if os.name == "nt":
        # /T prend la descendance, /F ne négocie pas : sous Windows un SIGTERM
        # se résout déjà en TerminateProcess, il n'y a rien de plus doux à
        # perdre. `taskkill` est livré avec le système : aucune dépendance.
        # Sans fenêtre : la passerelle tourne sans console (start-bridge-silent),
        # et un arrêt de moteur ne doit pas en faire clignoter une.
        issue = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                               capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if issue.returncode != 0:
            raise OSError((issue.stderr or issue.stdout or "").strip()
                          or f"taskkill a rendu {issue.returncode}")
        return
    import signal
    os.kill(pid, signal.SIGTERM)


def stop_engine(profile: EngineProfile, lock_dir: Path, timeout_s: float = 40.0) -> dict[str, Any]:
    """Stop the engine WE started. An 'attach' profile is never touched: that
    server belongs to someone else (ComfyUI Desktop)."""
    if not profile.manage:
        raise EngineError(
            f"engine {profile.name!r} is an 'attach' profile: it is not ours to stop")
    pid_file = _pid_path(profile, lock_dir)
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return {"stopped": False, "reason": "aucun PID connu pour ce moteur"}
    try:
        _arreter_l_arbre(pid)
    except Exception as e:
        return {"stopped": False, "reason": f"arrêt impossible ({e})"}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_alive(profile.base_url, 1.5):
            pid_file.unlink(missing_ok=True)
            return {"stopped": True, "pid": pid}
        time.sleep(1.5)
    return {"stopped": False, "reason": "toujours vivant après l'arrêt demandé", "pid": pid}


def ensure_engine(profile: EngineProfile, startup_timeout_s: float = 180.0,
                  lock_dir: Path | None = None) -> dict[str, Any]:
    """Make the engine reachable. Attach if it already answers; start it only if
    the profile manages it AND nothing is there. Returns what actually happened.
    """
    if is_alive(profile.base_url):
        return {"engine": profile.name, "state": "attached", "started": False,
                "base_url": profile.base_url}
    if not profile.manage:
        return {"engine": profile.name, "state": "absent", "started": False,
                "base_url": profile.base_url,
                "reason": "profil 'attach' : le serveur doit être lancé (ComfyUI Desktop ?)"}
    if not profile.command:
        raise EngineError(f"engine {profile.name!r}: 'manage' without a 'command'")

    # A slow-starting engine looks dead. Without this guard two bridge processes
    # both concluded "nothing there" and launched one each — observed: two
    # ComfyUI fighting for the same port.
    lock = _lock_path(profile, lock_dir) if lock_dir else None
    if lock is not None and lock.exists() and _starter_alive(lock, max_age_s=max(startup_timeout_s, 60.0) + 60.0):
        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            time.sleep(2.0)
            if is_alive(profile.base_url):
                return {"engine": profile.name, "state": "attached", "started": False,
                        "base_url": profile.base_url, "note": "démarrage mené par un autre processus"}
        raise EngineError(f"engine {profile.name!r}: another process is starting it, still silent")

    if lock is not None:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(f"{os.getpid()} {profile.base_url}", encoding="utf-8")
    # DETACHED: the engine is a long-lived service, not a child of this bridge.
    # As a child it died with every bridge restart, and a fresh engine takes
    # minutes to load — observed as "ComfyUI won't come back".
    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):        # Windows
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    # La sortie du moteur est GARDÉE, en ajout, dans le dossier de données :
    # jetée (DEVNULL), la mort du moteur du 2026-09-18 n'a laissé aucune ligne
    # — ni l'allocation refusée, ni le dernier nœud, ni les mesures de mémoire
    # que ses propres nœuds écrivent. Le fichier est refermé ici après le
    # lancement : l'enfant garde le sien.
    journal = open(_journal_path(profile, lock_dir), "ab") if lock_dir is not None else None
    try:
        proc = subprocess.Popen(
            profile.command, cwd=profile.cwd,
            stdout=journal if journal is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if journal is not None else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
            start_new_session=not hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"),  # POSIX
        )
        if journal is not None:
            # C'est nous qui ouvrons ce descripteur : nous pouvons le dater
            # avant de le confier au moteur, qui met des secondes à écrire sa
            # première ligne. Le démarrage est ainsi le seul repère sûr du
            # journal, et tout ce qui suit s'y rattache.
            journal.write(_ligne_de_journal(
                f"démarrage du moteur {profile.name!r}, PID lancé {proc.pid}"))
    finally:
        if journal is not None:
            journal.close()
    if lock_dir is not None:  # remember WHICH process we own, to stop it later
        _pid_path(profile, lock_dir).write_text(str(proc.pid), encoding="utf-8")
    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        time.sleep(2.0)
        if is_alive(profile.base_url):
            return {"engine": profile.name, "state": "started", "started": True,
                    "base_url": profile.base_url}
    raise EngineError(
        f"engine {profile.name!r} did not answer within {int(startup_timeout_s)}s",
        base_url=profile.base_url,
    )

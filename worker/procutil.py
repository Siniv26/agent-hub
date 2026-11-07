"""Subprocess helper. shell is never enabled. Used for gh, not for model CLIs."""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass

from worker.constants import CAPTURE_BYTES


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool


def runtime_env() -> dict[str, str]:
    """Environment for gh. Drops token-like variables so connector creds are unused."""
    allow = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "PUBLIC",
        "USERNAME",
    }
    env: dict[str, str] = {}
    for key in allow:
        if key in os.environ and not _secret_name(key):
            env[key] = os.environ[key]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_process(
    argv: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
    timeout: float,
    stdin: bytes | None = None,
    max_bytes: int = CAPTURE_BYTES,
) -> Completed:
    if not argv or any(not isinstance(part, str) for part in argv):
        raise ValueError("argv must be a list of strings")
    flags: dict[str, object] = {}
    if os.name == "nt":
        flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **flags,
    )
    timed_out = False
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        out, err = proc.communicate()
    return Completed(
        returncode=proc.returncode if proc.returncode is not None else 1,
        stdout=(out or b"")[:max_bytes],
        stderr=(err or b"")[:max_bytes],
        timed_out=timed_out,
    )


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _secret_name(name: str) -> bool:
    folded = name.casefold()
    markers = ("token", "secret", "password", "credential", "api_key", "apikey")
    return any(marker in folded for marker in markers)

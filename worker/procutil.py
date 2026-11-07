"""Subprocess helper. shell is never enabled."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass

from worker.constants import CAPTURE_BYTES


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    killed_by_policy: bool = False


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
    on_stdout_line=None,
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
    if on_stdout_line is None:
        return _communicate(proc, stdin=stdin, timeout=timeout, max_bytes=max_bytes)
    return _stream(
        proc,
        stdin=stdin,
        timeout=timeout,
        max_bytes=max_bytes,
        on_stdout_line=on_stdout_line,
    )


def _communicate(proc, *, stdin, timeout, max_bytes) -> Completed:
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


def _stream(proc, *, stdin, timeout, max_bytes, on_stdout_line) -> Completed:
    if stdin is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    out_chunks: list[bytes] = []
    err_box: list[bytes] = []
    policy = False

    def read_err() -> None:
        try:
            err_box.append(proc.stderr.read() if proc.stderr else b"")
        except OSError:
            err_box.append(b"")

    def read_out() -> None:
        nonlocal policy
        try:
            while True:
                line = proc.stdout.readline() if proc.stdout else b""
                if not line:
                    break
                out_chunks.append(line)
                try:
                    keep = on_stdout_line(line)
                except Exception:
                    keep = False
                if keep is False:
                    policy = True
                    kill_tree(proc)
                    break
                if sum(len(part) for part in out_chunks) > max_bytes:
                    policy = True
                    kill_tree(proc)
                    break
        except OSError:
            policy = True
            kill_tree(proc)

    err_thread = threading.Thread(target=read_err, daemon=True)
    out_thread = threading.Thread(target=read_out, daemon=True)
    err_thread.start()
    out_thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
    out_thread.join(timeout=5)
    err_thread.join(timeout=5)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass
    return Completed(
        returncode=proc.returncode if proc.returncode is not None else 1,
        stdout=b"".join(out_chunks)[:max_bytes],
        stderr=b"".join(err_box)[:max_bytes],
        timed_out=timed_out,
        killed_by_policy=policy,
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

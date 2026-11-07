"""Command line. Importing this module does not poll, arm, or install a task."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from worker import __version__
from worker.config import ConfigError, load_config, real_work_allowed
from worker.constants import (
    CODEX_EXE,
    CODEX_VERSION,
    DSH_EXE,
    DSH_VERSION,
    GH_VERSION_SEEN,
    HERMES_EXE,
    HERMES_HOME,
    HERMES_VERSION,
    REASONS,
)
from worker.engine import assert_worker_python, next_backoff, run_tick
from worker.github import GhClient, GhError
from worker.state import Lock, LockError, Store

_GH_HELP = (
    "GitHub CLI is required. Install official gh (2.103.3 is already the user-scope "
    "build on the worker machine) and finish `gh auth login` with the device code. "
    "The Cursor GitHub connector is not runtime auth. This worker never reads GH_TOKEN "
    "or credential files."
)


class Logger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lines: list[str] = []

    def __call__(self, message: str) -> None:
        line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " INFO " + message
        self.lines.append(line)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def find_config(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    env = os.environ.get("AGENT_HUB_WORKER_CONFIG")
    if env:
        return Path(env)
    cwd = Path.cwd() / "config.json"
    if cwd.exists():
        return cwd
    beside = Path(__file__).resolve().parent.parent / "config.json"
    if beside.exists():
        return beside
    return None


def install_task_argv(worker_root: Path) -> list[str]:
    text = str(worker_root)
    if any(char in text for char in " &'\"|<>^"):
        raise ValueError("worker root must not contain spaces or shell characters")
    script = worker_root / "scripts" / "install-logon-task.ps1"
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-WorkerRoot",
        text,
    ]


def doctor(cfg, store: Store, *, which=shutil.which, out=print) -> int:
    out(f"agent-hub-worker {__version__}")
    out(f"enabled={cfg.enabled} dry_run={cfg.dry_run} armed={store.armed()} stop={store.stop_requested()}")
    out("automatic task: smoke only (BUS_SMOKE_OK). No model process is started.")
    out("deferred: " + REASONS["hermes_deferred"])
    out("deferred: " + REASONS["codex_deferred"])
    out("deferred: " + REASONS["dsh_deferred"])
    out(f"recorded Hermes {HERMES_VERSION} not launched: {HERMES_EXE}")
    out(f"recorded HERMES_HOME not used: {HERMES_HOME}")
    out(f"recorded Codex {CODEX_VERSION} not launched: {CODEX_EXE}")
    out(f"recorded DSH {DSH_VERSION} not launched: {DSH_EXE}")
    found = which("gh") or which("gh.exe")
    if not found:
        out(_GH_HELP)
        return 2
    out(f"gh: {found}")
    out(
        f"Expected gh {GH_VERSION_SEEN} from the machine preflight. "
        "Finish device-code `gh auth login` in that user account. Do not paste tokens here."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="worker")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "once", "start", "stop", "status", "arm", "disarm", "clear-stop"):
        item = sub.add_parser(name)
        if name == "once":
            item.add_argument("--arm", action="store_true")
    sub.add_parser("install-task")
    sub.add_parser("uninstall-task")
    build = sub.add_parser("build-bundle")
    build.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "build-bundle":
        from worker.bundle import build_bundle

        destination = build_bundle(Path(args.out))
        print(destination)
        return 0
    if args.command == "install-task":
        return _install(Path(__file__).resolve().parent.parent)
    if args.command == "uninstall-task":
        return _uninstall(Path(__file__).resolve().parent.parent)
    path = find_config(args.config)
    if path is None or not path.exists():
        print("Copy config.example.json to config.json. No secrets.")
        return 2
    try:
        cfg = load_config(path)
    except (OSError, ConfigError, ValueError) as exc:
        print(f"config error: {exc}")
        return 2
    store = Store(cfg.state_dir)
    store.ensure()
    if args.command == "doctor":
        return doctor(cfg, store)
    if args.command == "status":
        return _status(cfg, store)
    if args.command == "arm":
        _refuse_special_python()
        store.arm()
        print("armed. enabled and dry_run still gate real work. No task was installed.")
        return 0
    if args.command == "disarm":
        store.disarm()
        print("disarmed")
        return 0
    if args.command == "stop":
        store.request_stop()
        print("stop flag set")
        return 0
    if args.command == "clear-stop":
        store.clear_stop()
        print("stop flag cleared")
        return 0
    if args.command == "once":
        return _once(cfg, store, arm=args.arm)
    if args.command == "start":
        return _start(cfg, store)
    print("unknown command")
    return 2


def _once(cfg, store: Store, *, arm: bool) -> int:
    real = arm and real_work_allowed(cfg, armed=store.armed())
    if arm and not real:
        print("once --arm refused. Need config enabled=true, dry_run=false, and arm.")
        return 2
    if real:
        _refuse_special_python()
    return _tick(cfg, store, dry_run=not real)


def _start(cfg, store: Store) -> int:
    if not real_work_allowed(cfg, armed=store.armed()):
        print("start refused. Need config enabled=true, dry_run=false, and arm.")
        return 2
    _refuse_special_python()
    if not _gh_present():
        print(_GH_HELP)
        return 2
    stop = threading.Event()

    def _handle(signum, frame) -> None:
        del signum, frame
        stop.set()
        store.request_stop()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)
    delay = cfg.poll_seconds
    log = Logger(cfg.log_dir / "worker.log")
    gh = GhClient(cfg.gh_executable)
    lock = Lock(store.lock_path)
    try:
        lock.acquire()
    except LockError:
        print("another worker holds the lock")
        return 3
    try:
        while not stop.is_set() and not store.stop_requested():
            try:
                run_tick(gh, store, log, dry_run=False)
                delay = cfg.poll_seconds
            except GhError:
                delay = next_backoff(delay)
                log(f"gh error; backoff {delay}s")
            stop.wait(delay)
    finally:
        lock.release()
    return 0


def _tick(cfg, store: Store, *, dry_run: bool) -> int:
    if not _gh_present():
        print(_GH_HELP)
        return 2
    for path in (cfg.state_dir, cfg.log_dir, cfg.work_dir):
        path.mkdir(parents=True, exist_ok=True)
    log = Logger(cfg.log_dir / "worker.log")
    gh = GhClient(cfg.gh_executable)
    lock = Lock(store.lock_path)
    try:
        lock.acquire()
    except LockError:
        print("another worker holds the lock")
        return 3
    try:
        run_tick(gh, store, log, dry_run=dry_run)
    except GhError:
        print("gh read failed")
        return 1
    finally:
        lock.release()
    return 0


def _status(cfg, store: Store) -> int:
    print(f"enabled={cfg.enabled} dry_run={cfg.dry_run} armed={store.armed()} stop={store.stop_requested()}")
    for claim in store.unfinished():
        print(f"unfinished #{claim.issue} {claim.phase} {claim.reason}")
    return 0


def _install(root: Path) -> int:
    if os.name != "nt":
        print("install-task is Windows-only. It was not run.")
        return 2
    _refuse_special_python()
    import subprocess

    try:
        argv = install_task_argv(root)
    except ValueError as exc:
        print(str(exc))
        return 2
    result = subprocess.run(argv, check=False, shell=False)
    return int(result.returncode)


def _uninstall(root: Path) -> int:
    if os.name != "nt":
        print("uninstall-task is Windows-only. It was not run.")
        return 2
    script = root / "scripts" / "uninstall-logon-task.ps1"
    import subprocess

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        check=False,
        shell=False,
    )
    return int(result.returncode)


def _gh_present() -> bool:
    return bool(shutil.which("gh") or shutil.which("gh.exe"))


def _refuse_special_python() -> None:
    if os.environ.get("AGENT_HUB_WORKER_TEST"):
        raise SystemExit("refusing while AGENT_HUB_WORKER_TEST is set")
    assert_worker_python(Path(sys.executable))

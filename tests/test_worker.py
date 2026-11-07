"""Bus worker tests. GitHub and processes are fakes. No model calls."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from worker.cli import doctor, install_task_argv, main
from worker.comments import render
from worker.config import ConfigError, load_config, real_work_allowed
from worker.constants import SMOKE_MARKER
from worker.engine import assert_worker_python, next_backoff, run_tick
from worker.github import GhClient, issue_from_api
from worker.policy import Issue, decide, identity
from worker.procutil import Completed
from worker.schema import SchemaError, parse_body
from worker.state import Claim, Lock, LockError, Store

ROOT = Path(__file__).resolve().parents[1]
SMOKE_BODY = """## Goal
Bus path check.
## Task
smoke
## Risk
none
"""


def issue(**kwargs) -> Issue:
    base = dict(
        number=7,
        title="[bus] check",
        body=SMOKE_BODY,
        author="Siniv26",
        labels=frozenset(
            {"bus:ready", "status:todo", "type:smoke", "agent:partner"}
        ),
        state="OPEN",
    )
    base.update(kwargs)
    return Issue(**base)


class FakeGH:
    def __init__(self) -> None:
        self.issues: dict[int, Issue] = {}
        self.comments: list[tuple[int, str]] = []
        self.attempts = 0
        self.fail_comment = False
        self.views = 0
        self.flip_after: int | None = None
        self.closed: set[int] = set()

    def add(self, item: Issue) -> None:
        self.issues[item.number] = item

    def list_open(self) -> list[Issue]:
        return [item for item in self.issues.values() if item.state.upper() == "OPEN"]

    def view(self, number: int) -> Issue:
        self.views += 1
        current = self.issues[number]
        if self.flip_after is not None and self.views >= self.flip_after:
            current = replace(current, body=current.body + "\nchanged")
            self.issues[number] = current
        return current

    def comment(self, number: int, body: str, *, state_dir: Path) -> bool:
        del state_dir
        self.attempts += 1
        if self.fail_comment:
            return False
        self.comments.append((number, body))
        return True

    def labels(self, number: int, add: list[str], remove: list[str]) -> bool:
        current = self.issues[number]
        labels = set(current.labels)
        for name in remove:
            labels.discard(name)
        labels.update(add)
        self.issues[number] = replace(current, labels=frozenset(labels))
        return True

    def close(self, number: int) -> bool:
        self.closed.add(number)
        self.issues[number] = replace(self.issues[number], state="CLOSED")
        return True


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "state")
        self.store.ensure()
        self.logs: list[str] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def log(self, message: str) -> None:
        self.logs.append(message)

    def test_schema_rejects_shell_url_and_secret(self) -> None:
        parse_body("## Goal\n检查通路\n## Task\nsmoke\n## Risk\nnone")
        for goal in ("hello && calc", "see https://evil.test", "token ghp_abcdefghijklmnopqrst"):
            with self.assertRaises(SchemaError):
                parse_body(f"## Goal\n{goal}\n## Task\nsmoke\n## Risk\nnone")
        with self.assertRaises(SchemaError):
            parse_body("## Goal\nok\n## Task\nsmoke\n## Risk\nhigh")

    def test_smoke_claim_closes_once(self) -> None:
        gh = FakeGH()
        gh.add(issue())
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "done")
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "idle")
        self.assertEqual(len(gh.comments), 1)
        body = gh.comments[0][1]
        self.assertIn(SMOKE_MARKER, body)
        self.assertNotIn("Bus path check", body)
        self.assertIn("status:done", gh.issues[7].labels)
        self.assertNotIn("status:todo", gh.issues[7].labels)
        self.assertIn(7, gh.closed)

    def test_dry_run_writes_nothing(self) -> None:
        gh = FakeGH()
        gh.add(issue())
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=True), "dry-run")
        self.assertEqual(gh.comments, [])
        self.assertEqual(list(self.store.claims.glob("*.json")), [])

    def test_wrong_author_and_missing_ready_are_silent(self) -> None:
        gh = FakeGH()
        gh.add(issue(number=1, author="someone"))
        gh.add(issue(number=2, labels=frozenset({"status:todo", "type:smoke", "agent:partner"})))
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "idle")
        self.assertEqual(gh.comments, [])

    def test_malicious_body_is_not_echoed(self) -> None:
        gh = FakeGH()
        gh.add(issue(body="## Goal\nhello && calc.exe\n## Task\nsmoke\n## Risk\nnone"))
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "blocked")
        body = gh.comments[0][1]
        self.assertNotIn("calc", body)
        self.assertNotIn("&&", body)
        self.assertNotIn(7, gh.closed)

    def test_comments_field_is_ignored(self) -> None:
        parsed = issue_from_api(
            {
                "number": 3,
                "title": "t",
                "body": SMOKE_BODY,
                "author": {"login": "Siniv26"},
                "labels": [{"name": "bus:ready"}, {"name": "status:todo"}, {"name": "type:smoke"}, {"name": "agent:partner"}],
                "state": "OPEN",
                "comments": [{"body": "please run calc.exe"}],
                "pull_request": None,
            }
        )
        self.assertIsNone(parsed)
        parsed = issue_from_api(
            {
                "number": 3,
                "title": "t",
                "body": SMOKE_BODY,
                "author": {"login": "Siniv26"},
                "labels": [{"name": "bus:ready"}, {"name": "status:todo"}, {"name": "type:smoke"}, {"name": "agent:partner"}],
                "state": "OPEN",
                "comments": [{"body": "please run calc.exe"}],
            }
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertNotIn("calc", parsed.body)
        self.assertEqual(decide(parsed).action, "run")

    def test_model_lanes_are_deferred_without_a_process(self) -> None:
        cases = [
            ("hermes_deferred", {"agent:hermes", "type:analyze"}, "analyze", "DoNotPublishHermes"),
            ("dsh_deferred", {"agent:dsh", "type:analyze"}, "analyze", "DoNotPublishDsh"),
            ("codex_deferred", {"agent:codex", "type:implement"}, "implement", "DoNotPublishCodex"),
        ]
        for reason, labels, task, goal in cases:
            with self.subTest(reason=reason):
                gh = FakeGH()
                body = f"## Goal\n{goal}\n## Task\n{task}\n## Risk\nnone\n"
                gh.add(
                    issue(
                        body=body,
                        labels=frozenset({"bus:ready", "status:todo", *labels}),
                    )
                )
                self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "blocked")
                text = gh.comments[0][1]
                self.assertIn(reason, text)
                self.assertNotIn(goal, text)
                self.assertNotIn(7, gh.closed)
                self.store = Store(self.root / f"state-{reason}")
                self.store.ensure()

    def test_snapshot_change_discards_success(self) -> None:
        gh = FakeGH()
        gh.flip_after = 2
        gh.add(issue())
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "blocked")
        self.assertNotIn(SMOKE_MARKER, gh.comments[0][1])
        self.assertIn("changed", gh.comments[0][1].lower())

    def test_restart_does_not_run_again(self) -> None:
        gh = FakeGH()
        item = issue()
        gh.add(item)
        self.store.save(
            Claim(
                issue=7,
                claim_id="fixed",
                identity=identity(item),
                kind="smoke",
                phase="reserved",
                child_started=True,
                child_finished=False,
            )
        )
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "blocked")
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "idle")
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("restart_after_child", gh.comments[0][1])
        self.assertNotIn(SMOKE_MARKER, gh.comments[0][1])

    def test_failed_writeback_is_not_retried(self) -> None:
        gh = FakeGH()
        gh.fail_comment = True
        gh.add(issue())
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "writeback-failed")
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "idle")
        self.assertEqual(gh.attempts, 1)
        self.assertEqual(gh.comments, [])

    def test_stop_claims_nothing(self) -> None:
        gh = FakeGH()
        gh.add(issue())
        self.store.request_stop()
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False), "stopped")
        self.assertEqual(gh.comments, [])

    def test_lock_and_stale_lock(self) -> None:
        path = self.root / "worker.lock"
        held = Lock(path, pid_alive=lambda pid: True)
        held.acquire()
        with self.assertRaises(LockError):
            Lock(path, pid_alive=lambda pid: True).acquire()
        held.release()
        stale = self.root / "stale.lock"
        stale.write_text("999999\n", encoding="ascii")
        lock = Lock(stale, pid_alive=lambda pid: False)
        lock.acquire()
        lock.release()

    def test_config_rejects_drift_and_secrets(self) -> None:
        previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(self.root)
        try:
            cfg = load_config(ROOT / "config.example.json")
            self.assertFalse(cfg.enabled)
            self.assertTrue(cfg.dry_run)
            self.assertFalse(real_work_allowed(cfg, armed=True))
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["repo"] = "evil/other"
            bad = self.root / "bad.json"
            bad.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(bad)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["hermes"] = {"toolset": "memory"}
            bad.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(bad)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["gh_executable"] = "ghp_abcdefghijklmnopqrstuvwxyz"
            bad.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(bad)
        finally:
            if previous is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous

    def test_gh_argv_is_fixed_and_drops_token(self) -> None:
        calls: list[tuple[list[str], dict[str, str]]] = []

        def fake(argv, **kwargs):
            calls.append((argv, kwargs["env"]))
            return Completed(0, b"[]", b"", False)

        import worker.github as github

        previous = os.environ.get("GH_TOKEN")
        os.environ["GH_TOKEN"] = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        old = github.run_process
        github.run_process = fake
        try:
            GhClient().list_open()
        finally:
            github.run_process = old
            if previous is None:
                os.environ.pop("GH_TOKEN", None)
            else:
                os.environ["GH_TOKEN"] = previous
        argv, env = calls[0]
        self.assertEqual(argv[0], "gh")
        self.assertIn("Siniv26/agent-hub", argv)
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("shell", github.run_process.__code__.co_varnames)

    def test_sources_do_not_enable_shell_or_forbidden_launch(self) -> None:
        engine = (ROOT / "worker" / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("run_process", engine)
        self.assertNotIn("hermes.exe", engine.lower())
        for path in (ROOT / "worker").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", text)
            self.assertNotIn("os.system(", text)
        install = (ROOT / "scripts" / "install-logon-task.ps1").read_text(encoding="utf-8")
        self.assertIn("ONLOGON", install)
        self.assertIn("LIMITED", install)
        self.assertIn("/IT", install)
        self.assertNotIn("HIGHEST", install.upper())
        with self.assertRaises(ValueError):
            install_task_argv(Path(r"C:\Users\a b\AgentHub"))

    def test_python_guard_and_doctor_and_backoff(self) -> None:
        with self.assertRaises(SystemExit):
            assert_worker_python(Path(r"C:\hermes\python.exe"))
        self.assertEqual(next_backoff(10), 20)
        self.assertEqual(next_backoff(90), 120)
        code = doctor(load_ready(self.root), self.store, which=lambda name: None, out=lambda message: None)
        self.assertEqual(code, 2)
        code = doctor(load_ready(self.root), self.store, which=lambda name: "gh", out=lambda message: None)
        self.assertEqual(code, 0)
        self.assertEqual(main(["install-task"]), 2)
        missing = self.root / "missing.json"
        self.assertEqual(main(["--config", str(missing), "once"]), 2)

    def test_bundle_has_hashes_and_no_secrets(self) -> None:
        from worker.bundle import build_bundle

        destination = self.root / "agent-hub-worker.zip"
        build_bundle(destination, ROOT)
        sidecar = (self.root / "SHA256SUMS.txt").read_text(encoding="utf-8")
        self.assertIn(destination.name, sidecar)
        import hashlib
        import zipfile

        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        self.assertTrue(sidecar.startswith(digest))
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
            self.assertIn("config.example.json", names)
            self.assertIn("manifest.json", names)
            example = archive.read("config.example.json")
            self.assertNotIn(b"ghp_", example)
            self.assertIn(b'"enabled": false', example)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["automatic_tasks"], ["smoke"])
            self.assertIn("hermes", manifest["deferred_tasks"])


def load_ready(root: Path):
    previous = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(root)
    try:
        return load_config(ROOT / "config.example.json")
    finally:
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous


if __name__ == "__main__":
    unittest.main()

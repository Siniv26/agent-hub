"""Bus worker tests. GitHub and processes are fakes. No model calls."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from worker.cli import doctor, install_task_argv, main
from worker.comments import render
from worker.config import ConfigError, LaneConfig, load_config, real_work_allowed
from worker.constants import DSH_EXE, SMOKE_MARKER
from worker.engine import assert_worker_python, next_backoff, run_tick
from worker.github import GhClient, issue_from_api
from worker.lanes import (
    AllowlistError,
    build_dsh_argv,
    build_hermes_argv,
    interpret_analyze,
    interpret_implement,
    line_keeps_running,
    windows_cmdline,
    windows_launch_command,
)
from worker.policy import Issue, decide, identity
from worker.procutil import Completed
from worker.sanitize import sanitize_public
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


def _cmd_s_strip(command: str) -> str:
    """Command text after cmd.exe /s removes one leading and one trailing quote."""
    prefix = "cmd.exe /d /s /c "
    if not command.startswith(prefix):
        raise AssertionError(command)
    rest = command[len(prefix):]
    if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
        return rest[1:-1]
    return rest


def _cmd_token(command: str) -> str:
    """First cmd token. Quotes keep spaces inside one token."""
    if command.startswith('"'):
        end = command.find('"', 1)
        if end < 0:
            return command
        return command[1:end]
    part = command.split(" ", 1)[0]
    return part


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
        self.assertEqual(
            parse_body("## Goal\nNeeds a person.\n## Task\nanalyze\n## Risk\nhigh").risk,
            "high",
        )
        for goal in ("hello && calc", "see https://evil.test", "token ghp_abcdefghijklmnopqrst"):
            with self.assertRaises(SchemaError):
                parse_body(f"## Goal\n{goal}\n## Task\nsmoke\n## Risk\nnone")
        with self.assertRaises(SchemaError):
            parse_body("## Goal\nok\n## Task\nsmoke\n## Risk\nhigh && calc")

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

    def test_model_lanes_are_off_without_a_process(self) -> None:
        cases = [
            ("lane_disabled", {"agent:hermes", "type:analyze"}, "analyze", "DoNotPublishHermes"),
            ("dsh_deferred", {"agent:dsh", "type:analyze"}, "analyze", "DoNotPublishDsh"),
            ("lane_disabled", {"agent:codex", "type:implement"}, "implement", "DoNotPublishCodex"),
            ("lane_disabled", {"agent:dsh", "type:implement"}, "implement", "DoNotPublishDshImpl"),
        ]
        for reason, labels, task, goal in cases:
            with self.subTest(goal=goal):
                gh = FakeGH()
                body = f"## Goal\n{goal}\n## Task\n{task}\n## Risk\nnone\n"
                gh.add(
                    issue(
                        body=body,
                        labels=frozenset({"bus:ready", "status:todo", *labels}),
                    )
                )
                self.assertEqual(
                    run_tick(gh, self.store, self.log, dry_run=False, executor=Boom()),
                    "blocked",
                )
                text = gh.comments[0][1]
                self.assertIn(reason, text)
                self.assertNotIn(goal, text)
                self.assertNotIn(7, gh.closed)
                self.store = Store(self.root / f"state-{goal}")
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
            self.assertFalse(cfg.analyze)
            self.assertFalse(cfg.implement)
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
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw.pop("lanes")
            bad.write_text(json.dumps(raw), encoding="utf-8")
            legacy = load_config(bad)
            self.assertFalse(legacy.analyze)
            self.assertFalse(legacy.implement)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["lanes"] = {"toolsets": "memory,file,terminal"}
            bad.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(bad)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["lanes"]["analyze_timeout_seconds"] = 5
            bad.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(bad)
            raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
            raw["enabled"] = True
            raw["dry_run"] = True
            raw["lanes"]["enabled"]["analyze"] = True
            bad.write_text(json.dumps(raw), encoding="utf-8")
            armed = load_config(bad)
            self.assertTrue(armed.analyze)
            self.assertFalse(real_work_allowed(armed, armed=True))
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
        prompt = "Public issue analysis.\nGoal: Count the agents."
        hermes_argv = build_hermes_argv(r"C:\hermes\hermes.exe", prompt)
        self.assertIn("--safe-mode", hermes_argv)
        self.assertIn("--ignore-rules", hermes_argv)
        self.assertEqual(hermes_argv[hermes_argv.index("-t") + 1], "todo")
        self.assertNotIn("--yolo", hermes_argv)
        self.assertNotIn("memory", hermes_argv)
        self.assertNotIn("delegation", hermes_argv)
        dsh_argv = build_dsh_argv(DSH_EXE)
        joined = " ".join(dsh_argv)
        self.assertIn("deepseek harness", joined)
        self.assertIn("--profile", dsh_argv)
        self.assertIn("headless", dsh_argv)
        self.assertIn("workspace-write", dsh_argv)
        self.assertIn("never", dsh_argv)
        self.assertIn("--json", dsh_argv)
        self.assertNotIn("--add-dir", dsh_argv)
        self.assertNotIn("dangerously-bypass-approvals-and-sandbox", dsh_argv)
        self.assertNotIn("Count the agents.", joined)
        wrapped = windows_cmdline(dsh_argv)
        self.assertEqual(wrapped[:4], ["cmd.exe", "/d", "/s", "/c"])
        self.assertIn("deepseek harness", wrapped[4])
        self.assertNotIn("Count the agents.", wrapped[4])

    def test_spaced_cmd_path_stays_one_token(self) -> None:
        """cmd /s strips one outer quote pair. A spaced .cmd must survive that."""
        exe = r"E:\deepseek harness\resources\runtime\cli\bin\dsh.cmd"
        argv = build_dsh_argv(exe)
        wrapped = windows_cmdline(argv)
        command = windows_launch_command(wrapped)
        self.assertIsInstance(command, str)
        # Popen(list) would run list2cmdline again and escape the quotes cmd sees.
        self.assertNotEqual(command, subprocess.list2cmdline(wrapped))
        self.assertNotIn('\\"', command)
        self.assertEqual(_cmd_token(_cmd_s_strip(command)), exe)
        self.assertNotEqual(_cmd_token(_cmd_s_strip(command)), r"E:\deepseek")
        self.assertIn("--json", command)
        self.assertNotIn("Count the agents.", command)

        on_path = windows_launch_command(windows_cmdline(build_dsh_argv("dsh.cmd")))
        self.assertEqual(_cmd_token(_cmd_s_strip(on_path)), "dsh.cmd")
        plain = windows_launch_command(windows_cmdline(build_dsh_argv(r"C:\tools\dsh.cmd")))
        self.assertEqual(_cmd_token(_cmd_s_strip(plain)), r"C:\tools\dsh.cmd")

        spaced_arg = [
            exe,
            "--note",
            "hello world",
        ]
        quoted = windows_launch_command(windows_cmdline(spaced_arg))
        self.assertEqual(_cmd_token(_cmd_s_strip(quoted)), exe)
        self.assertIn('"hello world"', _cmd_s_strip(quoted))

        native = [r"C:\Program Files\hermes.exe", "--help"]
        self.assertEqual(windows_cmdline(native), native)
        self.assertIsNone(windows_launch_command(native))
        with self.assertRaises(AllowlistError):
            build_dsh_argv(exe + " & calc.exe")

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
            self.assertEqual(manifest["optional_tasks"], ["analyze", "implement"])
            self.assertIn("dsh_analyze", manifest["deferred_tasks"])
            self.assertIn("codex_exec", manifest["deferred_tasks"])
            self.assertNotIn("hermes", manifest["deferred_tasks"])

    def test_malicious_analyze_does_not_launch(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "hello && calc.exe"),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lane_flags(self.root, analyze=True),
                executor=Boom(),
            ),
            "blocked",
        )
        self.assertNotIn("calc", gh.comments[0][1])
        self.assertNotIn(7, gh.closed)

    def test_high_risk_is_blocked_for_a_human(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents.", risk="high"),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lane_flags(self.root, analyze=True),
                executor=Boom(),
            ),
            "blocked",
        )
        text = gh.comments[0][1]
        self.assertIn("reason: risk", text)
        self.assertIn("human", text)
        self.assertNotIn("Count the agents.", text)
        self.assertNotIn(7, gh.closed)

    def test_dsh_analyze_stays_deferred_when_analyze_is_on(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:dsh", "type:analyze"),
            )
        )
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lane_flags(self.root, analyze=True),
                executor=Boom(),
            ),
            "blocked",
        )
        self.assertIn("dsh_deferred", gh.comments[0][1])

    def test_dry_run_does_not_launch_an_enabled_lane(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=True,
                lanes=lane_flags(self.root, analyze=True),
                executor=Boom(),
            ),
            "dry-run",
        )
        self.assertEqual(gh.comments, [])
        self.assertEqual(list(self.store.claims.glob("*.json")), [])

    def test_analyze_success_sanitizes_and_does_not_close(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        exe.analyze_result = Completed(
            0,
            "Four agents share one issue bus.\n".encode()
            + b"ghp_abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnopqrst",
            b"C:\\Users\\me\\.hermes\\auth.json",
            False,
        )
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lane_flags(self.root, analyze=True),
                executor=exe,
            ),
            "done",
        )
        text = gh.comments[0][1]
        self.assertIn("Four agents share one issue bus.", text)
        self.assertIn("[redacted]", text)
        self.assertNotIn("ghp_", text)
        self.assertNotIn("sk-abc", text)
        self.assertNotIn("auth.json", text)
        self.assertNotIn("Count the agents.", text)
        self.assertIn("not a full", text)
        self.assertIn("--safe-mode", exe.analyze[0][0])
        self.assertEqual(exe.analyze[0][0][exe.analyze[0][0].index("-t") + 1], "todo")
        self.assertIn("status:done", gh.issues[7].labels)
        self.assertNotIn(7, gh.closed)
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False, executor=exe), "idle")
        self.assertEqual(len(exe.analyze), 1)

    def test_analyze_timeout_restart_and_failed_writeback(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        exe.analyze_result = Completed(1, b"partial ghp_abcdefghijklmnopqrstuvwxyz", b"", True)
        lanes = lane_flags(self.root, analyze=True)
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe), "blocked")
        self.assertIn("timeout", gh.comments[0][1])
        self.assertNotIn("ghp_", gh.comments[0][1])
        self.assertNotIn(7, gh.closed)
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe), "idle")
        self.assertEqual(len(exe.analyze), 1)

        gh = FakeGH()
        item = issue(
            body=task_body("analyze", "Count the agents."),
            labels=lane_labels("agent:hermes", "type:analyze"),
        )
        gh.add(item)
        self.store = Store(self.root / "restart-analyze")
        self.store.ensure()
        self.store.save(
            Claim(
                issue=7,
                claim_id="11111111-1111-1111-1111-111111111111",
                identity=identity(item),
                kind="analyze",
                phase="reserved",
                child_started=True,
            )
        )
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=Boom()),
            "blocked",
        )
        self.assertIn("restart_after_child", gh.comments[0][1])

        gh = FakeGH()
        gh.fail_comment = True
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        self.store = Store(self.root / "writeback-analyze")
        self.store.ensure()
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "writeback-failed",
        )
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "idle",
        )
        self.assertEqual(len(exe.analyze), 1)
        self.assertEqual(gh.comments, [])

    def test_missing_binary_and_probe_fail_closed(self) -> None:
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        exe.hermes = False
        lanes = lane_flags(self.root, analyze=True)
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe), "blocked")
        self.assertIn("missing_binary", gh.comments[0][1])
        self.assertEqual(exe.probes, 0)
        self.assertEqual(exe.analyze, [])

        gh = FakeGH()
        gh.add(
            issue(
                number=8,
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        exe.probe = False
        self.store = Store(self.root / "probe")
        self.store.ensure()
        self.assertEqual(run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe), "blocked")
        self.assertIn("tool_allowlist", gh.comments[0][1])
        self.assertEqual(exe.analyze, [])

        gh = FakeGH()
        gh.add(
            issue(
                number=9,
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        self.store = Store(self.root / "real-missing")
        self.store.ensure()
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes),
            "blocked",
        )
        self.assertIn("missing_binary", gh.comments[0][1])

    def test_snapshot_discards_model_output(self) -> None:
        gh = FakeGH()
        gh.flip_after = 2
        gh.add(
            issue(
                body=task_body("analyze", "Count the agents."),
                labels=lane_labels("agent:hermes", "type:analyze"),
            )
        )
        exe = FakeExec()
        exe.analyze_result = Completed(0, b"UNIQUE_MODEL_OUTPUT", b"", False)
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lane_flags(self.root, analyze=True),
                executor=exe,
            ),
            "blocked",
        )
        self.assertEqual(len(exe.analyze), 1)
        self.assertNotIn("UNIQUE_MODEL_OUTPUT", gh.comments[0][1])
        self.assertIn("snapshot_changed", gh.comments[0][1])

    def test_implement_success_closes_only_with_one_pr(self) -> None:
        claim = "11111111-1111-1111-1111-111111111111"
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("implement", "Add a readme line."),
                labels=lane_labels("agent:codex", "type:implement"),
            )
        )
        exe = FakeExec()
        exe.implement_result = Completed(
            0,
            implement_stdout(
                "Opened a review pull request.\n"
                "PR_URL: https://github.com/Siniv26/agent-hub/pull/21\n"
                "sk-abcdefghijklmnopqrst"
            ),
            b"",
            False,
        )
        lanes = lane_flags(self.root, implement=True)
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lanes,
                executor=exe,
                new_id=lambda: claim,
            ),
            "done",
        )
        text = gh.comments[0][1]
        self.assertIn("https://github.com/Siniv26/agent-hub/pull/21", text)
        self.assertNotIn("sk-abc", text)
        self.assertNotIn("Add a readme line.", text)
        self.assertIn("not confined", text)
        self.assertIn(7, gh.closed)
        argv, _cwd, stdin = exe.implement[0]
        self.assertNotIn("Add a readme line.", " ".join(argv))
        self.assertIn("workspace-write", argv)
        self.assertIn("subagent_codex", stdin.decode())
        self.assertIn("Do not merge", stdin.decode())
        self.assertIn("Do not deploy", stdin.decode())
        self.assertIn("Do not delete", stdin.decode())
        self.assertEqual(len(exe.clones), 1)
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "idle",
        )
        self.assertEqual(len(exe.implement), 1)

    def test_implement_blocks_without_pr_or_on_policy(self) -> None:
        lanes = lane_flags(self.root, implement=True)
        claim = "22222222-2222-2222-2222-222222222222"

        def once(stdout: bytes, number: int):
            gh = FakeGH()
            gh.add(
                issue(
                    number=number,
                    body=task_body("implement", "Add a readme line."),
                    labels=lane_labels("agent:dsh", "type:implement"),
                )
            )
            exe = FakeExec()
            exe.implement_result = Completed(0, stdout, b"", False)
            store = Store(self.root / f"impl-{number}")
            store.ensure()
            status = run_tick(
                gh,
                store,
                self.log,
                dry_run=False,
                lanes=lanes,
                executor=exe,
                new_id=lambda: claim,
            )
            return status, gh, exe

        status, gh, exe = once(implement_stdout("I looked around."), 11)
        self.assertEqual(status, "blocked")
        self.assertIn("ambiguous", gh.comments[0][1])
        self.assertNotIn(11, gh.closed)
        self.assertEqual(len(exe.implement), 1)

        status, gh, exe = once(implement_stdout("BLOCKED"), 12)
        self.assertEqual(status, "blocked")
        self.assertIn("ambiguous", gh.comments[0][1])
        self.assertNotIn(12, gh.closed)

        status, gh, exe = once(
            implement_stdout(
                "Opened.\nPR_URL: https://github.com/Siniv26/agent-hub/pull/21",
                codex=2,
            ),
            13,
        )
        self.assertEqual(status, "blocked")
        self.assertIn("ambiguous", gh.comments[0][1])
        self.assertNotIn(13, gh.closed)

        status, gh, exe = once(
            implement_stdout(
                "Opened.\nPR_URL: https://github.com/Siniv26/agent-hub/pull/21",
                extra=[
                    json.dumps(
                        {
                            "type": "tool_call",
                            "name": "shell",
                            "arguments": {"command": "gh pr merge 21"},
                        }
                    )
                ],
            ),
            14,
        )
        self.assertEqual(status, "blocked")
        self.assertIn("policy", gh.comments[0][1])
        self.assertNotIn("gh pr merge", gh.comments[0][1])
        self.assertNotIn(14, gh.closed)

    def test_implement_timeout_missing_binary_and_writeback(self) -> None:
        lanes = lane_flags(self.root, implement=True)
        gh = FakeGH()
        gh.add(
            issue(
                body=task_body("implement", "Add a readme line."),
                labels=lane_labels("agent:codex", "type:implement"),
            )
        )
        exe = FakeExec()
        exe.implement_result = Completed(1, b"", b"secret ghp_abcdefghijklmnopqrstuvwxyz", True)
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "blocked",
        )
        self.assertIn("timeout", gh.comments[0][1])
        self.assertNotIn("ghp_", gh.comments[0][1])
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "idle",
        )
        self.assertEqual(len(exe.implement), 1)

        gh = FakeGH()
        gh.add(
            issue(
                number=15,
                body=task_body("implement", "Add a readme line."),
                labels=lane_labels("agent:codex", "type:implement"),
            )
        )
        exe = FakeExec()
        exe.dsh = False
        self.store = Store(self.root / "no-dsh")
        self.store.ensure()
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "blocked",
        )
        self.assertIn("missing_binary", gh.comments[0][1])
        self.assertEqual(exe.clones, [])

        gh = FakeGH()
        gh.fail_comment = True
        gh.add(
            issue(
                number=16,
                body=task_body("implement", "Add a readme line."),
                labels=lane_labels("agent:codex", "type:implement"),
            )
        )
        exe = FakeExec()
        exe.implement_result = Completed(
            0,
            implement_stdout("Opened.\nPR_URL: https://github.com/Siniv26/agent-hub/pull/21"),
            b"",
            False,
        )
        self.store = Store(self.root / "impl-writeback")
        self.store.ensure()
        self.assertEqual(
            run_tick(
                gh,
                self.store,
                self.log,
                dry_run=False,
                lanes=lanes,
                executor=exe,
                new_id=lambda: "33333333-3333-3333-3333-333333333333",
            ),
            "writeback-failed",
        )
        self.assertEqual(
            run_tick(gh, self.store, self.log, dry_run=False, lanes=lanes, executor=exe),
            "idle",
        )
        self.assertEqual(len(exe.implement), 1)

    def test_output_sanitizer_and_policy_lines(self) -> None:
        dirty = "see C:\\Users\\me\\AppData\\Local\\secret auth.json and " + ("A" * 2000)
        cleaned = sanitize_public(dirty, limit=100)
        self.assertNotIn("Users", cleaned)
        self.assertNotIn("auth.json", cleaned)
        self.assertIn("[truncated]", cleaned)
        secret_only = interpret_analyze(Completed(0, b"ghp_abcdefghijklmnopqrstuvwxyz", b"", False))
        self.assertEqual(secret_only.reason, "model_failed")
        self.assertNotIn("ghp_", secret_only.detail)
        state = {"codex": 0}
        bad = json.dumps(
            {"type": "tool_call", "name": "shell", "arguments": {"command": "git push --force"}}
        ).encode()
        self.assertFalse(line_keeps_running(bad, state))
        good = json.dumps(
            {"type": "tool_call", "name": "subagent_codex", "arguments": {"task": "Do not deploy"}}
        ).encode()
        self.assertTrue(line_keeps_running(good, state))
        self.assertFalse(line_keeps_running(good, state))
        blocked = interpret_implement(
            Completed(0, implement_stdout("BLOCKED\nPR_URL: https://github.com/Siniv26/agent-hub/pull/4"), b"", False)
        )
        self.assertFalse(blocked.close)
        self.assertEqual(blocked.reason, "ambiguous")


def task_body(task: str, goal: str, risk: str = "none") -> str:
    return f"## Goal\n{goal}\n## Task\n{task}\n## Risk\n{risk}\n"


def lane_labels(*names: str) -> frozenset[str]:
    return frozenset({"bus:ready", "status:todo", *names})


def lane_flags(root: Path, *, analyze: bool = False, implement: bool = False) -> LaneConfig:
    return LaneConfig(analyze=analyze, implement=implement, work_dir=root / "work")


def implement_stdout(text: str, *, codex: int = 1, extra: list[str] | None = None) -> bytes:
    lines = [
        json.dumps(
            {"type": "tool_call", "name": "subagent_codex", "arguments": {"task": "edit the readme"}}
        )
        for _ in range(codex)
    ]
    if extra:
        lines.extend(extra)
    lines.append(json.dumps({"type": "final", "reason": {"kind": "completed"}, "text": text}))
    return ("\n".join(lines) + "\n").encode()


class Boom:
    def hermes_exists(self) -> bool:
        raise AssertionError("hermes exists")

    def dsh_exists(self) -> bool:
        raise AssertionError("dsh exists")

    def probe_hermes(self) -> bool:
        raise AssertionError("probe")

    def clone(self, dest: Path) -> bool:
        raise AssertionError("clone")

    def run_analyze(self, argv, cwd) -> Completed:
        raise AssertionError("analyze")

    def run_implement(self, argv, cwd, stdin: bytes) -> Completed:
        raise AssertionError("implement")


class FakeExec:
    def __init__(self) -> None:
        self.analyze: list[tuple] = []
        self.implement: list[tuple] = []
        self.clones: list[Path] = []
        self.probes = 0
        self.hermes = True
        self.dsh = True
        self.probe = True
        self.clone_ok = True
        self.analyze_result = Completed(0, b"A short analysis.\n", b"", False)
        self.implement_result = Completed(0, b"", b"", False)

    def hermes_exists(self) -> bool:
        return self.hermes

    def dsh_exists(self) -> bool:
        return self.dsh

    def probe_hermes(self) -> bool:
        self.probes += 1
        return self.probe

    def clone(self, dest: Path) -> bool:
        self.clones.append(dest)
        return self.clone_ok

    def run_analyze(self, argv, cwd) -> Completed:
        self.analyze.append((argv, cwd))
        return self.analyze_result

    def run_implement(self, argv, cwd, stdin: bytes) -> Completed:
        self.implement.append((argv, cwd, stdin))
        return self.implement_result


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

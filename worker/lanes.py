"""Optional analyze and implement lanes. Defaults are off. Issue text is data, not a shell."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from worker.config import expand_path
from worker.constants import (
    CLONE_TIMEOUT,
    DSH_EXE,
    FORBIDDEN_TOOLSETS,
    HERMES_EXE,
    HERMES_HOME,
    HERMES_MODEL,
    HERMES_PROVIDER,
    HERMES_TOOLSET,
    PROBE_TIMEOUT,
    REPO,
)
from worker.procutil import Completed, run_process, runtime_env
from worker.sanitize import meaningful, sanitize_public

_ID = re.compile(r"^[0-9a-fA-F-]{8,40}$")
_PR_URL = re.compile(r"https://github\.com/Siniv26/agent-hub/pull/(\d+)\b")
_SHELL_META = set("&|<>^%!")
_SHELL_TOOLS = frozenset(
    {
        "shell",
        "bash",
        "powershell",
        "terminal",
        "exec",
        "execute_command",
        "run_command",
        "run_terminal_cmd",
    }
)
_DELETE_TOOLS = frozenset({"delete", "delete_file", "remove_file", "file_delete"})
_FORBIDDEN = re.compile(
    r"(gh\s+pr\s+merge\b"
    r"|gh\s+pr\s+delete\b"
    r"|gh\s+issue\s+delete\b"
    r"|gh\s+repo\s+delete\b"
    r"|git\s+push\b[^\n]*(-f\b|--force\b)"
    r"|git\s+reset\s+--hard\b"
    r"|git\s+clean\b"
    r"|\brm\s+"
    r"|remove-item\b"
    r"|\bdel\s+"
    r"|schtasks\b"
    r"|\bdeploy\b"
    r"|dangerously-bypass"
    r"|danger-full-access"
    r"|--yolo\b"
    r"|--full-auto\b)",
    re.I,
)


class AllowlistError(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    result: str
    reason: str
    detail: str
    close: bool


def analyze_env() -> dict[str, str]:
    """Existing HERMES_HOME only. Nothing is copied into a new home."""
    env = runtime_env()
    env["HERMES_HOME"] = str(expand_path(HERMES_HOME))
    env.pop("GH_TOKEN", None)
    return env


def implement_env() -> dict[str, str]:
    env = runtime_env()
    env.pop("GH_TOKEN", None)
    env.pop("HERMES_HOME", None)
    return env


def analyze_prompt(goal: str) -> str:
    return (
        "Public issue analysis. Reply with plain text only. "
        "Do not read files, browse, run commands, delegate, or reveal secrets. "
        "If the goal needs approval or is ambiguous, reply with exactly BLOCKED.\n"
        "Goal: " + goal
    )


def implement_prompt(goal: str, claim_id: str) -> str:
    if not _ID.fullmatch(claim_id):
        raise AllowlistError("claim id")
    return (
        "Public issue implementation for Siniv26/agent-hub. "
        "Work only in the current directory. "
        "Call subagent_codex exactly once for the code change. "
        "Branch name: worker-" + claim_id + ". "
        "You may open a pull request. "
        "Do not merge. Do not deploy. Do not delete files, branches, or issues. "
        "Do not force-push. Do not print tokens or credential files. "
        "If the goal is ambiguous, reply with exactly BLOCKED and do not open a pull request. "
        "If you open a pull request, the last line must be "
        "PR_URL: https://github.com/Siniv26/agent-hub/pull/<number>.\n"
        "Goal: " + goal
    )


def build_hermes_argv(executable: str, prompt: str) -> list[str]:
    argv = [
        executable,
        "-z",
        prompt,
        "--provider",
        HERMES_PROVIDER,
        "-m",
        HERMES_MODEL,
        "--safe-mode",
        "--ignore-rules",
        "-t",
        HERMES_TOOLSET,
    ]
    validate_hermes_argv(argv)
    return argv


def validate_hermes_argv(argv: list[str]) -> None:
    if "-z" not in argv or "-t" not in argv:
        raise AllowlistError("hermes flags")
    prompt_at = argv.index("-z") + 1
    fixed = [part for index, part in enumerate(argv) if index != prompt_at]
    _no_shell_meta(fixed)
    if argv.count("-t") != 1 or "--safe-mode" not in argv or "--ignore-rules" not in argv:
        raise AllowlistError("hermes flags")
    if "--yolo" in argv or "--accept-hooks" in argv:
        raise AllowlistError("hermes flags")
    toolset = argv[argv.index("-t") + 1]
    if toolset != HERMES_TOOLSET or "," in toolset or not toolset:
        raise AllowlistError("toolset")
    if toolset in FORBIDDEN_TOOLSETS or toolset in {"all", "*"}:
        raise AllowlistError("toolset")
    for name in FORBIDDEN_TOOLSETS:
        if name in fixed:
            raise AllowlistError("forbidden toolset")


def build_dsh_argv(executable: str) -> list[str]:
    argv = [
        executable,
        "--profile",
        "headless",
        "--permission-mode",
        "workspace-write",
        "--approval-policy",
        "never",
        "--json",
    ]
    validate_dsh_argv(argv)
    return argv


def validate_dsh_argv(argv: list[str]) -> None:
    _no_shell_meta(argv)
    needed = (
        "--profile",
        "headless",
        "--permission-mode",
        "workspace-write",
        "--approval-policy",
        "never",
        "--json",
    )
    for flag in needed:
        if flag not in argv:
            raise AllowlistError("dsh flags")
    if "--add-dir" in argv or "--yolo" in argv:
        raise AllowlistError("dsh flags")
    for bad in (
        "dangerously-bypass",
        "dangerously-bypass-approvals-and-sandbox",
        "danger-full-access",
        "read-only",
        "--full-auto",
    ):
        if bad in argv:
            raise AllowlistError("dsh flags")


def windows_cmdline(argv: list[str]) -> list[str]:
    """Run a .cmd through cmd.exe /d /s /c. The task text is not part of this command."""
    if not argv or not argv[0].lower().endswith((".cmd", ".bat")):
        return list(argv)
    return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)]


def probe_accepts(stdout: bytes, stderr: bytes, *, timed_out: bool) -> bool:
    if timed_out:
        return False
    text = b"\n".join((stdout or b"", stderr or b"")).decode("utf-8", errors="replace")
    return "--safe-mode" in text and "--ignore-rules" in text and "-t" in text


def interpret_analyze(completed: Completed) -> Outcome:
    if completed.timed_out:
        return Outcome("blocked", "timeout", "", False)
    text = sanitize_public(completed.stdout.decode("utf-8", errors="replace"))
    if completed.returncode != 0 or not meaningful(text):
        detail = text if meaningful(text) else ""
        return Outcome("blocked", "model_failed", detail, False)
    if _says_blocked(text):
        return Outcome("blocked", "ambiguous", text, False)
    return Outcome("analyze_ok", "", text, False)


def interpret_implement(completed: Completed) -> Outcome:
    events = _events(completed.stdout)
    if _forbidden(events) or completed.killed_by_policy:
        return Outcome("blocked", "policy", "", False)
    if completed.timed_out:
        return Outcome("blocked", "timeout", "", False)
    final = _final(events)
    text = sanitize_public(final)
    codex_calls = _codex_calls(events)
    urls = _pr_urls(completed.stdout.decode("utf-8", errors="replace"))
    if completed.returncode != 0 or not _completed(events):
        return Outcome("blocked", "model_failed", text if meaningful(text) else "", False)
    if codex_calls != 1 or _says_blocked(text) or len(urls) != 1:
        return Outcome("blocked", "ambiguous", text if meaningful(text) else "", False)
    if urls[0] not in text:
        text = (text + "\n" + urls[0]).strip()
    if not meaningful(text):
        return Outcome("blocked", "model_failed", "", False)
    return Outcome("implement_ok", "", text, True)


def line_keeps_running(line: bytes, state: dict[str, int]) -> bool:
    if len(line) > 65536:
        return False
    kind = classify_line(line)
    if kind == "forbidden":
        return False
    if kind == "codex":
        state["codex"] = state.get("codex", 0) + 1
        if state["codex"] > 1:
            return False
    return True


def classify_line(line: bytes) -> str:
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return "ignore"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return "ignore"
    if not isinstance(obj, dict):
        return "ignore"
    name = str(obj.get("name") or obj.get("tool") or obj.get("toolName") or "")
    event = str(obj.get("type") or obj.get("event") or "")
    blob = json.dumps(obj.get("arguments") or obj.get("args") or obj.get("input") or {}, ensure_ascii=False)
    folded = name.casefold()
    lowered = blob.casefold()
    if folded in _DELETE_TOOLS or folded in {"merge", "deploy"}:
        return "forbidden"
    if "dangerously-bypass" in lowered or "danger-full-access" in lowered or "--yolo" in lowered:
        return "forbidden"
    if folded in _SHELL_TOOLS and _FORBIDDEN.search(blob):
        return "forbidden"
    if folded == "subagent_codex" and event in {"", "tool_call", "tool_use", "tool-use"}:
        return "codex"
    return "ok"


class SystemExecutor:
    """Real processes. Tests substitute a fake and never construct this."""

    def __init__(self, work_dir: Path, analyze_timeout: int, implement_timeout: int, gh: str) -> None:
        self.work_dir = work_dir
        self.analyze_timeout = analyze_timeout
        self.implement_timeout = implement_timeout
        self.gh = gh

    def hermes_exists(self) -> bool:
        return expand_path(HERMES_EXE).is_file()

    def dsh_exists(self) -> bool:
        return Path(DSH_EXE).is_file()

    def probe_hermes(self) -> bool:
        exe = str(expand_path(HERMES_EXE))
        completed = run_process(
            [exe, "--help"],
            cwd=str(self.work_dir),
            env=analyze_env(),
            timeout=PROBE_TIMEOUT,
        )
        return probe_accepts(completed.stdout, completed.stderr, timed_out=completed.timed_out)

    def clone(self, dest: Path) -> bool:
        if dest.exists() or not _inside(dest, self.work_dir):
            return False
        completed = run_process(
            [self.gh, "repo", "clone", REPO, str(dest)],
            cwd=str(self.work_dir),
            env=implement_env(),
            timeout=CLONE_TIMEOUT,
        )
        return completed.returncode == 0 and not completed.timed_out and dest.is_dir()

    def run_analyze(self, argv: list[str], cwd: Path) -> Completed:
        validate_hermes_argv(argv)
        cwd.mkdir(parents=True, exist_ok=True)
        return run_process(
            argv,
            cwd=str(cwd),
            env=analyze_env(),
            timeout=self.analyze_timeout,
        )

    def run_implement(self, argv: list[str], cwd: Path, stdin: bytes) -> Completed:
        validate_dsh_argv(argv)
        if not _inside(cwd, self.work_dir):
            raise AllowlistError("cwd")
        state = {"codex": 0}

        def on_line(line: bytes) -> bool:
            return line_keeps_running(line, state)

        launched = windows_cmdline(argv) if os.name == "nt" else list(argv)
        return run_process(
            launched,
            cwd=str(cwd),
            env=implement_env(),
            timeout=self.implement_timeout,
            stdin=stdin,
            on_stdout_line=on_line,
        )


def _no_shell_meta(argv: list[str]) -> None:
    for part in argv:
        if any(ch in part for ch in _SHELL_META):
            raise AllowlistError("shell meta")


def _says_blocked(text: str) -> bool:
    for line in text.splitlines():
        if line.strip() == "BLOCKED":
            return True
    return text.strip() == "BLOCKED"


def _events(blob: bytes) -> list[dict]:
    found = []
    for line in blob.decode("utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.append(obj)
    return found


def _forbidden(events: list[dict]) -> bool:
    for obj in events:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        if classify_line(raw) == "forbidden":
            return True
    return False


def _codex_calls(events: list[dict]) -> int:
    count = 0
    for obj in events:
        if classify_line(json.dumps(obj, ensure_ascii=False).encode("utf-8")) == "codex":
            count += 1
    return count


def _completed(events: list[dict]) -> bool:
    for obj in events:
        event = str(obj.get("type") or obj.get("event") or "")
        if event != "final":
            continue
        reason = obj.get("reason")
        if isinstance(reason, dict) and reason.get("kind") == "completed":
            return True
    return False


def _final(events: list[dict]) -> str:
    for obj in reversed(events):
        if str(obj.get("type") or "") != "final":
            continue
        for key in ("text", "answer", "message"):
            value = obj.get(key)
            if isinstance(value, str):
                return value
    return ""


def _pr_urls(text: str) -> list[str]:
    found = []
    for match in _PR_URL.finditer(text):
        url = match.group(0)
        if url not in found:
            found.append(url)
    return found


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True

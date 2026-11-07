"""Owner-controlled issue schema. The body is data, never a shell command."""

from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED_HEADINGS = frozenset({"Goal", "Task", "Risk"})
TASKS = frozenset({"smoke", "analyze", "implement"})

_HEADING = re.compile(r"^## ([A-Za-z ]+)$")
_TOKEN = re.compile(
    r"(ghp_[A-Za-z0-9]|github_pat_|sk-[A-Za-z0-9]|xox[baprs]-|AKIA[0-9A-Z]{8}|Bearer\s)",
    re.I,
)
_DENY = (
    "://",
    "..",
    "cmd.exe",
    "powershell",
    "cmd /c",
    "--yolo",
    "danger-full-access",
    "dangerously-bypass",
    "workspace-write",
    "--full-auto",
    "node_modules",
    "localappdata",
    ".hermes",
    "auth.json",
    ".env",
    "id_rsa",
)


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class TaskBody:
    goal: str
    task: str
    risk: str


def parse_body(body: str) -> TaskBody:
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.split("\n"):
        match = _HEADING.match(raw.strip())
        if match:
            name = match.group(1).strip()
            if name in sections:
                raise SchemaError("duplicate heading")
            current = name
            sections[name] = []
            continue
        if current is None:
            if raw.strip():
                raise SchemaError("text outside headings")
            continue
        sections[current].append(raw)
    if set(sections) != ALLOWED_HEADINGS:
        raise SchemaError("headings")
    goal = "\n".join(sections["Goal"]).strip()
    task = "\n".join(sections["Task"]).strip()
    risk = "\n".join(sections["Risk"]).strip()
    if task not in TASKS:
        raise SchemaError("task")
    if risk not in {"none", "approval", "high"}:
        raise SchemaError("risk")
    _check_goal(goal)
    return TaskBody(goal=goal, task=task, risk=risk)


def _check_goal(goal: str) -> None:
    if not goal or len(goal) > 400:
        raise SchemaError("goal length")
    if "\n" in goal or any(ord(ch) < 32 for ch in goal):
        raise SchemaError("goal controls")
    folded = goal.casefold()
    if _TOKEN.search(goal):
        raise SchemaError("goal secret")
    for item in _DENY:
        if item in folded:
            raise SchemaError("goal denied")
    for ch in "|&;<>`$\\\"'":
        if ch in goal:
            raise SchemaError("goal metachar")

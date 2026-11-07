"""gh CLI adapter. Reads issues; ignores comments and pull requests."""

from __future__ import annotations

import json
from pathlib import Path

from worker.constants import GH_TIMEOUT, REPO, STATUS_LABELS
from worker.policy import Issue
from worker.procutil import runtime_env, run_process


class GhError(RuntimeError):
    pass


def issue_from_api(obj: dict) -> Issue | None:
    if not isinstance(obj, dict) or "pull_request" in obj:
        return None
    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    login = str(author.get("login") or "")
    labels: set[str] = set()
    for label in obj.get("labels") or []:
        if isinstance(label, dict) and label.get("name"):
            labels.add(str(label["name"]))
        elif isinstance(label, str):
            labels.add(label)
    try:
        number = int(obj["number"])
    except (KeyError, TypeError, ValueError):
        return None
    return Issue(
        number=number,
        title=str(obj.get("title") or ""),
        body=str(obj.get("body") or ""),
        author=login,
        labels=frozenset(labels),
        state=str(obj.get("state") or "OPEN"),
    )


class GhClient:
    def __init__(self, executable: str = "gh", timeout: float = GH_TIMEOUT) -> None:
        if executable not in {"gh", "gh.exe"}:
            raise ValueError("unexpected gh executable")
        self.executable = executable
        self.timeout = timeout

    def list_open(self) -> list[Issue]:
        payload = self._json(
            [
                "issue",
                "list",
                "--repo",
                REPO,
                "--state",
                "open",
                "--limit",
                "30",
                "--json",
                "number,title,author,labels,state",
            ]
        )
        if not isinstance(payload, list):
            raise GhError("issue list was not a list")
        issues = []
        for item in payload:
            issue = issue_from_api(item)
            if issue is not None:
                issues.append(issue)
        return issues

    def view(self, number: int) -> Issue:
        payload = self._json(
            [
                "issue",
                "view",
                str(number),
                "--repo",
                REPO,
                "--json",
                "number,title,body,author,labels,state",
            ]
        )
        issue = issue_from_api(payload if isinstance(payload, dict) else {})
        if issue is None:
            raise GhError("issue view was not usable")
        return issue

    def comment(self, number: int, body: str, *, state_dir: Path) -> bool:
        path = state_dir / f"comment-{number}.txt"
        path.write_text(body, encoding="utf-8")
        try:
            result = self._run(
                ["issue", "comment", str(number), "--repo", REPO, "--body-file", str(path)]
            )
        finally:
            path.unlink(missing_ok=True)
        return result.returncode == 0 and not result.timed_out

    def labels(self, number: int, add: list[str], remove: list[str]) -> bool:
        allowed = STATUS_LABELS | {"bus:ready"}
        if any(label not in allowed for label in [*add, *remove]):
            raise ValueError("refusing to write an unexpected label")
        argv = ["issue", "edit", str(number), "--repo", REPO]
        for label in add:
            argv.extend(["--add-label", label])
        for label in remove:
            argv.extend(["--remove-label", label])
        result = self._run(argv)
        return result.returncode == 0 and not result.timed_out

    def close(self, number: int) -> bool:
        result = self._run(
            ["issue", "close", str(number), "--repo", REPO, "--reason", "completed"]
        )
        return result.returncode == 0 and not result.timed_out

    def _json(self, args: list[str]) -> object:
        result = self._run(args)
        if result.returncode != 0 or result.timed_out:
            raise GhError("gh read failed")
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GhError("gh returned non-json") from exc

    def _run(self, args: list[str]):
        return run_process(
            [self.executable, *args],
            cwd=None,
            env=runtime_env(),
            timeout=self.timeout,
        )

"""Eligibility for the public issue bus. Issue text is never a command."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from worker.constants import AUTHOR, READY_LABEL, REPO, STATUS_LABELS
from worker.schema import SchemaError, parse_body

_PAIRS = {
    frozenset({"agent:partner", "type:smoke"}): "smoke",
    frozenset({"agent:hermes", "type:analyze"}): "hermes_deferred",
    frozenset({"agent:dsh", "type:analyze"}): "dsh_deferred",
    frozenset({"agent:codex", "type:implement"}): "codex_deferred",
}


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author: str
    labels: frozenset[str]
    state: str


@dataclass(frozen=True)
class Decision:
    action: str  # skip, block, run
    kind: str
    reason: str


def identity(issue: Issue) -> str:
    labels = sorted(label for label in issue.labels if label not in STATUS_LABELS)
    blob = json.dumps(
        {
            "author": issue.author,
            "body": issue.body,
            "labels": labels,
            "number": issue.number,
            "state": issue.state.upper(),
            "title": issue.title,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def decide(issue: Issue) -> Decision:
    if issue.state.upper() != "OPEN":
        return Decision("skip", "", "closed")
    if issue.author.casefold() != AUTHOR.casefold():
        return Decision("skip", "", "author")
    if READY_LABEL not in issue.labels:
        return Decision("skip", "", "not-ready")
    status = issue.labels & STATUS_LABELS
    if status != frozenset({"status:todo"}):
        return Decision("skip", "", "status")
    agents = {label for label in issue.labels if label.startswith("agent:")}
    types = {label for label in issue.labels if label.startswith("type:")}
    pair = _PAIRS.get(frozenset(agents | types))
    if pair is None or len(agents) != 1 or len(types) != 1:
        return Decision("block", "unsupported_task", "unsupported_task")
    try:
        body = parse_body(issue.body)
    except SchemaError:
        return Decision("block", pair, "schema")
    expected = {
        "smoke": "smoke",
        "hermes_deferred": "analyze",
        "dsh_deferred": "analyze",
        "codex_deferred": "implement",
    }[pair]
    if body.task != expected:
        return Decision("block", pair, "schema")
    if pair == "smoke":
        return Decision("run", "smoke", "")
    return Decision("block", pair, pair)


def repo_constant() -> str:
    return REPO

"""One-issue tick. v1 runs only the deterministic smoke marker."""

from __future__ import annotations

import uuid
from pathlib import Path

from worker.comments import render
from worker.constants import SMOKE_MARKER
from worker.github import GhError
from worker.policy import Decision, Issue, decide, identity
from worker.state import Claim, Store


def run_tick(gh, store: Store, log, *, dry_run: bool, new_id=None) -> str:
    """Return a short status. dry_run reads GitHub and does not write."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    if store.stop_requested():
        log("stop flag set; not claiming")
        return "stopped"
    try:
        unfinished = store.unfinished()
    except OSError:
        unfinished = []
    if unfinished:
        if dry_run:
            claim = unfinished[0]
            log(f"dry-run unfinished #{claim.issue} {claim.phase}")
            return "dry-run"
        return _advance(gh, store, log, unfinished[0])
    try:
        listed = gh.list_open()
    except GhError:
        log("gh list failed")
        raise
    for stub in sorted(listed, key=lambda item: item.number):
        try:
            issue = gh.view(stub.number)
        except GhError:
            log(f"gh view failed for #{stub.number}")
            raise
        decision = decide(issue)
        if decision.action == "skip":
            continue
        if dry_run:
            log(f"dry-run #{issue.number} {decision.action} {decision.kind} {decision.reason}")
            return "dry-run"
        claim = store.get(issue.number)
        if claim and claim.phase in {"writeback_done", "writeback_failed"}:
            continue
        if claim is None:
            claim = Claim(
                issue=issue.number,
                claim_id=new_id(),
                identity=identity(issue),
                kind=decision.kind,
                phase="reserved",
            )
            store.save(claim)
        return _advance(gh, store, log, claim, decision=decision, issue=issue)
    return "idle"


def _advance(
    gh,
    store: Store,
    log,
    claim: Claim,
    *,
    decision: Decision | None = None,
    issue: Issue | None = None,
) -> str:
    if claim.phase in {"writeback_done", "writeback_failed"}:
        return "already"
    if claim.phase == "writeback_started":
        claim.phase = "writeback_failed"
        store.save(claim)
        log(f"#{claim.issue} writeback was left unfinished; not retrying")
        return "writeback-failed"
    if claim.child_started and not claim.child_finished:
        claim.reason = "restart_after_child"
        claim.result = "blocked"
        return _writeback(gh, store, log, claim)
    if issue is None:
        issue = gh.view(claim.issue)
    if decision is None:
        decision = decide(issue)
    if identity(issue) != claim.identity:
        claim.kind = decision.kind or claim.kind
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
        return _writeback(gh, store, log, claim)
    if decision.action == "skip":
        log(f"#{claim.issue} no longer eligible")
        claim.phase = "writeback_failed"
        store.save(claim)
        return "ineligible"
    if decision.action == "run" and decision.kind == "smoke":
        claim.kind = "smoke"
        claim.result = SMOKE_MARKER
        claim.reason = ""
    else:
        claim.kind = decision.kind
        claim.reason = decision.reason or "unsupported_task"
        claim.result = "blocked"
    fresh = gh.view(claim.issue)
    if identity(fresh) != claim.identity:
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
    return _writeback(gh, store, log, claim)


def _writeback(gh, store: Store, log, claim: Claim) -> str:
    if claim.writeback_started:
        claim.phase = "writeback_failed"
        store.save(claim)
        return "writeback-failed"
    body = render(
        claim_id=claim.claim_id,
        kind=claim.kind,
        snapshot=claim.identity,
        result=claim.result,
        reason=claim.reason,
    )
    current = gh.view(claim.issue)
    if identity(current) != claim.identity:
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
        body = render(
            claim_id=claim.claim_id,
            kind=claim.kind,
            snapshot=claim.identity,
            result=claim.result,
            reason=claim.reason,
        )
    if claim.result == SMOKE_MARKER:
        add, close = ["status:done"], True
    else:
        add, close = ["status:blocked"], False
    remove = [
        label
        for label in ("status:todo", "status:doing")
        if label in current.labels
    ]
    claim.writeback_started = True
    claim.phase = "writeback_started"
    store.save(claim)
    state_dir = store.root
    if not gh.comment(claim.issue, body, state_dir=state_dir):
        claim.phase = "writeback_failed"
        store.save(claim)
        log(f"#{claim.issue} comment failed")
        return "writeback-failed"
    if not gh.labels(claim.issue, add, remove):
        claim.phase = "writeback_failed"
        store.save(claim)
        log(f"#{claim.issue} label update failed")
        return "writeback-failed"
    if close and not gh.close(claim.issue):
        claim.phase = "writeback_failed"
        store.save(claim)
        log(f"#{claim.issue} close failed")
        return "writeback-failed"
    claim.phase = "writeback_done"
    store.save(claim)
    log(f"#{claim.issue} {claim.result} {claim.reason}".strip())
    return "done" if claim.result == SMOKE_MARKER else "blocked"


def next_backoff(current: int) -> int:
    from worker.constants import POLL_MAX, POLL_MIN

    return min(POLL_MAX, max(POLL_MIN, current * 2))


def assert_worker_python(executable: Path) -> None:
    text = str(executable).casefold().replace("\\", "/")
    if "/hermes/" in f"/{text}/":
        raise SystemExit("Refusing the Hermes bundled Python. Use PATH Python 3.13.")

"""One-issue tick. Smoke is the default lane. Analyze and implement are optional."""

from __future__ import annotations

import uuid
from pathlib import Path

from worker.comments import render
from worker.config import LaneConfig
from worker.constants import HERMES_EXE, SMOKE_MARKER
from worker.github import GhError
from worker.lanes import (
    AllowlistError,
    SystemExecutor,
    analyze_prompt,
    build_dsh_argv,
    build_hermes_argv,
    implement_prompt,
    interpret_analyze,
    interpret_implement,
)
from worker.policy import Decision, Issue, decide, identity
from worker.schema import SchemaError, parse_body
from worker.state import Claim, Store


def run_tick(
    gh,
    store: Store,
    log,
    *,
    dry_run: bool,
    new_id=None,
    lanes: LaneConfig | None = None,
    executor=None,
) -> str:
    """Return a short status. dry_run reads GitHub and does not write."""
    new_id = new_id or (lambda: str(uuid.uuid4()))
    lanes = lanes or LaneConfig()
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
        return _advance(gh, store, log, unfinished[0], lanes=lanes, executor=executor)
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
        decision = decide(issue, analyze=lanes.analyze, implement=lanes.implement)
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
        return _advance(
            gh,
            store,
            log,
            claim,
            decision=decision,
            issue=issue,
            lanes=lanes,
            executor=executor,
        )
    return "idle"


def _advance(
    gh,
    store: Store,
    log,
    claim: Claim,
    *,
    decision: Decision | None = None,
    issue: Issue | None = None,
    lanes: LaneConfig | None = None,
    executor=None,
) -> str:
    lanes = lanes or LaneConfig()
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
        claim.detail = ""
        claim.close_issue = False
        return _writeback(gh, store, log, claim)
    if claim.child_finished:
        return _writeback_checked(gh, store, log, claim)
    if issue is None:
        issue = gh.view(claim.issue)
    if decision is None:
        decision = decide(issue, analyze=lanes.analyze, implement=lanes.implement)
    if identity(issue) != claim.identity:
        claim.kind = decision.kind or claim.kind
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
        claim.detail = ""
        claim.close_issue = False
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
        claim.detail = ""
        claim.close_issue = False
    elif decision.action == "run" and decision.kind in {"analyze", "implement"}:
        _execute_model(
            store,
            log,
            claim,
            issue,
            lanes=lanes,
            executor=_executor(lanes, executor),
        )
    else:
        claim.kind = decision.kind
        claim.reason = decision.reason or "unsupported_task"
        claim.result = "blocked"
        claim.detail = ""
        claim.close_issue = False
    return _writeback_checked(gh, store, log, claim)


def _execute_model(store: Store, log, claim: Claim, issue: Issue, *, lanes: LaneConfig, executor) -> None:
    claim.close_issue = False
    claim.detail = ""
    try:
        body = parse_body(issue.body)
    except SchemaError:
        claim.kind = claim.kind or "unsupported_task"
        claim.reason = "schema"
        claim.result = "blocked"
        return
    try:
        if claim.kind == "analyze":
            _run_analyze(store, log, claim, body.goal, lanes, executor)
        else:
            _run_implement(store, log, claim, body.goal, lanes, executor)
    except AllowlistError:
        claim.reason = "tool_allowlist"
        claim.result = "blocked"
        claim.detail = ""
        claim.close_issue = False
        if not claim.child_finished:
            claim.child_started = False


def _run_analyze(store, log, claim: Claim, goal: str, lanes: LaneConfig, executor) -> None:
    claim.kind = "analyze"
    if not executor.hermes_exists():
        claim.reason = "missing_binary"
        claim.result = "blocked"
        return
    if not executor.probe_hermes():
        claim.reason = "tool_allowlist"
        claim.result = "blocked"
        return
    prompt = analyze_prompt(goal)
    argv = build_hermes_argv(str(_hermes_exe()), prompt)
    claim.child_started = True
    store.save(claim)
    completed = executor.run_analyze(argv, _analyze_cwd(lanes))
    outcome = interpret_analyze(completed)
    _store_outcome(store, claim, outcome)
    log(f"#{claim.issue} analyze child finished {claim.reason or claim.result}")


def _run_implement(store, log, claim: Claim, goal: str, lanes: LaneConfig, executor) -> None:
    claim.kind = "implement"
    if not executor.dsh_exists():
        claim.reason = "missing_binary"
        claim.result = "blocked"
        return
    prompt = implement_prompt(goal, claim.claim_id)
    argv = build_dsh_argv(_dsh_exe())
    dest = _work_dir(lanes) / claim.claim_id
    if not executor.clone(dest):
        claim.reason = "workspace"
        claim.result = "blocked"
        return
    claim.child_started = True
    store.save(claim)
    completed = executor.run_implement(argv, dest, prompt.encode("utf-8"))
    outcome = interpret_implement(completed)
    _store_outcome(store, claim, outcome)
    log(f"#{claim.issue} implement child finished {claim.reason or claim.result}")


def _store_outcome(store: Store, claim: Claim, outcome) -> None:
    claim.child_finished = True
    claim.result = outcome.result
    claim.reason = outcome.reason
    claim.detail = outcome.detail
    claim.close_issue = outcome.close
    store.save(claim)


def _writeback_checked(gh, store: Store, log, claim: Claim) -> str:
    current = gh.view(claim.issue)
    if identity(current) != claim.identity:
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
        claim.detail = ""
        claim.close_issue = False
    return _writeback(gh, store, log, claim)


def _writeback(gh, store: Store, log, claim: Claim) -> str:
    if claim.writeback_started:
        claim.phase = "writeback_failed"
        store.save(claim)
        return "writeback-failed"
    current = gh.view(claim.issue)
    if identity(current) != claim.identity:
        claim.reason = "snapshot_changed"
        claim.result = "blocked"
        claim.detail = ""
        claim.close_issue = False
    body = render(
        claim_id=claim.claim_id,
        kind=claim.kind,
        snapshot=claim.identity,
        result=claim.result,
        reason=claim.reason,
        detail=claim.detail,
    )
    if claim.result == SMOKE_MARKER:
        add, close = ["status:done"], True
    elif claim.result == "implement_ok" and claim.close_issue:
        add, close = ["status:done"], True
    elif claim.result in {"analyze_ok", "implement_ok"}:
        add, close = ["status:done"], False
    else:
        add, close = ["status:blocked"], False
    remove = [label for label in ("status:todo", "status:doing") if label in current.labels]
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
    if claim.result == SMOKE_MARKER:
        return "done"
    if claim.result in {"analyze_ok", "implement_ok"}:
        return "done"
    return "blocked"


def _executor(lanes: LaneConfig, executor):
    if executor is not None:
        return executor
    work = lanes.work_dir or Path("work")
    work.mkdir(parents=True, exist_ok=True)
    return SystemExecutor(work, lanes.analyze_timeout, lanes.implement_timeout, lanes.gh_executable)


def _work_dir(lanes: LaneConfig) -> Path:
    work = lanes.work_dir or Path("work")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _analyze_cwd(lanes: LaneConfig) -> Path:
    return _work_dir(lanes) / "hermes-cwd"


def _hermes_exe() -> Path:
    from worker.config import expand_path

    return expand_path(HERMES_EXE)


def _dsh_exe() -> str:
    from worker.constants import DSH_EXE

    return DSH_EXE


def next_backoff(current: int) -> int:
    from worker.constants import POLL_MAX, POLL_MIN

    return min(POLL_MAX, max(POLL_MIN, current * 2))


def assert_worker_python(executable: Path) -> None:
    text = str(executable).casefold().replace("\\", "/")
    if "/hermes/" in f"/{text}/":
        raise SystemExit("Refusing the Hermes bundled Python. Use PATH Python 3.13.")

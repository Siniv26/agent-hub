"""GitHub comments built only from fixed fields."""

from __future__ import annotations

from worker.constants import PUBLISH_CHARS, REASONS, SMOKE_MARKER


def render(*, claim_id: str, kind: str, snapshot: str, result: str, reason: str) -> str:
    if result == SMOKE_MARKER and reason:
        raise ValueError("smoke result cannot carry a reason")
    if result == SMOKE_MARKER:
        outcome = SMOKE_MARKER
        detail = "Deterministic bus check. No model process was started."
    elif result == "blocked":
        if reason not in REASONS:
            raise ValueError("unknown reason")
        outcome = "blocked"
        detail = f"reason: {reason}\n\n{REASONS[reason]}"
    else:
        raise ValueError("unsupported result")
    text = "\n".join(
        [
            "## agent-hub-worker",
            "",
            f"- claim: {claim_id}",
            f"- task: {kind}",
            f"- snapshot: {snapshot[:12]}",
            f"- result: {outcome}",
            "",
            detail,
            "",
        ]
    )
    if len(text) > PUBLISH_CHARS:
        raise ValueError("comment too long")
    return text

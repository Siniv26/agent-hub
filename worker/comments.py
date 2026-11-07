"""GitHub comments built only from fixed fields and sanitized model text."""

from __future__ import annotations

from worker.constants import ANALYZE_NOTE, IMPLEMENT_NOTE, PUBLISH_CHARS, REASONS, SMOKE_MARKER
from worker.sanitize import meaningful, sanitize_public

_OK = {"analyze_ok": ANALYZE_NOTE, "implement_ok": IMPLEMENT_NOTE}
_EXCERPT = frozenset({"ambiguous", "model_failed"})


def render(
    *,
    claim_id: str,
    kind: str,
    snapshot: str,
    result: str,
    reason: str,
    detail: str = "",
) -> str:
    if result == SMOKE_MARKER and (reason or detail):
        raise ValueError("smoke result cannot carry a reason")
    if result == SMOKE_MARKER:
        outcome = SMOKE_MARKER
        body = "Deterministic bus check. No model process was started."
    elif result in _OK:
        if reason:
            raise ValueError("ok result cannot carry a reason")
        clean = sanitize_public(detail)
        if not meaningful(clean):
            raise ValueError("empty model text")
        outcome = "ok"
        body = _OK[result] + "\n\n" + clean
    elif result == "blocked":
        if reason not in REASONS:
            raise ValueError("unknown reason")
        outcome = "blocked"
        body = f"reason: {reason}\n\n{REASONS[reason]}"
        if reason in _EXCERPT and detail:
            extra = sanitize_public(detail, limit=500)
            if meaningful(extra):
                body = body + "\n\n" + extra
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
            body,
            "",
        ]
    )
    if len(text) > PUBLISH_CHARS:
        raise ValueError("comment too long")
    return text

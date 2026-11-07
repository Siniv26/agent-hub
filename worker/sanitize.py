"""Public comment text. Secrets and local paths are removed before anything is saved."""

from __future__ import annotations

import re

from worker.constants import MODEL_CHARS

_SECRET = re.compile(
    r"(ghp_[A-Za-z0-9]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|sk-[A-Za-z0-9]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|AKIA[0-9A-Z]{8,}"
    r"|Bearer\s+\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,400})",
    re.I,
)
_PATH = re.compile(
    r"([A-Za-z]:\\Users\\[^\s\"']+"
    r"|/home/[^\s\"']+"
    r"|%LOCALAPPDATA%"
    r"|LOCALAPPDATA"
    r"|OpenAI\.Codex_[^\s\"']+"
    r"|\\\.hermes\b"
    r"|auth\.json"
    r"|\.env\b"
    r"|id_rsa)",
    re.I,
)


def sanitize_public(text: str, *, limit: int = MODEL_CHARS) -> str:
    raw = text.replace("\x00", "")
    kept = []
    for ch in raw:
        if ch in "\n\t" or ord(ch) >= 32:
            kept.append(ch)
        else:
            kept.append(" ")
    cleaned = _SECRET.sub("[redacted]", "".join(kept))
    cleaned = _PATH.sub("[redacted]", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "\n[truncated]"
    return cleaned


def meaningful(text: str) -> bool:
    core = text.replace("[redacted]", "").replace("[truncated]", "").strip()
    return bool(core)

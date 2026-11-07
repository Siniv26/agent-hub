"""Pinned facts from the machine preflight. v1 does not launch these programs."""

from __future__ import annotations

VERSION = "0.1.0"
REPO = "Siniv26/agent-hub"
AUTHOR = "Siniv26"
READY_LABEL = "bus:ready"
SMOKE_MARKER = "BUS_SMOKE_OK"

DEPLOY_ROOT = "%LOCALAPPDATA%\\AgentHub\\worker"
STATE_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\state"
LOG_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\logs"
WORK_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\work"

# Recorded so the bundle matches the preflight. Not executed in v1.
HERMES_EXE = (
    "%LOCALAPPDATA%\\Packages\\OpenAI.Codex_2p2nqsd0c76g0"
    "\\LocalCache\\Local\\hermes\\bin\\hermes.exe"
)
HERMES_HOME = (
    "%LOCALAPPDATA%\\Packages\\OpenAI.Codex_2p2nqsd0c76g0"
    "\\LocalCache\\Local\\hermes"
)
HERMES_VERSION = "0.22.7"
CODEX_EXE = "%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\14b838c1d074ff01\\codex.exe"
CODEX_VERSION = "0.167.0-alpha.3.2"
DSH_EXE = "E:\\deepseek harness\\resources\\runtime\\cli\\bin\\dsh.cmd"
DSH_VERSION = "0.2.2-rc.1"
GH_VERSION_SEEN = "2.103.3"

POLL_MIN = 15
POLL_MAX = 120
GH_TIMEOUT = 60
PUBLISH_CHARS = 4000
CAPTURE_BYTES = 1_000_000

STATUS_LABELS = frozenset(
    {"status:todo", "status:doing", "status:blocked", "status:done"}
)

# Public text only. Never append issue body, paths, or command output.
REASONS = {
    "schema": "Issue body did not match the worker schema. Nothing ran.",
    "unsupported_task": "This label pair is not an automatic task. Nothing ran.",
    "snapshot_changed": "Issue changed after claim. Nothing ran.",
    "restart_after_child": "Worker restarted after work had started. It was not run again.",
    "writeback_failed": "GitHub write failed. It was not retried.",
    "hermes_deferred": (
        "Hermes was not started. Empty -t means all tools and is never used. "
        "memory, file, terminal, browser, and delegation are forbidden on a public issue. "
        "todo-only with --safe-mode still shares the real HERMES_HOME, and copying "
        "credentials into a fresh home is not allowed, so analysis is deferred."
    ),
    "codex_deferred": (
        "Codex was not started. A read-only sandbox can still read the home directory "
        "and can still load MCP or user config. That is not an isolation boundary."
    ),
    "dsh_deferred": (
        "DSH was not started. Headless read-only does not confine local reads or network, "
        "and subagent_codex is not invoked."
    ),
}

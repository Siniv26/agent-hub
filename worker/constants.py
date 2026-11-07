"""Pinned facts from the machine preflight."""

from __future__ import annotations

VERSION = "1.1.1"
REPO = "Siniv26/agent-hub"
AUTHOR = "Siniv26"
READY_LABEL = "bus:ready"
SMOKE_MARKER = "BUS_SMOKE_OK"

DEPLOY_ROOT = "%LOCALAPPDATA%\\AgentHub\\worker"
STATE_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\state"
LOG_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\logs"
WORK_DIR = "%LOCALAPPDATA%\\AgentHub\\worker\\work"

# Recorded paths. Analyze uses Hermes. Implement uses DSH. codex.exe is not launched.
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
PROBE_TIMEOUT = 20
CLONE_TIMEOUT = 120
ANALYZE_TIMEOUT_DEFAULT = 300
ANALYZE_TIMEOUT_MIN = 30
ANALYZE_TIMEOUT_MAX = 600
IMPLEMENT_TIMEOUT_DEFAULT = 900
IMPLEMENT_TIMEOUT_MIN = 60
IMPLEMENT_TIMEOUT_MAX = 1200
PUBLISH_CHARS = 4000
MODEL_CHARS = 1000
CAPTURE_BYTES = 1_000_000

# Empty -t enables every Hermes tool. The public worker always passes this one name.
HERMES_TOOLSET = "todo"
FORBIDDEN_TOOLSETS = frozenset({"memory", "file", "terminal", "browser", "delegation"})
HERMES_PROVIDER = "deepseek"
HERMES_MODEL = "deepseek-flash"

STATUS_LABELS = frozenset(
    {"status:todo", "status:doing", "status:blocked", "status:done"}
)

# Public text only. Never append the issue body, paths, or raw command output.
REASONS = {
    "schema": "Issue body did not match the worker schema. Nothing ran.",
    "unsupported_task": "This label pair is not an automatic task. Nothing ran.",
    "snapshot_changed": "Issue changed after claim. Nothing ran.",
    "restart_after_child": "Worker restarted after work had started. It was not run again.",
    "writeback_failed": "GitHub write failed. It was not retried.",
    "risk": "Risk is not none. A human has to take this issue. Nothing ran.",
    "lane_disabled": "This lane is off in config. Nothing ran.",
    "missing_binary": "The lane program is not at the recorded path. Nothing ran.",
    "tool_allowlist": (
        "The tool allowlist was not the safe set, or Hermes did not confirm "
        "--safe-mode and -t. The model was not started."
    ),
    "timeout": "The lane timed out and the child was killed. It was not run again.",
    "model_failed": "The lane exited with an error. It was not run again.",
    "ambiguous": "The lane did not meet the success rule. The issue stays open for a human.",
    "workspace": "The implement workspace was not prepared. Nothing ran.",
    "policy": "The lane tried a forbidden merge, deploy, or delete. It was stopped and was not run again.",
    "codex_deferred": (
        "codex.exe was not started. codex exec -s read-only can still read the home "
        "directory and can still load MCP or user config, and it cannot safely write. "
        "dangerously-bypass is refused. Implementation uses DSH only when that lane is on."
    ),
    "dsh_deferred": (
        "DSH was not started for analysis. Headless read-only denies writes but does not "
        "confine local reads or network. Analysis uses Hermes when that lane is on."
    ),
}

ANALYZE_NOTE = (
    "Untrusted model text, truncated. Hermes ran with --safe-mode, --ignore-rules, "
    "and only the todo toolset. Credentials were not copied. This is not a full "
    "filesystem or network sandbox."
)
IMPLEMENT_NOTE = (
    "Untrusted model text, truncated. DSH ran headless in a fresh clone with "
    "workspace-write. Reads and network are not confined. codex.exe was not started. "
    "Merge, deploy, and delete are refused."
)

"""Local config. Unknown keys and token-like strings are refused."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from worker.constants import (
    ANALYZE_TIMEOUT_DEFAULT,
    ANALYZE_TIMEOUT_MAX,
    ANALYZE_TIMEOUT_MIN,
    DEPLOY_ROOT,
    IMPLEMENT_TIMEOUT_DEFAULT,
    IMPLEMENT_TIMEOUT_MAX,
    IMPLEMENT_TIMEOUT_MIN,
    LOG_DIR,
    POLL_MAX,
    POLL_MIN,
    REPO,
    STATE_DIR,
    WORK_DIR,
)

_TOKEN = re.compile(
    r"(ghp_[A-Za-z0-9]|github_pat_|sk-[A-Za-z0-9]|xox[baprs]-|AKIA[0-9A-Z]{8}|Bearer\s)",
    re.I,
)
_TOP_KEYS = frozenset(
    {
        "enabled",
        "dry_run",
        "repo",
        "state_dir",
        "log_dir",
        "work_dir",
        "poll_seconds",
        "gh_executable",
        "lanes",
    }
)
_LANE_KEYS = frozenset({"enabled", "analyze_timeout_seconds", "implement_timeout_seconds"})
_LANE_FLAGS = frozenset({"analyze", "implement"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LaneConfig:
    analyze: bool = False
    implement: bool = False
    analyze_timeout: int = ANALYZE_TIMEOUT_DEFAULT
    implement_timeout: int = IMPLEMENT_TIMEOUT_DEFAULT
    work_dir: Path | None = None
    gh_executable: str = "gh"


@dataclass(frozen=True)
class Config:
    enabled: bool
    dry_run: bool
    repo: str
    state_dir: Path
    log_dir: Path
    work_dir: Path
    poll_seconds: int
    gh_executable: str
    analyze: bool = False
    implement: bool = False
    analyze_timeout: int = ANALYZE_TIMEOUT_DEFAULT
    implement_timeout: int = IMPLEMENT_TIMEOUT_DEFAULT
    source: Path | None = None


def lane_config(cfg: Config) -> LaneConfig:
    return LaneConfig(
        analyze=cfg.analyze,
        implement=cfg.implement,
        analyze_timeout=cfg.analyze_timeout,
        implement_timeout=cfg.implement_timeout,
        work_dir=cfg.work_dir,
        gh_executable=cfg.gh_executable,
    )


def expand_path(value: str) -> Path:
    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    text = re.sub(r"%([^%]+)%", repl, value).replace("\\", "/")
    return Path(os.path.expandvars(os.path.expanduser(text)))


def load_config(path: Path) -> Config:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError("config must be an object")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError("unknown config keys")
    _reject_secrets(data)
    if data.get("repo") != REPO:
        raise ConfigError("repo is not Siniv26/agent-hub")
    if "%LOCALAPPDATA%" in json.dumps(data) and not os.environ.get("LOCALAPPDATA"):
        raise ConfigError("LOCALAPPDATA is not set")
    if data.get("state_dir") != STATE_DIR or data.get("log_dir") != LOG_DIR:
        raise ConfigError("state_dir or log_dir is not the deploy root")
    if data.get("work_dir") != WORK_DIR:
        raise ConfigError("work_dir is not the deploy root")
    if type(data.get("enabled")) is not bool or type(data.get("dry_run")) is not bool:
        raise ConfigError("enabled and dry_run must be booleans")
    gh_exe = data.get("gh_executable")
    if gh_exe not in {"gh", "gh.exe"}:
        raise ConfigError("gh_executable must be gh")
    poll = data.get("poll_seconds")
    if type(poll) is not int or not POLL_MIN <= poll <= POLL_MAX:
        raise ConfigError("poll_seconds out of range")
    analyze, implement, analyze_timeout, implement_timeout = _parse_lanes(data.get("lanes", {}))
    cfg = Config(
        enabled=data["enabled"],
        dry_run=data["dry_run"],
        repo=REPO,
        state_dir=expand_path(STATE_DIR),
        log_dir=expand_path(LOG_DIR),
        work_dir=expand_path(WORK_DIR),
        poll_seconds=poll,
        gh_executable=gh_exe,
        analyze=analyze,
        implement=implement,
        analyze_timeout=analyze_timeout,
        implement_timeout=implement_timeout,
        source=path,
    )
    ensure_layout(cfg)
    return cfg


def ensure_layout(cfg: Config) -> None:
    root = expand_path(DEPLOY_ROOT)
    for path in (cfg.state_dir, cfg.log_dir, cfg.work_dir):
        if not _inside(path, root):
            raise ConfigError("refusing a directory outside the deploy root")


def real_work_allowed(cfg: Config, *, armed: bool) -> bool:
    return bool(cfg.enabled and not cfg.dry_run and armed)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _parse_lanes(raw: object) -> tuple[bool, bool, int, int]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("lanes must be an object")
    if set(raw) - _LANE_KEYS:
        raise ConfigError("unknown config keys")
    enabled = raw.get("enabled", {})
    if not isinstance(enabled, dict) or set(enabled) - _LANE_FLAGS:
        raise ConfigError("lanes.enabled must be an object")
    analyze = enabled.get("analyze", False)
    implement = enabled.get("implement", False)
    if type(analyze) is not bool or type(implement) is not bool:
        raise ConfigError("lane flags must be booleans")
    analyze_timeout = raw.get("analyze_timeout_seconds", ANALYZE_TIMEOUT_DEFAULT)
    implement_timeout = raw.get("implement_timeout_seconds", IMPLEMENT_TIMEOUT_DEFAULT)
    if type(analyze_timeout) is not int or not ANALYZE_TIMEOUT_MIN <= analyze_timeout <= ANALYZE_TIMEOUT_MAX:
        raise ConfigError("analyze_timeout_seconds out of range")
    if (
        type(implement_timeout) is not int
        or not IMPLEMENT_TIMEOUT_MIN <= implement_timeout <= IMPLEMENT_TIMEOUT_MAX
    ):
        raise ConfigError("implement_timeout_seconds out of range")
    return analyze, implement, analyze_timeout, implement_timeout


def _reject_secrets(value: object) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item)
    elif isinstance(value, str) and _TOKEN.search(value):
        raise ConfigError("config contains a secret-like string")

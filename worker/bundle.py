"""Build the Windows deploy zip. No secrets and no model binaries."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from worker.constants import VERSION

_ROOT = Path(__file__).resolve().parent.parent
_PATHS = (
    "worker/__init__.py",
    "worker/__main__.py",
    "worker/bundle.py",
    "worker/cli.py",
    "worker/comments.py",
    "worker/config.py",
    "worker/constants.py",
    "worker/engine.py",
    "worker/github.py",
    "worker/lanes.py",
    "worker/policy.py",
    "worker/procutil.py",
    "worker/sanitize.py",
    "worker/schema.py",
    "worker/state.py",
    "config.example.json",
    "scripts/run.ps1",
    "scripts/install-logon-task.ps1",
    "scripts/uninstall-logon-task.ps1",
    "scripts/verify.ps1",
    "docs/OPS.zh.md",
    "docs/RUN.txt",
    ".github/ISSUE_TEMPLATE/bus-task.md",
    ".github/ISSUE_TEMPLATE/bus-analyze.md",
    ".github/ISSUE_TEMPLATE/bus-implement.md",
)


def build_bundle(destination: Path, root: Path | None = None) -> Path:
    root = root or _ROOT
    destination.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, bytes]] = []
    for rel in _PATHS:
        data = (root / rel).read_bytes()
        _reject_secrets(data, rel)
        files.append((rel, data))
    manifest = {
        "name": "agent-hub-worker",
        "version": VERSION,
        "automatic_tasks": ["smoke"],
        "optional_tasks": ["analyze", "implement"],
        "deferred_tasks": ["dsh_analyze", "codex_exec"],
        "files": [
            {"path": rel, "sha256": hashlib.sha256(data).hexdigest()}
            for rel, data in files
        ],
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {rel}\n" for rel, data in files
    ).encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, data in files:
            _write(archive, rel, data)
        _write(archive, "manifest.json", manifest_bytes)
        _write(archive, "SHA256SUMS", sums)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    sidecar = destination.parent / "SHA256SUMS.txt"
    sidecar.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    return destination


def _write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 11, 7, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _reject_secrets(data: bytes, rel: str) -> None:
    text = data.decode("utf-8", errors="replace")
    if re.search(
        r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
        text,
    ):
        raise RuntimeError(f"refusing secret-like text in {rel}")

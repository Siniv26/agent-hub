"""Local claim files and a single-process lock."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class LockError(RuntimeError):
    pass


@dataclass
class Claim:
    issue: int
    claim_id: str
    identity: str
    kind: str
    phase: str
    child_started: bool = False
    child_finished: bool = False
    writeback_started: bool = False
    reason: str = ""
    result: str = ""
    detail: str = ""
    close_issue: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "issue": self.issue,
            "claim_id": self.claim_id,
            "identity": self.identity,
            "kind": self.kind,
            "phase": self.phase,
            "child_started": self.child_started,
            "child_finished": self.child_finished,
            "writeback_started": self.writeback_started,
            "reason": self.reason,
            "result": self.result,
            "detail": self.detail,
            "close_issue": self.close_issue,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "Claim":
        return cls(
            issue=int(data["issue"]),
            claim_id=str(data["claim_id"]),
            identity=str(data["identity"]),
            kind=str(data["kind"]),
            phase=str(data["phase"]),
            child_started=bool(data.get("child_started")),
            child_finished=bool(data.get("child_finished")),
            writeback_started=bool(data.get("writeback_started")),
            reason=str(data.get("reason") or ""),
            result=str(data.get("result") or ""),
            detail=str(data.get("detail") or ""),
            close_issue=bool(data.get("close_issue")),
        )


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.claims = root / "claims"
        self.lock_path = root / "worker.lock"
        self.stop_path = root / "STOP"
        self.armed_path = root / "ARMED"

    def ensure(self) -> None:
        self.claims.mkdir(parents=True, exist_ok=True)

    def path_for(self, number: int) -> Path:
        return self.claims / f"{number}.json"

    def get(self, number: int) -> Claim | None:
        path = self.path_for(number)
        if not path.exists():
            return None
        return Claim.from_json(json.loads(path.read_text(encoding="utf-8")))

    def save(self, claim: Claim) -> None:
        self.ensure()
        path = self.path_for(claim.issue)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(claim.to_json(), sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def unfinished(self) -> list[Claim]:
        if not self.claims.exists():
            return []
        found = []
        for path in sorted(self.claims.glob("*.json")):
            claim = Claim.from_json(json.loads(path.read_text(encoding="utf-8")))
            if claim.phase not in {"writeback_done", "writeback_failed"}:
                found.append(claim)
        return found

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def request_stop(self) -> None:
        self.ensure()
        self.stop_path.write_text("stop\n", encoding="utf-8")

    def clear_stop(self) -> None:
        self.stop_path.unlink(missing_ok=True)

    def arm(self) -> None:
        self.ensure()
        self.armed_path.write_text("armed\n", encoding="utf-8")

    def disarm(self) -> None:
        self.armed_path.unlink(missing_ok=True)

    def armed(self) -> bool:
        return self.armed_path.exists()


class Lock:
    def __init__(self, path: Path, pid_alive=None) -> None:
        self.path = path
        self.pid_alive = pid_alive or _pid_alive
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            self._take_stale()
        os.write(self._fd, str(os.getpid()).encode("ascii"))

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)

    def _take_stale(self) -> None:
        try:
            pid = int(self.path.read_text(encoding="ascii").strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid and self.pid_alive(pid):
            raise LockError("worker already running")
        self.path.unlink(missing_ok=True)
        self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

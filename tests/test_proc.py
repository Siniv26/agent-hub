"""Process cleanup. No network and no model CLIs."""

from __future__ import annotations

import os
import sys
import time
import unittest

from worker.lanes import line_keeps_running
from worker.procutil import run_process, runtime_env


class ProcTests(unittest.TestCase):
    def test_timeout_kills_the_child(self) -> None:
        started = time.monotonic()
        result = run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=None,
            env=runtime_env(),
            timeout=1,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 8)

    def test_runtime_env_drops_github_token(self) -> None:
        previous = os.environ.get("GH_TOKEN")
        os.environ["GH_TOKEN"] = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        try:
            env = runtime_env()
        finally:
            if previous is None:
                os.environ.pop("GH_TOKEN", None)
            else:
                os.environ["GH_TOKEN"] = previous
        self.assertNotIn("GH_TOKEN", env)
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_forbidden_tool_call_kills_the_child(self) -> None:
        script = (
            "import json,sys,time;"
            "sys.stdout.write(json.dumps({'type':'tool_call','name':'shell',"
            "'arguments':{'command':'gh pr merge 1'}})+'\\n');"
            "sys.stdout.flush();"
            "time.sleep(30)"
        )
        state = {"codex": 0}
        started = time.monotonic()
        result = run_process(
            [sys.executable, "-c", script],
            cwd=None,
            env=runtime_env(),
            timeout=20,
            on_stdout_line=lambda line: line_keeps_running(line, state),
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.killed_by_policy)
        self.assertFalse(result.timed_out)
        self.assertLess(elapsed, 8)


if __name__ == "__main__":
    unittest.main()

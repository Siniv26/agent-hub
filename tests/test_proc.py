"""Process cleanup. No network and no model CLIs."""

from __future__ import annotations

import os
import sys
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest

from toolbox.engine_common import CommandCancelled, run_cancellable_cmd


class CancellableCommandTests(unittest.TestCase):
    def test_success_captures_output(self) -> None:
        result = run_cancellable_cmd(
            [sys.executable, "-c", "print('ready')"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ready")

    def test_cancel_stops_owned_process_promptly(self) -> None:
        stop = threading.Event()
        timer = threading.Timer(0.15, stop.set)
        timer.start()
        started = time.perf_counter()
        try:
            with self.assertRaises(CommandCancelled):
                run_cancellable_cmd(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    timeout=15,
                    cancelled=stop.is_set,
                    poll_seconds=0.05,
                )
        finally:
            timer.cancel()
        self.assertLess(time.perf_counter() - started, 3.0)

    def test_timeout_stops_owned_process(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cancellable_cmd(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=0.15,
                poll_seconds=0.05,
            )


if __name__ == "__main__":
    unittest.main()

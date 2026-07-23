from __future__ import annotations

import subprocess
import sys
import unittest


class ShellNavigationTests(unittest.TestCase):
    def test_real_shell_navigation_contract_in_isolated_tk_process(self) -> None:
        """Tk supports one root lifecycle per process most reliably.

        Run the real-shell contract in isolation so this test cannot leak CTk
        image/DPI callbacks into the Sprite Viewer panel lifecycle test.
        """
        result = subprocess.run(
            [sys.executable, "-m", "benchmarks.check_shell_navigation"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("PASS: grouped shell navigation", result.stdout)


if __name__ == "__main__":
    unittest.main()

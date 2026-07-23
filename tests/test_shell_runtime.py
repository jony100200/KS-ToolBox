from __future__ import annotations

import unittest
from pathlib import Path

from toolbox.shell import restart_command


class ShellRuntimeTests(unittest.TestCase):
    def test_source_restart_uses_the_project_main_file(self) -> None:
        command, cwd = restart_command()
        self.assertEqual(Path(command[-1]).name, "main.py")
        self.assertEqual(cwd.name, "KS-ToolBox")
        self.assertTrue(Path(command[-1]).is_file())


if __name__ == "__main__":
    unittest.main()

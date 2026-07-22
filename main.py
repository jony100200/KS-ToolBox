"""KS ToolBox — one UI, many auto-discovered tools.

Adding a tool: drop a folder in tools/ that exposes `TOOL`. No changes here.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from toolbox.discovery import discover      # noqa: E402
from toolbox.shell import ToolBoxShell       # noqa: E402


def main() -> int:
    ToolBoxShell(discover()).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

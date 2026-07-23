"""KS ToolBox — one UI, many auto-discovered tools.

Adding a tool: drop a folder in tools/ that exposes `TOOL`. No changes here.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _set_stdlib_dll_directory() -> None:
    """Put the active interpreter's DLLs ahead of foreign PATH entries.

    This runs before plugin discovery and before the shell's durable-queue
    imports.  It protects source launches where an old shell-extension
    ``sqlite3.dll`` would otherwise be selected by ``_sqlite3.pyd``.
    """
    if os.name != "nt":
        return
    dll_dir = Path(sys.base_prefix) / "DLLs"
    if not (dll_dir / "sqlite3.dll").is_file():
        return
    if not ctypes.windll.kernel32.SetDllDirectoryW(str(dll_dir)):
        raise ctypes.WinError()


_set_stdlib_dll_directory()

from toolbox.discovery import discover      # noqa: E402
from toolbox.shell import ToolBoxShell       # noqa: E402


def main() -> int:
    ToolBoxShell(discover()).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

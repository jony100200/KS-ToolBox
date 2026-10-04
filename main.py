"""KS ToolBox — one UI, many auto-discovered tools.

Adding a tool: drop a folder in tools/ that exposes `TOOL`. No changes here.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Guarantee stdout/stderr streams even when launched via pythonw.exe (no console)
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = LOGS_DIR / "kstoolbox.log"

if sys.stderr is None:
    try:
        sys.stderr = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    except Exception:
        pass

if sys.stdout is None:
    try:
        sys.stdout = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    except Exception:
        pass

logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    err_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.critical("Uncaught top-level exception:\n%s", err_text)
    if sys.stderr is not None:
        try:
            sys.stderr.write(f"FATAL ERROR:\n{err_text}\n")
            sys.stderr.flush()
        except Exception:
            pass
    try:
        from tkinter import messagebox
        messagebox.showerror(
            "KS ToolBox - Error",
            f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc_value}\n\nCheck logs/kstoolbox.log for details.",
        )
    except Exception:
        pass


sys.excepthook = _handle_uncaught_exception


def _set_stdlib_dll_directory() -> None:
    """Put the active interpreter's DLLs ahead of foreign PATH entries.

    This runs before plugin discovery and before the shell's durable-queue
    imports. It protects source launches where an old shell-extension
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

try:
    import sqlite3  # noqa: F401  # Preload stdlib SQLite extension before plugins/UI modify process DLL state
except Exception:
    pass

from toolbox.discovery import discover      # noqa: E402
from toolbox.shell import ToolBoxShell       # noqa: E402


def main() -> int:
    ToolBoxShell(discover()).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

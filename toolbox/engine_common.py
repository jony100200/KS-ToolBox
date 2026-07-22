"""Shared engine utilities — envelope helpers, binary resolution, subprocess runner.

Extracted from duplicated code across tool engines. Every engine imports these
instead of defining its own copies. Pure refactor, zero behavior change.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# --- file extension sets ------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".wmv",
              ".flv", ".mpg", ".mpeg", ".ts", ".m2ts"}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


# --- error envelope -----------------------------------------------------------

def ok(data: Any = None, degraded: bool = False, details: str = "") -> dict:
    """Successful result envelope."""
    return {"error": False, "error_type": "", "retryable": False,
            "degraded": degraded, "details": details, "data": data}


def err(error_type: str, details: str, retryable: bool = False) -> dict:
    """Failed result envelope."""
    return {"error": True, "error_type": error_type, "retryable": retryable,
            "degraded": False, "details": details, "data": None}


# --- binary resolution (explicit, announced) — no magic PATH assumptions ------

def bundled_bin_dir() -> Path | None:
    """A `bin/` folder shipped with the app (for portable, no-install use).
    Checked before PATH so a bundled ffmpeg wins. Works frozen (PyInstaller)
    and from source."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "bin")
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "bin")
    else:
        # toolbox/engine_common.py → parent.parent = repo root
        candidates.append(Path(__file__).resolve().parent.parent / "bin")
    return next((c for c in candidates if c.is_dir()), None)


def resolve_tool(name: str) -> str | None:
    """Resolve an external binary: bundled bin/ first (portable), then PATH.
    None if absent (caller degrades)."""
    bin_dir = bundled_bin_dir()
    if bin_dir:
        for candidate in (bin_dir / name, bin_dir / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def tools_status(names: list[str]) -> dict[str, str | None]:
    """What's available on this machine. UI/status can show this."""
    return {n: resolve_tool(n) for n in names}


# --- subprocess runner --------------------------------------------------------

def run_cmd(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a child process, capturing output, without a shell (safe on both OSes).
    No console window flashes on Windows."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=(0x08000000 if os.name == "nt" else 0))


# --- crash cleanup ------------------------------------------------------------

def sweep_part_files(root: Path) -> int:
    """Remove stale atomic-write temps under `root` and return how many.

    Every engine writes to a `.part` temp then `os.replace()`s it into place, so
    any `*.part` / `*.part.<ext>` left behind is an incomplete write from a prior
    hard kill — safe to delete. Scoped to an output root the app writes to;
    best-effort (per-file errors are ignored). Same-name temps are already
    self-healed by the next atomic write, so this only clears orphans."""
    root = Path(root)
    if not root.is_dir():
        return 0
    removed = 0
    seen: set[Path] = set()
    for pattern in ("*.part", "*.part.*"):     # video: name.mkv.part · image: name.part.png
        for p in root.rglob(pattern):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed

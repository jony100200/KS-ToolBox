"""Shared engine utilities — envelope helpers, binary resolution, subprocess runner.

Extracted from duplicated code across tool engines. Every engine imports these
instead of defining its own copies. Pure refactor, zero behavior change.
"""
from __future__ import annotations

import hashlib
import os
import signal
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

# --- file extension sets ------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".wmv",
              ".flv", ".mpg", ".mpeg", ".ts", ".m2ts"}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class CommandCancelled(subprocess.SubprocessError):
    """Raised after an owned external command or file operation is cancelled."""

    def __init__(self, cmd: list[str]) -> None:
        super().__init__(f"command cancelled: {Path(cmd[0]).name}")
        self.cmd = cmd


def sha256_file(
    path: str | Path,
    chunk_size: int = 1024 * 1024,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Stream a file into SHA-256 without loading it into memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if cancelled is not None and cancelled():
        raise CommandCancelled(["sha256", str(path)])
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            if cancelled is not None and cancelled():
                raise CommandCancelled(["sha256", str(path)])
            digest.update(chunk)
    return digest.hexdigest()


def find_output_collisions(
    sources: Iterable[str | Path],
    plan_outputs: Callable[[Path], Iterable[str | Path]],
) -> dict[str, tuple[str, ...]]:
    """Find output paths shared by sources or targeting a selected input.

    The planner remains tool-specific; normalization and collision semantics are
    shared. Host filesystem case rules are respected through ``normcase``.
    """
    resolved_sources = [Path(path).expanduser().resolve(strict=False) for path in sources]

    def resolved(path: str | Path) -> Path:
        return Path(path).expanduser().resolve(strict=False)

    def key(path: str | Path) -> str:
        return os.path.normcase(str(resolved(path)))

    source_by_key = {key(source): str(source) for source in resolved_sources}
    owners: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for source in resolved_sources:
        for output in plan_outputs(source):
            output_key = key(output)
            display.setdefault(output_key, str(resolved(output)))
            owners.setdefault(output_key, []).append(str(source))

    collisions: dict[str, tuple[str, ...]] = {}
    for output_key, output_owners in owners.items():
        participants = list(dict.fromkeys(output_owners))
        multiple_writes = len(output_owners) > 1
        if output_key in source_by_key:
            participants.append(f"selected input: {source_by_key[output_key]}")
        if multiple_writes or output_key in source_by_key:
            collisions[display[output_key]] = tuple(participants)
    return collisions


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


def run_cancellable_cmd(
    cmd: list[str],
    *,
    timeout: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    poll_seconds: float = 0.1,
) -> subprocess.CompletedProcess:
    """Run one owned process with captured output and cooperative cancellation.

    This is separate from ``run_cmd`` so existing tools retain their proven
    subprocess path until explicitly migrated. No shell is involved.
    """
    if not cmd:
        raise ValueError("cmd cannot be empty")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    started = time.monotonic()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=(0x08000000 if os.name == "nt" else 0),
        start_new_session=(os.name != "nt"),
    )
    while True:
        if cancelled is not None and cancelled():
            _stop_owned_process(process)
            raise CommandCancelled(cmd)
        if timeout is not None:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                stdout, stderr = _stop_owned_process(process)
                raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
            wait_for = min(poll_seconds, remaining)
        else:
            wait_for = poll_seconds
        try:
            stdout, stderr = process.communicate(timeout=wait_for)
            return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def _stop_owned_process(process: subprocess.Popen) -> tuple[str, str]:
    """Stop the exact process tree started by ``run_cancellable_cmd``."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,
                check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        return process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        return process.communicate()


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

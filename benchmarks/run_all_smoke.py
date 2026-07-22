"""Run every tool's smoke test and summarize — the release gate (AGENTS.md §Verification).

Each tool's test skips cleanly if a dependency is absent, so this is honest on any
machine. For a full run, provide the deps in an isolated overlay, e.g.:

  uv run --no-project --with customtkinter --with pillow --with numpy \
         --with onnxruntime --with vtracer --with markdown \
         python benchmarks/run_all_smoke.py

Exit code is non-zero if any tool's test FAILS (a SKIP is not a failure).
"""
from __future__ import annotations

import importlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolbox.discovery import discover  # noqa: E402


def main() -> int:
    tool_ids = sorted(t.meta.id for t in discover().all())
    print(f"discovered {len(tool_ids)} tools\n")
    failed: list[str] = []
    for tid in tool_ids:
        buf = io.StringIO()
        try:
            mod = importlib.import_module(f"tools.{tid}.test_smoke")
            with redirect_stdout(buf):
                rc = mod.main()
            out = buf.getvalue().strip().splitlines()
            status = "FAIL" if rc else ("SKIP" if any("SKIP" in l for l in out) and not any("PASS" in l for l in out) else "PASS")
            if rc:
                failed.append(tid)
            detail = out[-1] if out else ""
            print(f"  [{status}] {tid:<18} {detail}")
        except Exception as ex:                     # AssertionError or import error = real failure
            failed.append(tid)
            print(f"  [FAIL] {tid:<18} {type(ex).__name__}: {ex}")
    print(f"\n{len(tool_ids) - len(failed)}/{len(tool_ids)} ok" + (f" — FAILED: {failed}" if failed else " — all green"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

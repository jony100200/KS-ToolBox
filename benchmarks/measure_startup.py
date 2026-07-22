"""Startup benchmark — time to a ready-to-mainloop state, headless (no window).

Also asserts nothing heavy (rembg/onnx/numpy/torch) is imported at startup, which
is the whole point of the lazy-loading design. Run:

    .venv\\Scripts\\python.exe benchmarks\\measure_startup.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HEAVY = ("rembg", "onnxruntime", "numpy", "torch", "numba", "scipy", "skimage", "PIL")


def main() -> int:
    t0 = time.perf_counter()
    import customtkinter  # noqa: F401
    t1 = time.perf_counter()
    from toolbox.discovery import discover
    from toolbox.shell import ToolBoxShell  # noqa: F401
    reg = discover()
    t2 = time.perf_counter()

    print(f"customtkinter import          : {(t1 - t0) * 1000:6.0f} ms")
    print(f"discover({len(reg.all())} tools) + shell import : {(t2 - t1) * 1000:6.0f} ms")
    print(f"total to ready-to-mainloop    : {(t2 - t0) * 1000:6.0f} ms")

    loaded = [m for m in HEAVY if m in sys.modules]
    print(f"heavy modules loaded at startup: {loaded or 'none'}")
    assert not loaded, f"lazy-loading regression — heavy import at startup: {loaded}"
    print("OK: nothing heavy loaded at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Verifies the pure sizing math for all four modes, then runs the full pipeline
on a real image and asserts the saved file has the computed dimensions. Needs
only Pillow for the file leg; the math leg needs nothing. Skips cleanly.

Run standalone:  python -m tools.image_rescale.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.image_rescale import engine as e  # noqa: E402


def test_sizing_math() -> None:
    # 2000x1000 source, downscale-only (allow_upscale defaults False).
    o = e.ResizeOptions
    assert e.compute_size("longest_side", 2000, 1000, o(longest_side=1000)) == (1000, 500)
    assert e.compute_size("scale_factor", 2000, 1000, o(scale_factor=0.5)) == (1000, 500)
    assert e.compute_size("fit_inside", 2000, 1000, o(fit_w=800, fit_h=800)) == (800, 400)
    # max_mp: 1 MP target from 2 MP source -> factor ~0.72 -> ~1448x724
    w, h = e.compute_size("max_mp", 2000, 1000, o(max_mp=1.0))
    assert 1400 <= w <= 1500 and 700 <= h <= 760, f"max_mp off: {w}x{h}"
    # upscale gating: never enlarge unless allowed.
    assert e.compute_size("longest_side", 500, 500, o(longest_side=2000)) == (500, 500)
    assert e.compute_size("longest_side", 500, 500, o(longest_side=2000, allow_upscale=True)) == (2000, 2000)
    # snap: round down to a multiple.
    assert e.compute_size("scale_factor", 1000, 1000, o(scale_factor=0.53, snap=64)) == (512, 512)
    print("PASS: image_rescale sizing math — all four modes + upscale gating + snap.")


def test_full_pipeline() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — full-pipeline leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "big.png"
        Image.new("RGB", (1600, 800), (120, 60, 200)).save(src)
        out = tmp / "out"
        res = e.process(src, e.ResizeOptions(out_root=out, mode="longest_side",
                                             longest_side=800, dry_run=False))
        assert res.action == "resized", f"expected resized, got {res.action}: {res.reason}"
        with Image.open(res.out_path) as got:     # close the handle so tempdir cleanup works on Windows
            assert got.size == (800, 400), f"saved size wrong: {got.size}"
    print(f"PASS: image_rescale full pipeline — {res.before} -> {res.after}.")


def main() -> int:
    test_sizing_math()
    test_full_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Makes a real PNG, runs the full pipeline, and asserts it wrote a non-empty
`.svg` that actually contains an `<svg` tag. Needs vtracer (the tracer) and
Pillow (to synthesize the input); skips cleanly if either is absent.

Run standalone:  python -m tools.to_svg.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.to_svg import engine as e  # noqa: E402


def main() -> int:
    if importlib.util.find_spec("vtracer") is None:
        print("SKIP: vtracer not installed — pip install vtracer to run this test.")
        return 0
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — cannot synthesize the input image here.")
        return 0
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Two solid colour blocks — a shape vtracer can actually trace into paths.
        img = Image.new("RGB", (64, 64), (240, 240, 240))
        for y in range(16, 48):
            for x in range(16, 48):
                img.putpixel((x, y), (200, 40, 60))
        src = tmp / "blocks.png"
        img.save(src)

        out = tmp / "out"
        res = e.process(src, e.SvgOptions(out_root=out, colormode="color", dry_run=False))
        assert res.action == "converted", f"expected converted, got {res.action}: {res.reason}"
        dst = Path(res.out_path)
        assert dst.is_file(), "no output written"
        text = dst.read_text(encoding="utf-8")
        size = dst.stat().st_size            # read inside the block — tempdir is gone after
        assert size > 0, "output SVG is empty"
        assert "<svg" in text, "output does not contain an <svg tag"

    print(f"PASS: to_svg — wrote {dst.name}, {size} bytes, contains <svg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

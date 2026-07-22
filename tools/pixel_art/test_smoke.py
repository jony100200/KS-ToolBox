"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Makes a real gradient image, runs the full pipeline, and asserts it wrote a
pixel-art PNG whose palette is actually reduced to <= the requested colours and
whose size matches the upscale choice. Needs only Pillow; skips cleanly without.

Run standalone:  python -m tools.pixel_art.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pixel_art import engine as e  # noqa: E402


def main() -> int:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — cannot run smoke test here.")
        return 0
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # a smooth 64-colour-ish gradient — something to quantize down.
        img = Image.new("RGB", (64, 64))
        img.putdata([(x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256)
                     for y in range(64) for x in range(64)])
        src = tmp / "grad.png"
        img.save(src)

        out = tmp / "out"
        opts = e.PixelOptions(out_root=out, pixel_size=4, num_colors=8,
                              upscale=True, dry_run=False)
        res = e.process(src, opts)
        assert res.action == "converted", f"expected converted, got {res.action}: {res.reason}"
        assert Path(res.out_path).is_file(), "no output written"
        assert e.validate_result(res, opts), "fresh pixel-art output did not pass artifact validation"

        with Image.open(res.out_path) as got:     # close the handle so tempdir cleanup works on Windows
            assert got.size == (64, 64), f"upscale should restore size, got {got.size}"
            colors = got.convert("RGB").getcolors(maxcolors=100000) or []
        assert len(colors) <= 8, f"palette not reduced: {len(colors)} colours > 8"

        Path(res.out_path).write_bytes(b"not a png")
        assert not e.validate_result(res, opts), "corrupt stored output must not be reusable"

    print(f"PASS: pixel_art — wrote {Path(res.out_path).name}, {len(colors)} colours (<= 8).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

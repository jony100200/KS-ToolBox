"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Verifies the pure seam math (a flat/seamless image scores ~1.0; a hard vertical
seam scores LOW on X; offset_wrap is an even-dim identity when applied twice;
tile_preview triples size), then runs the full pipeline on a real image and
asserts the previews were written and a score reported. The math leg needs
numpy; the preview/pipeline legs need Pillow. Skips cleanly without them.

Run standalone:  python -m tools.tileset_checker.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.tileset_checker import engine as e  # noqa: E402


def test_seam_math() -> None:
    import numpy as np

    # Flat colour tiles perfectly — every edge matches its opposite.
    flat = np.full((64, 64, 3), 128, np.uint8)
    s = e.seam_score(flat)
    assert s["overall"] > 0.99, f"flat should be seamless: {s}"

    # A horizontal sine that closes the loop (col 0 ≈ col -1) is X-seamless.
    xs = np.arange(64)
    sine = ((np.sin(2 * np.pi * xs / 64) * 0.5 + 0.5) * 255).astype(np.uint8)
    wrap = np.repeat(sine[None, :], 64, axis=0)[:, :, None].repeat(3, axis=2)
    sw = e.seam_score(wrap)
    assert sw["x"] > 0.9, f"wrapping sine should be X-seamless: {sw}"

    # Hard vertical seam: left half black, right half white -> X breaks, Y clean.
    seam = np.zeros((64, 64, 3), np.uint8); seam[:, 32:] = 255
    s2 = e.seam_score(seam)
    assert s2["x"] < 0.2, f"hard seam should score LOW on X: {s2}"
    assert s2["y"] > 0.99, f"hard seam is Y-seamless: {s2}"
    assert s2["overall"] < 0.2, f"overall governed by worst axis: {s2}"

    # offset_wrap applied twice by half == identity for even dims.
    arr = (np.arange(64 * 64 * 3, dtype=np.uint32) % 256).astype(np.uint8).reshape(64, 64, 3)
    back = e.offset_wrap(e.offset_wrap(arr))
    assert np.array_equal(back, arr), "double half-roll must be identity (even dims)"
    print("PASS: tileset_checker seam math — flat/sine seamless, hard seam LOW on X, "
          "offset_wrap identity.")


def test_previews() -> None:
    from PIL import Image
    img = Image.new("RGB", (20, 10), (10, 20, 30))
    tp = e.tile_preview(img, 3)
    assert tp.size == (60, 30), f"3x3 tile must triple size: {tp.size}"
    import numpy as np
    strip = e.edge_diff_strip(np.zeros((32, 40, 3), np.uint8))
    assert strip.size[0] == 256 and strip.height > 0, f"heatmap strip malformed: {strip.size}"
    print("PASS: tileset_checker previews — tile montage triples size, heatmap strip built.")


def test_full_pipeline() -> None:
    if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("numpy") is None:
        print("SKIP: numpy/Pillow not installed — full-pipeline leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "flat.png"
        Image.new("RGB", (64, 64), (80, 120, 160)).save(src)
        out = tmp / "out"
        res = e.process(src, e.TileOptions(out_root=out, dry_run=False))
        assert res.action == "checked", f"expected checked, got {res.action}: {res.reason}"
        assert res.overall > 0.99, f"flat image should score seamless: {res.overall}"
        names = [Path(p).name for p in res.outputs]
        assert any("_offset" in n for n in names), f"no offset preview written: {names}"
        assert any("_tile" in n for n in names), f"no tile preview written: {names}"
        for p in res.outputs:
            assert Path(p).is_file(), f"preview missing on disk: {p}"
            with Image.open(p) as g:      # close the handle so tempdir cleanup works on Windows
                g.load()
    print(f"PASS: tileset_checker full pipeline — wrote {len(res.outputs)} previews, "
          f"overall={res.overall:.3f}.")


def main() -> int:
    if importlib.util.find_spec("numpy") is None:
        print("SKIP: numpy not installed — tileset_checker tests skipped.")
        return 0
    test_seam_math()
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — preview/pipeline legs skipped.")
        return 0
    test_previews()
    test_full_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

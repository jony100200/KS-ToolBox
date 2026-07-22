"""Smoke test — the release bar for Showcase (AGENTS.md §Verification).

Pure-math checks on the three compositing helpers, then a full round-trip:
build a real contact sheet from a temp folder of PNGs and frame one hero, and
assert non-empty PNGs land on disk. Needs Pillow (importing the package pulls
customtkinter via tool.py); skips cleanly without Pillow.

Run standalone:  python -m tools.showcase.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.showcase import engine as e  # noqa: E402


def _swatch(size, color):
    from PIL import Image
    return Image.new("RGBA", size, color)


def _pure_checks() -> None:
    from PIL import Image

    # contact_sheet: 4 images, 2 cols -> a 2x2 grid of the expected dimensions.
    imgs = [_swatch((40, 40), c) for c in
            [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]]
    labels = ["a", "b", "c", "d"]
    cols, cell, pad = 2, 64, 8
    sheet = e.contact_sheet(imgs, cols=cols, cell=cell, pad=pad, bg=(17, 24, 39), labels=labels)
    label_h = e._LABEL_H
    exp_w = pad + cols * (cell + pad)
    exp_h = pad + 2 * (cell + label_h + pad)   # 2 rows, no title header
    assert sheet.size == (exp_w, exp_h), f"contact sheet size {sheet.size} != expected {(exp_w, exp_h)}"

    # frame_hero: a small asset centered on a larger canvas (output == canvas).
    asset = _swatch((20, 20), (220, 40, 40, 255))
    opts = e.ShowcaseOptions(mode="hero", cell_size=128, padding=16, bg_style="solid",
                             bg_color=(10, 20, 40), shadow=False, watermark=False)
    hero = e.frame_hero(asset, opts)
    assert hero.size == (128, 128), f"hero canvas {hero.size} != (128, 128)"
    center = hero.convert("RGB").getpixel((64, 64))
    assert center[0] > 150 and center[1] < 90 and center[2] < 90, \
        f"asset not centered/red at canvas centre: {center}"

    # before_after: width is ~2 panels + a divider.
    a, b = _swatch((40, 30), (200, 60, 60, 255)), _swatch((30, 40), (60, 60, 200, 255))
    ba = e.before_after(a, b, labels=("Before", "After"), cell=64, pad=10)
    ta = e.thumbnail_fit(a.convert("RGBA"), (64, 64))
    tb = e.thumbnail_fit(b.convert("RGBA"), (64, 64))
    assert ba.width >= ta.width + tb.width + 4, \
        f"before/after width {ba.width} not ~2x + divider ({ta.width}+{tb.width}+divider)"
    assert ba.height > 0

    print(f"PASS(pure): contact {sheet.size}, hero {hero.size}, before/after {ba.size}")


def _full_checks() -> None:
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_dir = tmp / "in"; src_dir.mkdir()
        srcs = []
        for i, color in enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]):
            p = src_dir / f"img{i}.png"
            Image.new("RGB", (48, 48), color).save(p)
            srcs.append(p)

        out = tmp / "out"

        # contact sheet, real write
        res = e.build_contact_sheet(srcs, e.ShowcaseOptions(mode="contact", cols=2, cell_size=80,
                                                            padding=10, out_root=out, dry_run=False))
        assert res.action == "rendered", f"contact expected rendered, got {res.action}: {res.reason}"
        cs = Path(res.out_path)
        assert cs.is_file() and cs.stat().st_size > 0, "contact sheet not written / empty"
        with Image.open(cs) as got:
            assert got.size[0] > 0 and got.size[1] > 0, "contact sheet has no pixels"

        # hero, real write on one file
        hres = e.process(srcs[0], e.ShowcaseOptions(mode="hero", cell_size=160, padding=16,
                                                    bg_style="gradient", bg_color=(20, 30, 60),
                                                    watermark=True, out_root=out, dry_run=False))
        assert hres.action == "rendered", f"hero expected rendered, got {hres.action}: {hres.reason}"
        hp = Path(hres.out_path)
        assert hp.is_file() and hp.stat().st_size > 0, "hero PNG not written / empty"
        with Image.open(hp) as got:
            assert got.size == (160, 160), f"hero PNG size {got.size} != (160, 160)"

    print(f"PASS(full): wrote {cs.name} and {hp.name}")


def main() -> int:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — cannot run smoke test here.")
        return 0
    _pure_checks()
    _full_checks()
    print("PASS: showcase — all checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

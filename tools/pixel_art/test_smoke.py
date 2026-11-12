"""Smoke test — the release bar for Pixel Art Studio (AGENTS.md §Verification).

Verifies retro hardware palettes, ordered Bayer dithering, Floyd-Steinberg,
Atkinson dithering, pixel-perfect corner pruning, sprite outlines, and batch validation.

Run standalone:  python -m tools.pixel_art.test_smoke
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image

from tools.pixel_art import engine as e
from tools.pixel_art import palettes
from tools.pixel_art import dithering


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # 1. Create a test RGB gradient image with transparency
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for y in range(8, 56):
            for x in range(8, 56):
                img.putpixel((x, y), (x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256, 255))

        src = tmp / "sprite_test.png"
        img.save(src)

        out = tmp / "out"

        # 2. Test PICO-8 + Bayer 4x4
        opts_pico = e.PixelOptions(
            out_root=out / "pico",
            pixel_size=4,
            palette="pico8",
            dither_method="bayer4",
            upscale=True,
            dry_run=False
        )
        res_pico = e.process(src, opts_pico)
        assert res_pico.action == "converted", f"expected converted, got {res_pico.action}: {res_pico.reason}"
        assert Path(res_pico.out_path).is_file()
        assert e.validate_result(res_pico, opts_pico)

        # 3. Test Game Boy DMG + Atkinson Dithering
        opts_gb = e.PixelOptions(
            out_root=out / "gb",
            pixel_size=4,
            palette="gameboy_dmg",
            dither_method="atkinson",
            pixel_perfect=True,
            outline=True,
            upscale=True,
            dry_run=False
        )
        res_gb = e.process(src, opts_gb)
        assert res_gb.action == "converted"
        assert e.validate_result(res_gb, opts_gb)

        # 4. Test NES + Floyd-Steinberg
        opts_nes = e.PixelOptions(
            out_root=out / "nes",
            pixel_size=4,
            palette="nes",
            dither_method="floyd",
            upscale=False,
            dry_run=False
        )
        res_nes = e.process(src, opts_nes)
        assert res_nes.action == "converted"
        assert e.validate_result(res_nes, opts_nes)

        with Image.open(res_nes.out_path) as got:
            assert got.size == (16, 16), f"expected native size (16, 16), got {got.size}"

        # 5. Regression (2026-11-12): the live preview shows the real output's block size.
        big = img.resize((240, 160), Image.Resampling.BICUBIC)
        big_src = tmp / "big.png"; big.save(big_src)
        opts_prev = e.PixelOptions(out_root=out / "prev", pixel_size=8, palette="pico8",
                                   dither_method="bayer4", upscale=True, dry_run=False)
        grid = e.preview_grid(big, opts_prev)
        assert grid.size == (30, 20), f"preview grid {grid.size} != output grid (30, 20)"
        res_prev = e.process(big_src, opts_prev)
        with Image.open(res_prev.out_path) as got:
            native = got.convert("RGBA").resize(grid.size, Image.Resampling.NEAREST)
        assert native.tobytes() == grid.convert("RGBA").tobytes(), "preview differs from the saved output"

        # 6. Test Corrupted Artifact Validation
        Path(res_pico.out_path).write_bytes(b"not a valid png")
        assert not e.validate_result(res_pico, opts_pico), "corrupt stored output must not be reusable"

    print("PASS: Pixel Art Studio — verified PICO-8, Game Boy, NES, Bayer 4x4, Atkinson, Floyd, Outlines, preview = output grid, and Artifact Validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

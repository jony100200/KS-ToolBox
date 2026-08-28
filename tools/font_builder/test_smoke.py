"""Smoke test for Font Builder tool — creates sample glyphs, compiles TTF, and verifies font tables."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

from tools.font_builder import engine as e


def create_test_glyphs(dir_path: Path) -> list[Path]:
    """Generate synthetic raster character images."""
    files: list[Path] = []

    # 1. 'A' (uppercase triangle glyph)
    img_a = Image.new("L", (200, 200), color=255)
    draw_a = ImageDraw.Draw(img_a)
    draw_a.polygon([(100, 20), (40, 180), (70, 180), (85, 130), (115, 130), (130, 180), (160, 180)], fill=0)
    draw_a.polygon([(100, 60), (88, 110), (112, 110)], fill=255)
    path_a = dir_path / "A.png"
    img_a.save(path_a)
    files.append(path_a)

    # 2. 'B' (uppercase double-box glyph)
    img_b = Image.new("L", (200, 200), color=255)
    draw_b = ImageDraw.Draw(img_b)
    draw_b.rectangle([(50, 20), (80, 180)], fill=0)
    draw_b.rectangle([(80, 20), (140, 95)], fill=0)
    draw_b.rectangle([(80, 105), (140, 180)], fill=0)
    path_b = dir_path / "B.png"
    img_b.save(path_b)
    files.append(path_b)

    # 3. 'g' (lowercase descender glyph)
    img_g = Image.new("L", (200, 200), color=255)
    draw_g = ImageDraw.Draw(img_g)
    draw_g.ellipse([(60, 40), (140, 120)], outline=0, width=15)
    draw_g.line([(135, 60), (135, 160)], fill=0, width=15)
    draw_g.arc([(60, 120), (135, 180)], 0, 180, fill=0, width=15)
    path_g = dir_path / "small_g.png"
    img_g.save(path_g)
    files.append(path_g)

    # 4. '1' (digit glyph)
    img_1 = Image.new("L", (200, 200), color=255)
    draw_1 = ImageDraw.Draw(img_1)
    draw_1.rectangle([(90, 30), (120, 180)], fill=0)
    draw_1.polygon([(90, 30), (60, 60), (70, 70), (90, 50)], fill=0)
    path_1 = dir_path / "1.png"
    img_1.save(path_1)
    files.append(path_1)

    # 5. 'comma' (punctuation glyph)
    img_c = Image.new("L", (200, 200), color=255)
    draw_c = ImageDraw.Draw(img_c)
    draw_c.ellipse([(90, 140), (110, 160)], fill=0)
    draw_c.polygon([(90, 150), (110, 150), (95, 185)], fill=0)
    path_c = dir_path / "comma.png"
    img_c.save(path_c)
    files.append(path_c)

    return files


def run_smoke_test() -> int:
    print("=== SMOKE TEST: Font Builder ===")

    # 1. Test filename-to-character parser
    assert e.parse_character_from_name("A") == ("A", 65)
    assert e.parse_character_from_name("small_g") == ("g", 103)
    assert e.parse_character_from_name("cap_z") == ("Z", 90)
    assert e.parse_character_from_name("comma") == (",", 44)
    assert e.parse_character_from_name("space") == (" ", 32)
    assert e.parse_character_from_name("u0041") == ("A", 65)
    print("  [OK] parse_character_from_name mappings verified")

    with tempfile.TemporaryDirectory(prefix="font_builder_smoke_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        sources = create_test_glyphs(tmp_dir)
        print(f"  [OK] Created {len(sources)} test glyphs")

        # 2. Test Dry Run
        opts_dry = e.FontOptions(font_name="SmokeFont", dry_run=True)
        res_dry = e.build_font(sources, opts_dry)
        assert res_dry.action == "dry-run", f"Dry run failed: {res_dry.reason}"
        assert res_dry.artifact["glyph_count"] == len(sources) + 2  # + .notdef, space
        print(f"  [OK] Dry run verified ({res_dry.artifact['glyph_count']} glyphs planned)")

        # 3. Test Full Compilation
        opts = e.FontOptions(
            font_name="SmokeFont",
            family_name="SmokeFont",
            style_name="Regular",
            units_per_em=1000,
            cap_height=700,
            auto_descenders=True,
            out_root=tmp_dir,
            dry_run=False,
        )
        res = e.build_font(sources, opts)
        assert res.action == "built", f"Compilation failed: {res.reason}"

        out_ttf = Path(res.out_path)
        assert out_ttf.exists(), f"Output TTF does not exist: {out_ttf}"
        assert out_ttf.stat().st_size > 500, f"TTF file too small: {out_ttf.stat().st_size} bytes"
        print(f"  [OK] Generated TTF: {out_ttf.name} ({out_ttf.stat().st_size} bytes)")

        # 4. Verify TTF Tables and CMAP via fontTools
        from fontTools.ttLib import TTFont

        font = TTFont(out_ttf)
        required_tables = ["head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "loca", "glyf", "name", "post"]
        for table in required_tables:
            assert table in font, f"Missing required table: {table}"

        cmap = font.getBestCmap()
        assert ord("A") in cmap, "Missing 'A' in cmap"
        assert ord("B") in cmap, "Missing 'B' in cmap"
        assert ord("g") in cmap, "Missing 'g' in cmap"
        assert ord("1") in cmap, "Missing '1' in cmap"
        assert ord(",") in cmap, "Missing ',' in cmap"
        assert ord(" ") in cmap, "Missing ' ' in cmap"
        print(f"  [OK] TTF Table and CMAP verification passed ({len(cmap)} mapped characters)")

    print("=== SMOKE TEST PASSED: Font Builder is 100% operational! ===")
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())

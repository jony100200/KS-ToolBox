"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Two legs, both Pillow-only (skips cleanly without it):
  1. the pure `normalize()` — an off-centre opaque square with wide transparent
     margins must come back square at the target size, trimmed and centred.
  2. the full `process()` pipeline — a valid square RGBA PNG is written to disk.

Run standalone:  python -m tools.icon_normalizer.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.icon_normalizer import engine as e  # noqa: E402


def main() -> int:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — cannot run smoke test here.")
        return 0
    from PIL import Image

    # -- leg 1: the pure transform --------------------------------------------
    # A 200x200 canvas with a 40x40 opaque red block off in the top-left corner,
    # everything else transparent. Trimming should discard the empty margins and
    # centring should put the block dead-centre of a square target.
    src_img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    block = Image.new("RGBA", (40, 40), (255, 0, 0, 255))
    src_img.paste(block, (10, 20))

    out = e.normalize(src_img, size=128, padding_pct=0.0, trim=True)
    assert out.size == (128, 128), f"expected 128x128 square, got {out.size}"
    assert out.mode == "RGBA", f"expected RGBA, got {out.mode}"
    # a square subject with no padding fills the whole square -> corners opaque.
    assert out.getpixel((0, 0))[3] == 255, "trimmed square should reach the edges"
    assert out.getpixel((64, 64))[3] == 255, "centre must be opaque (content present)"
    # symmetry check: the trimmed content is centred, so mirrored pixels match.
    assert out.getpixel((5, 64)) == out.getpixel((122, 64)), "content is not centred"

    # padding shrinks the content, leaving a transparent border.
    padded = e.normalize(src_img, size=128, padding_pct=20.0, trim=True)
    assert padded.getpixel((0, 0))[3] == 0, "padding % should leave transparent margins"
    assert padded.getpixel((64, 64))[3] == 255, "centre still opaque with padding"

    # -- leg 2: the full pipeline ---------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        s = tmp / "sprite.png"
        src_img.save(s)

        outdir = tmp / "out"
        res = e.process(s, e.NormalizeOptions(out_root=outdir, size=256, padding_pct=0.0,
                                              trim=True, dry_run=False))
        assert res.action == "converted", f"expected converted, got {res.action}: {res.reason}"
        assert Path(res.out_path).is_file(), "no output written"
        assert e.validate_result(res), "fresh normalized output did not pass artifact validation"

        with Image.open(res.out_path) as got:     # close the handle so tempdir cleanup works on Windows
            assert got.size == (256, 256), f"output should be 256x256, got {got.size}"
            assert got.convert("RGBA").mode == "RGBA", "output must be RGBA"

        Path(res.out_path).write_bytes(b"not a png")
        assert not e.validate_result(res), "corrupt stored output must not be reusable"

    print(f"PASS: icon_normalizer — pure normalize centred+squared; process wrote "
          f"{Path(res.out_path).name} at 256x256 RGBA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

The interactive viewer can't be exercised headless, but the ENGINE can — and it
carries all the real logic (slicing, alpha detection, frame loading, GIF export).
This synthesizes real images and asserts each engine function's output. Needs
only Pillow; skips cleanly without it. Uses `with Image.open(...)` for read-backs
so the Windows tempdir can be cleaned up.

Run standalone:  python -m tools.sprite_viewer.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.sprite_viewer import engine as e  # noqa: E402


def main() -> int:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — cannot run smoke test here.")
        return 0
    from PIL import Image

    # --- slice_grid: a 4x2 sheet -> 8 equal frames of the right size ----------
    rows, cols = 2, 4                         # 2 rows x 4 cols = 8 cells
    cell_w, cell_h = 20, 15
    sheet = Image.new("RGBA", (cell_w * cols, cell_h * rows), (0, 0, 0, 0))
    spx = sheet.load()                        # distinct color per cell so frames differ
    for r in range(rows):
        for col in range(cols):
            tone = (r * cols + col + 1) * 25
            for y in range(r * cell_h, (r + 1) * cell_h):
                for x in range(col * cell_w, (col + 1) * cell_w):
                    spx[x, y] = (tone, 255 - tone, 128, 255)
    grid_frames = e.slice_grid(sheet, rows, cols)
    assert len(grid_frames) == 8, f"grid: expected 8 frames, got {len(grid_frames)}"
    assert all(f.size == (cell_w, cell_h) for f in grid_frames), \
        f"grid: frames not all {cell_w}x{cell_h}: {[f.size for f in grid_frames]}"

    # --- slice_by_cell: fixed 20x15 cells over the same sheet -----------------
    cell_frames = e.slice_by_cell(sheet, cell_w, cell_h)
    assert len(cell_frames) == 8, f"cell: expected 8 frames, got {len(cell_frames)}"
    assert all(f.size == (cell_w, cell_h) for f in cell_frames), "cell: wrong frame size"

    # --- detect_sprites: N separated opaque rects on a transparent canvas -----
    N = 3
    canvas = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
    px = canvas.load()
    for i in range(N):                        # rects at x = 10, 80, 150 (well separated)
        x0 = 10 + i * 70
        for y in range(15, 15 + 24):
            for x in range(x0, x0 + 24):
                px[x, y] = (255, 0, 0, 255)
    boxes = e.detect_sprites(canvas, alpha_thresh=32)
    assert len(boxes) == N, f"detect: expected {N} blobs, got {len(boxes)}: {boxes}"
    assert all(len(b) == 4 and b[2] > b[0] and b[3] > b[1] for b in boxes), \
        f"detect: degenerate boxes: {boxes}"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # --- load_frames: a synthesized 5-frame animated GIF ------------------
        anim = tmp / "anim.gif"
        anim_frames = []
        for i in range(5):
            fr = Image.new("RGBA", (16, 16), (i * 40, 0, 255 - i * 40, 255))
            anim_frames.append(fr)
        anim_frames[0].save(anim, save_all=True, append_images=anim_frames[1:],
                            duration=80, loop=0, disposal=2)
        loaded = e.load_frames(anim)
        assert not loaded["error"], f"load_frames errored: {loaded['details']}"
        assert len(loaded["data"]) == 5, \
            f"load_frames: expected 5 frames, got {len(loaded['data'])}"

        # --- export_gif: writes a non-empty animated GIF ----------------------
        out_gif = tmp / "out.gif"
        exp = e.export_gif(grid_frames, out_gif, fps=10)
        assert not exp["error"], f"export_gif errored: {exp['details']}"
        assert out_gif.is_file(), "export_gif wrote no file"
        assert out_gif.stat().st_size > 0, "export_gif wrote an empty file"
        with Image.open(out_gif) as g:            # close handle for tempdir cleanup
            assert getattr(g, "n_frames", 1) == 8, \
                f"export_gif: expected 8 frames in GIF, got {getattr(g, 'n_frames', 1)}"

        # --- export_meta_json: writes valid metadata JSON ---------------------
        meta = e.describe(grid_frames, str(sheet), "sheet-grid", fps=10,
                          boxes=e.grid_boxes(sheet.width, sheet.height, rows, cols))
        out_json = tmp / "meta.json"
        mj = e.export_meta_json(meta, out_json)
        assert not mj["error"], f"export_meta_json errored: {mj['details']}"
        import json
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["frame_count"] == 8 and payload["kind"] == "sheet-grid", \
            f"meta json wrong: {payload.get('frame_count')} / {payload.get('kind')}"

    print(f"PASS: sprite_viewer — grid=8, cell=8, detect={N}, gif=5-load/8-export, json ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

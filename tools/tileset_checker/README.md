# Tileset Checker

Judge and visualize how seamlessly a texture tiles — **deterministic, no AI, no
network**. For each texture it computes a seam-continuity score and writes the
classic seam-test previews. This is the free/offline answer to paid "tileset"
tools: they *generate* seamless textures; this one *checks and previews* the
ones you already have (or the ones a generator produced).

## What it does

| Output | Meaning |
|---|---|
| **Seam score** | Compares opposite wrap edges — left↔right (X) and top↔bottom (Y) — with mean-abs-diff + RMSE, normalized `0..1` (1 = perfectly seamless). Reported per-axis and overall (overall = the worse axis). |
| **Wrap-offset preview** | Rolls the image by half in X and Y so any seam lands in the centre where it's obvious — the classic "offset filter" seam test. |
| **Tile montage** | An N×N (default 3×3) tiled grid to eyeball repetition and seam lines. |
| **Edge-diff heatmap** | A compact two-bar strip: the X-seam and Y-seam discontinuity as a heatmap (bright = large break). |

Previews are written next to an aggregate `seam_scores.csv` + `seam_scores.json`.
Everything is deterministic (same input → same score and pixels) and
non-destructive: it reads your sources and only writes previews + the report.

Runs use the shared durable queue with per-texture checkpoints, pause/resume,
cancellation at item boundaries, failure quarantine, and completion reports.
Stored previews are reused only when their exact SHA-256, PNG format, RGB mode,
expected dimensions, selected artifact set, and score invariants still match.
Flat output-name collisions—including an output that would overwrite a selected
input—are blocked before a real run; enable mirroring, choose another output
folder, or rename the conflicting sources.

## Options

| Option | Meaning | Default |
|---|---|---|
| Tile grid (N×N) | montage repeats: 2 · 3 · 4 | `3` |
| Wrap-offset / Tile montage / Edge-diff heatmap | which previews to write | all on |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | score every texture and list what *would* be written, without writing | on |
| Output folder | where previews + the score report go (blank = `./tileset_check` beside each source) | — |

Reading the score: overall **≥ 0.85** reads as seamless, **0.6–0.85** borderline,
**< 0.6** a visible seam. A perfectly wrapping texture scores ~1.0; a hard edge
(e.g. left half dark, right half light) scores near 0 on that axis.

## Dependencies

- **numpy** and **Pillow** (`pip install numpy pillow`). Both load lazily — the
  tool appears in the sidebar even before they're installed, and announces the
  missing dep when opened.

## Verify

```
python -m tools.tileset_checker.test_smoke
```

Checks the pure seam math (flat/wrapping-sine textures score ~1.0; a hard
vertical seam scores LOW on X; `offset_wrap` twice is an even-dim identity;
`tile_preview` triples size), then runs the full pipeline on a real image and
asserts the previews were written and a score reported.

## Credits

Seam math and previews lifted from RupayanFlow's `seamless/tile_tools.py`
(`offset_wrap`, `tile_preview`, `seam_score`) and ChobiEngine's
`seamless_checker/tile_metrics.py` (per-axis edge-diff / RMSE), keeping the pure
numpy + Pillow path and dropping the cv2 + job-wrapper layers.

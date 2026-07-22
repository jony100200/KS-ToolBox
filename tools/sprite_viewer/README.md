# Sprite Viewer

Load a sprite sheet, an animated GIF/WebP/APNG, or a folder of numbered frames
and **view / play** them — step, scrub, adjust FPS, slice a grid, toggle an alpha
checkerboard. Deterministic, offline, **no AI, no network**. This is the free /
offline answer to paid "sprite analyzer" tools.

## What it does

Load ONE source and page through its frames:

- **Sheet grid** — set **rows × cols**; the sheet is sliced into equal frames.
- **Cell size** — set **cell width × height**; the sheet is tiled into fixed cells.
- **Auto-detect** — find discrete sprites on a transparent sheet by **alpha
  connected-components** (threshold → dilate → flood-fill → reading-order sort).
- **Animation / Folder** — an animated GIF/WebP/APNG loads its frames directly
  (Pillow `n_frames`/`seek`); a folder loads its images in natural-sorted order;
  a still image loads as a single frame.

Then **play** at an adjustable FPS, **step** prev/next, **scrub** the timeline
slider, watch the **frame index / count**, toggle an **alpha checkerboard**
background, and toggle a **slice-grid overlay** (draws the cell/sprite boxes on
the full sheet and highlights the current frame).

## Export (optional)

- **Export GIF** — the loaded frames as an animated GIF preview at the current FPS.
- **Export slice JSON** — the slice metadata (kind, frame count, frame size, FPS,
  and the per-frame boxes) as JSON.

Nothing is written unless you press an Export button.

## Modes

| Mode | Parameters | Source |
|---|---|---|
| Animation / Folder | — | file (GIF/WebP/APNG/still) or folder |
| Sheet grid | Rows, Cols | one image file |
| Cell size | Cell W, Cell H | one image file |
| Auto-detect | Alpha threshold (0–255) | one image file |

## Dependencies

- **Pillow** (`pip install pillow`). No numpy, no GPU, no network.

## Verify

```
python -m tools.sprite_viewer.test_smoke
```

Synthesizes real images and asserts: `slice_grid` of a 4×2 sheet → 8 equal
frames; `slice_by_cell` tiles correctly; `detect_sprites` finds N separated
blobs; `load_frames` reads all frames of a multi-frame GIF; `export_gif` writes a
non-empty animated GIF; `export_meta_json` writes valid metadata.

## Credits

Slicing and alpha connected-component detection lifted from KS ChobiEngine's
`sprite_processor` services (`autosprite_service.split_sprite_sheet` cell math;
`splitter_service.find_sprite_boxes` alpha BFS) — the job-shell wrappers were
dropped, numpy was dropped (pure Pillow), and the nested-Python dilation was
replaced with a C-speed `ImageFilter.MaxFilter`.

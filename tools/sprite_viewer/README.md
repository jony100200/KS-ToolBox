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
the full sheet and highlights the current frame). Decode, slicing, alpha
detection, and export run on a cancellable worker; the custom viewer remains
responsive while those operations run.

## Export (optional)

- **Export GIF** — the loaded frames as an animated GIF preview at the current FPS.
- **Export slice JSON** — the slice metadata (kind, frame count, frame size, FPS,
  and the per-frame boxes) as JSON.

Nothing is written unless you press an Export button. Existing destinations
require confirmation. Both formats are written to a unique same-folder stage,
read back and validated, then atomically published; failure or cancellation
removes the stage and preserves an earlier destination.

## Resource and failure boundaries

- At most 10,000 resident frames or 128 million decoded RGBA pixels.
- Frame-folder discovery stops at 100,000 directory entries.
- Grid and cell requests are rejected before iteration when they exceed the
  frame budget or image dimensions.
- Folder/animation decoding, alpha detection, cropping, GIF encoding, and
  validation observe cancellation at deterministic boundaries.
- The checkerboard cache retains at most eight display-sized images.
- A failed replacement load leaves the currently displayed source intact.

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
frames; invalid grids are bounded; `slice_by_cell` tiles correctly;
`detect_sprites` finds N separated blobs; `load_frames` reads a multi-frame GIF;
`export_gif` writes a decode-valid animated GIF without staging residue;
`export_meta_json` writes parse-valid metadata.

## Credits

Slicing and alpha connected-component detection lifted from KS ChobiEngine's
`sprite_processor` services (`autosprite_service.split_sprite_sheet` cell math;
`splitter_service.find_sprite_boxes` alpha BFS) — the job-shell wrappers were
dropped, numpy was dropped (pure Pillow), and the nested-Python dilation was
replaced with a C-speed `ImageFilter.MaxFilter`.

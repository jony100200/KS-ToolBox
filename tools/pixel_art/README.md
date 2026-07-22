# Pixel Art Converter

Turn ordinary images into clean **pixel art** — chunky pixels, a reduced colour
palette, sharp alpha edges. Batch-capable and mirrors the input folder structure.

## What it does

1. **Pixelize** — downscale by the pixel size with NEAREST sampling so blocks are crisp.
2. **Palettize** — reduce to N colours (median-cut), optionally Floyd-Steinberg dithered.
3. **Hard alpha** — transparency is keyed 0/255 so edges don't smear.
4. **Upscale** (optional) — scale back to the original size so the pixels are big and visible.

Saves RGBA PNGs. Nothing is written until you turn off **Preview only**; originals
are never touched. A `pixel_manifest.csv` records every file when an output folder is set.
Jobs use the shared durable queue, can pause or cancel at item boundaries, and
reuse only outputs that pass PNG, RGBA, dimension, palette, and hard-alpha checks.

## Options

| Option | Meaning | Default |
|---|---|---|
| Pixel size | block size — bigger = chunkier | `4` |
| Colors | palette size | `16` |
| Dither | Floyd-Steinberg when reducing colours | on |
| Upscale to original size | big visible pixels vs the tiny native result | on |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | list planned outputs without writing | on |

## Dependencies

- **Pillow** (`pip install pillow`). No numpy, no GPU.

## Verify

```
python -m tools.pixel_art.test_smoke
```

Generates a gradient, converts it, and asserts the palette really dropped to the
requested colour count and the size matches the upscale choice.

## Credits

Algorithm lifted from ChobiEngine's true_pixel_converter, with per-image failures
surfaced as errors instead of being silently logged and skipped.

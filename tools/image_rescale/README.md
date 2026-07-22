# Image Rescale

Batch-resize images by one of four sizing modes, with sensible defaults (never
upscales unless you ask, sharpest resampling picked automatically). Mirrors the
input folder structure into the output.

## Modes

| Mode | Meaning |
|---|---|
| `longest_side` | scale the longest edge to N pixels |
| `max_mp` | scale to a target megapixel count (1 MP = 1024×1024) |
| `scale_factor` | multiply both dimensions by a factor |
| `fit_inside` | scale to fit within a W×H box (aspect preserved) |

## Options

| Option | Meaning | Default |
|---|---|---|
| Resample | `auto` (Lanczos down / bilinear up) · lanczos · bicubic · bilinear · nearest | `auto` |
| Snap to multiple of | round dimensions down to a multiple (0 = off) — handy for model-friendly sizes | `0` |
| Allow upscaling | permit enlarging (off avoids blurry blow-ups) | off |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | list planned resizes without writing | on |

Output keeps the source format; a JPEG target is flattened to RGB automatically
(JPEG can't store alpha). A `resize_manifest.csv` records every file when an
output folder is set.

## Dependencies

- **Pillow** (`pip install pillow`).

## Verify

```
python -m tools.image_rescale.test_smoke
```

Checks the sizing math for all four modes (plus upscale-gating and snap), then
resizes a real image and asserts the saved dimensions match.

## Credits

Sizing math distilled from RupayanFlow's multi-mode resize engine, keeping the
four modes a batch resizer needs.

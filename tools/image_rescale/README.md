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
| Allow upscaling | permit deterministic enlarging (off avoids blurry blow-ups) | off |
| AI upscale (Real-ESRGAN) | run the local NCNN 4× utility model; automatically enables upscaling | off |
| AI model | `realesrgan-x4plus` for photographs/general assets, or the smaller anime/art model | `realesrgan-x4plus` |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | list planned resizes without writing | off |

Output keeps the source format; a JPEG target is flattened to RGB automatically
(JPEG can't store alpha). A `resize_manifest.csv` records every file when an
output folder is set.

## AI upscaling

When **AI upscale** is selected, this tool invokes the portable Real-ESRGAN
NCNN runtime, not Pillow. It runs only for an actual enlargement and writes the
requested final dimensions; non-native requested scales use the next native
2×/3×/4× model output followed by one final Lanczos resize. The UI shows whether
the local bundle is ready and fails the job clearly if it is not.

The Windows bundle is stored locally under `models/realesrgan-ncnn-20220424/`
(ignored by Git), including the general 4× model (~32 MiB) and compact anime/art
4× model (~9 MiB). It is lazy: it does not add PyTorch, load at application
startup, or leave a model process running after an item completes.

Chobi's optional bounding-box face/region refinement is a distinct enhancement
pipeline, not a prerequisite for normal image super-resolution. It should only
be added as a separate explicit feature with its own face-restoration runtime.

## Dependencies

- **Pillow** (`pip install pillow`).
- Optional local AI runtime: bundled/cacheable Real-ESRGAN NCNN executable and
  models (no Python AI package required).

## Verify

```
python -m tools.image_rescale.test_smoke
```

Checks the sizing math for all four modes (plus upscale-gating and snap), then
resizes a real image and asserts the saved dimensions match.

## Credits

Sizing math distilled from RupayanFlow's multi-mode resize engine, keeping the
four modes a batch resizer needs.

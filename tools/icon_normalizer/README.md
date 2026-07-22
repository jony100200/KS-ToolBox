# Icon / Sprite Normalizer

Make a whole batch of icons or sprites one **uniform, centred, square size**.
Each image is trimmed to its opaque bounds, scaled to fit — aspect preserved —
onto a transparent square canvas, and centred. Output is always RGBA PNG. This
is the daily "make all my icons the same clean 256×256" batch tool.

## What it does

1. **Trim** (optional) — crop away transparent borders down to the opaque bounds.
2. **Fit** — scale the subject to fit the canvas, keeping its aspect ratio.
3. **Pad** (optional) — leave an even transparent margin around the content.
4. **Centre** — place the result dead-centre of a square `size` × `size` canvas.

Saves RGBA PNGs. Nothing is written until you turn off **Preview only**; originals
are never touched. An `icon_manifest.csv` records every file when an output folder
is set. Fully-transparent images are reported as **skipped** (nothing to centre).

## Options

| Option | Meaning | Default |
|---|---|---|
| Icon size (px) | target square canvas — 64 / 128 / 256 / 512 | `256` |
| Padding % | transparent margin as a % of the canvas (0 = fill to edge) | `0` |
| Trim transparent margins | crop to the opaque bounding box first | on |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | list planned outputs without writing | on |

## Dependencies

- **Pillow** (`pip install pillow`). No numpy, no GPU.

## Verify

```
python -m tools.icon_normalizer.test_smoke
```

Builds an off-centre opaque block on a transparent field, asserts the pure
`normalize()` returns a square, trimmed, centred image at the target size, then
runs the full `process()` pipeline and asserts a valid square RGBA PNG is written.

## Credits

Normalization algorithm lifted from RupayanFlow's `asset2d.icons.core.fit_icon`,
reworked to drop the numpy dependency (Pillow's alpha `getbbox` + `resize` do the
same work), generalise the fixed pixel margin to a size-independent `padding_pct`,
add an atomic `.part` write, and surface per-image failures as errors instead of
returning a silent empty canvas.

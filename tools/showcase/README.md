# Showcase

Turn a folder of images or sprites into clean **presentation renders** — the kind
of thing you drop into a portfolio, an asset-store listing, or a "look what I
made" post. Fully **deterministic**: pure Pillow compositing, **no AI, no
network**. Same inputs → same pixels, every time.

## Modes

1. **Contact sheet** — a grid of every input as a thumbnail on a clean
   background, with optional filename labels and a title header. Configurable
   columns, cell size and padding. One aggregate PNG.
2. **Framed hero** (per image) — center each asset on a backdrop (solid colour,
   vertical gradient, transparency checkerboard, or none) with margin, an
   optional drop shadow, an optional caption, and an optional
   "Made with KS ToolBox" watermark. One PNG per input.
3. **Before / After** (per pair) — pair each input with its same-name counterpart
   in a **second folder**, placed side by side with a divider and Before/After
   labels. One PNG per pair.

All modes preserve aspect ratio with Lanczos and **never upscale an asset past
2x**. RGBA assets composite over the chosen backdrop for opaque outputs, or keep
their transparency for the checkerboard / none backdrop. Outputs are PNG; hero and
before/after mirror the input folder structure when **Mirror input structure** is
on, and the contact sheet is a single `contact_sheet.png`.

## Options

| Option | Applies to | Meaning | Default |
|---|---|---|---|
| Mode | all | contact / hero / before_after | `contact` |
| Cell size | all | thumbnail box (contact), canvas square (hero), panel box (before/after) | `256` |
| Padding | all | gutter / margin in px | `16` |
| Columns | contact | grid columns | `4` |
| Filename labels | contact | caption each thumbnail with its filename | on |
| Title header | contact | optional text across the top | (blank) |
| Backdrop | hero | solid / gradient / checker / none | `solid` |
| Backdrop colour | hero | hex, e.g. `#111827` (gradient darkens toward the bottom) | `#111827` |
| Caption | hero | optional text under the asset | (blank) |
| Drop shadow | hero | soft shadow behind the asset | on |
| Watermark | hero | small "Made with KS ToolBox" corner mark | on |
| Second folder | before_after | folder of counterparts matched by filename | (required) |
| Mirror input structure | hero / before_after | rebuild the source tree under the output | off |
| Preview only | all | list planned outputs without writing | on |

Nothing is written until **Preview only** is off; originals are never touched. A
`showcase_manifest.csv` records every render when an output folder is set.

The durable queue uses mode-correct work units: a contact sheet is one grouped
item depending on every source image, while hero and before/after renders remain
per image. Every possible counterpart path participates in before/after identity,
including paths that do not exist yet. Stored PNGs carry a streamed SHA-256 and
mode-specific geometry metadata, so an output overwritten by another job is not
mistakenly reused. Degraded contact sheets remain useful but finish with a
visible warning listing unreadable sources.

> The watermark is a friendly funnel — it points people who see your renders back
> to **KS ToolBox**.

## Dependencies

- **Pillow** (`pip install pillow`). No numpy, no GPU, no network, no AI.

## Verify

```
python -m tools.showcase.test_smoke
```

Checks the pure helpers (contact-sheet grid dimensions, a centered hero on its
canvas, a ~2x-plus-divider before/after) and a full round-trip that a real
contact sheet and a hero PNG land on disk non-empty.

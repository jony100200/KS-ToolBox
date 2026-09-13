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
4. **Spritesheet / Grid Presentation** — compose transparent PNGs into framed icons
   and/or an N×N presentation grid (4×4, 3×3, 2×2, or custom dimensions) on a
   transparent, matched, checkerboard, or solid sheet backdrop. Supports tiling a single
   asset across the grid or packing multiple assets in sequence, with optional icon
   backdrops (solid, gradient, checker, none) and drop shadows. Delivers either the
   assembled grid sheet PNG, the individual framed icon PNGs, or both. Pagination
   can export every input as a numbered series of fixed-size grid sheets.

Use **Load presentation library** to select a volume folder containing
`Hero Renders` and `Animations`. Showcase loads the hero renders, reports the
matching GIF/MP4 coverage, selects a paginated 2×2 layout, and targets the
library's `Presentation Sheets` folder.

All modes preserve aspect ratio with Lanczos and **never upscale an asset past
2x**. RGBA assets composite over the chosen backdrop for opaque outputs, or keep
their transparency for the checkerboard / none backdrop. Outputs are PNG; hero and
before/after mirror the input folder structure when **Mirror input structure** is
on, contact sheet writes `contact_sheet.png`, and spritesheet writes
`<name>_spritesheet_<cols>x<rows>.png` and/or `<name>_icon.png`.

## Options

| Option | Applies to | Meaning | Default |
|---|---|---|---|
| Mode | all | contact / hero / before_after / spritesheet | `contact` |
| Cell size | all | thumbnail box (contact), canvas square (hero/spritesheet), panel box (before/after) | `256` |
| Padding | all | gutter / margin in px | `16` |
| Columns | contact | grid columns | `4` |
| Filename labels | contact | caption each thumbnail with its filename | on |
| Title header | contact | optional text across the top | (blank) |
| Backdrop | hero | solid / gradient / checker / none | `solid` |
| Backdrop colour | hero | hex, e.g. `#111827` (gradient darkens toward the bottom) | `#111827` |
| Caption | hero | optional text under the asset | (blank) |
| Drop shadow | hero / spritesheet | soft shadow behind the asset | on |
| Watermark | hero | small "Made with KS ToolBox" corner mark | on |
| Second folder | before_after | folder of counterparts matched by filename | (required) |
| Grid preset | spritesheet | 4x4 / 3x3 / 2x2 / custom | `4x4` |
| Columns / Rows | spritesheet | grid dimensions for custom or presets | `4` / `4` |
| Icon backdrop | spritesheet | solid / gradient / checker / none backdrop per cell | `solid` |
| Icon backdrop colour | spritesheet | hex colour for solid/gradient icon backdrop | `#111827` |
| Sheet backdrop | spritesheet | transparent / match (use icon bg) / checker / solid | `transparent` |
| Tile single input | spritesheet | repeat single image across all grid cells | on |
| Paginate all inputs | spritesheet | create numbered sheets until every selected image is placed | off |
| Spritesheet PNG | spritesheet | deliverable toggle: export assembled grid sheet PNG | on |
| Individual Icons PNG | spritesheet | deliverable toggle: export per-file framed icon PNGs | on |
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

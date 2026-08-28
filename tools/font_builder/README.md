# Font Builder

Batch-compile raster glyph images (PNG, JPG, WebP, BMP) and vector SVGs into standard TrueType (`.ttf`) font files.

---

## Features

- **Automated Glyph Mapping**: Automatically maps filenames to unicode codepoints:
  - Single characters: `A.png`, `b.png`, `1.png`, `$.png`
  - Case-specific prefixes: `cap_a.png`, `small_a.png`, `upper_b.png`, `lower_b.png`
  - Named punctuation: `space.png`, `exclamation.png`, `question.png`, `comma.png`, `period.png`, `colon.png`, `hyphen.png`, `quote.png`, etc.
  - Unicode hex: `u0041.png`, `uni0041.png`, `0x0041.png`
- **Vectorization Engine**: Uses `vtracer` (Rust-based vectorization) with custom speckle filtering to generate clean Bezier outlines.
- **Font Metric Normalization**:
  - Customizable Units Per Em (1000 or 2048 UPM).
  - Configurable Cap-Height, Ascent, and Descent.
  - Automatic baseline alignment with intelligent descender offset for `g`, `j`, `p`, `q`, `y`, `,`, `;`.
  - Proportional advance widths with side-bearing padding or fixed-width Monospace mode.
- **Standards Compliant**:
  - Compiles valid TrueType tables (`head`, `hhea`, `maxp`, `OS/2`, `hmtx`, `cmap`, `loca`, `glyf`, `name`, `post`).
  - Includes standard `.notdef` fallback glyph and whitespace `space` advance.

---

## Dependencies

- `fonttools` (TrueType / OpenType font compilation)
- `vtracer` (Rust image vectorizer)
- `Pillow` (Raster image handling)

---

## Smoke Test

```bash
python -m tools.font_builder.test_smoke
```

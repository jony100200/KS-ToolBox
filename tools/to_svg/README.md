# To SVG

Batch-vectorize raster images (PNG/JPG/WEBP/BMP/TIFF) into scalable **SVG**
vector graphics. Point it at files or a folder, preview the plan, then run.

## What it does

For each image it calls [vtracer](https://github.com/visioncortex/vtracer) — a
fast Rust image tracer — to convert the pixels into stacked (or cut-out) vector
paths and writes a `.svg` beside the source (or into an output folder, mirroring
the input tree if you ask it to). Every real run is atomic (writes a `.part`
temp, then renames) and logged to a `svg_manifest.csv` in the output folder.

## Dependencies

- **vtracer** — the tracer (`pip install vtracer`). Imported lazily: the tool
  appears in the sidebar without it and reports the missing dep only when you
  run a conversion.
- **pillow** — only needed by the smoke test to synthesize a sample image.

## Options

| Option | Meaning |
|--------|---------|
| **Color mode** | `color` (full-colour trace) or `binary` (black/white). |
| **Filter speckle (px)** | Discard blobs smaller than this — denoises the trace. |
| **Color precision** | Bits of colour kept (higher = more distinct colours). |
| **Output folder** | Blank = an `svg` folder beside each source. |
| **Preview only** | Dry-run: list what would be written, no files touched. |
| **Mirror input structure** | Recreate the source subtree under the output folder. |

Preview + Confirm + Logging: the panel defaults to preview-only, and every real
run writes a manifest — per the batch-tool contract.

## Verify

Run the smoke test in an ephemeral uv overlay (does not touch the app venv):

```
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; uv run --no-project --python 3.12 --with vtracer --with pillow python -m tools.to_svg.test_smoke
```

It writes a real PNG, vectorizes it, and asserts a non-empty `.svg` containing an
`<svg` tag. Skips cleanly (prints SKIP, exits 0) if vtracer or Pillow is absent.

## Credit

The vtracer conversion path is distilled from KS ChobiEngine's
`tosvg_wrapper` module (the job-runner wrapper and multi-engine silent-fallback
were dropped; a missing dep is now announced as a value, never swallowed).

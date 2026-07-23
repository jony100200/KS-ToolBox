# To SVG

Batch-vectorize raster images (PNG/JPG/WEBP/BMP/TIFF) into scalable **SVG**
vector graphics. Point it at files or a folder, preview the plan, then run.

## What it does

For each image it calls [vtracer](https://github.com/visioncortex/vtracer) — a
fast Rust image tracer — to convert the pixels into stacked (or cut-out) vector
paths and writes a `.svg` beside the source (or into an output folder, mirroring
the input tree if you ask it to).

The CustomTkinter screen uses the shared durable queue: pause/resume,
cancellation boundaries, crash recovery, per-image quarantine, history, and a
morning report are consistent with the other KS batch tools. Every real output
is staged, parsed as bounded XML, rejected if it contains document/entity
declarations, hashed with SHA-256, and atomically published. Identical completed
jobs reuse only exact valid SVGs; missing or same-size-corrupted outputs retrace
only their source image.

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

Preview + Confirm + Logging: the panel defaults to preview-only. Every real run
preflights same-name/source collisions, confirms the batch and overwrite count,
writes an atomic `svg_manifest.csv`, and writes the shared JSON completion
report. A failed or cancelled trace cleans its `.part` file and preserves an
existing valid destination.

## Bounds and validation

- Input and output files are capped at 512 MiB each.
- SVG parsing caps at 1,000,000 elements.
- Color mode, hierarchy, speckle, color precision, and path precision are
  validated before dry-run or conversion.
- Symlinked inputs are rejected.
- VTracer remains lazy and optional; no model or AI runtime is used.

## Verify

Run the smoke test in an ephemeral uv overlay (does not touch the app venv):

```
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; uv run --no-project --python 3.12 --with vtracer --with pillow python -m tools.to_svg.test_smoke
```

Deterministic stub checks always run: strict options, collision detection,
staged cleanup, cancellation, forbidden XML declarations, SVG/hash validation,
and same-size corruption repair. When vtracer and Pillow are available, the
same smoke also performs and validates a real trace; otherwise only that
optional leg is skipped.

## Credit

The vtracer conversion path is distilled from KS ChobiEngine's
`tosvg_wrapper` module (the job-runner wrapper and multi-engine silent-fallback
were dropped; a missing dep is now announced as a value, never swallowed).

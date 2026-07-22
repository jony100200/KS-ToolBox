# Clean Cutout

Remove the background from a photo and **clean the edge fringe** the matte leaves
behind. Batch-capable: point it at a folder of hundreds of images and it mirrors
the input structure into the output. Saves transparent RGBA PNGs.

## What it does

1. **Cut** the subject with the **u2net** model, run directly on **ONNX Runtime**
   (lean — no rembg). Model selectable: `u2net` (default) or `u2netp` (lighter).
2. **Despill** (opt-in) — kill green tint from a green screen.
3. **Defringe** — erode the alpha edge inward and feather it, removing the 1-px
   background-coloured halo the matte leaves on outlines. Background-agnostic;
   this is also what gives soft edges without alpha-matting.
4. **Save** an RGBA PNG; reject an empty matte (kept < 1%) instead of writing junk.

Nothing is written until you turn off **Preview only**; originals are never
touched. A `cutout_manifest.csv` records every file when an output folder is set.

## Options

| Option | Meaning | Default |
|---|---|---|
| Model | `u2net` · `u2netp` (lighter) | `u2net` |
| Hi-Q matte | use the **rembg** backend (extra models + alpha matting); needs rembg installed | off |
| Defringe | erode + feather the alpha edge to kill the halo | on |
| Edge erode (px) | halo erosion width | `1` |
| Feather | edge feather radius | `0.6` |
| Green-screen despill | remove green tint (chroma-green sources only) | off |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | list planned outputs without running the model | on |

## Dependencies

- Default (lean): **onnxruntime** + **Pillow** + **numpy** — `pip install onnxruntime pillow numpy`.
  The u2net model (~176 MB) is downloaded to `~/.u2net/` on first use (reuses an
  existing rembg download if present).
- Optional **Hi-Q matte** backend: `pip install rembg` (heavier; adds alpha
  matting + the `isnet`/`birefnet` models).

If a dependency is missing the tool reports a clear "install X" message per file —
it never crashes.

## Why ONNX-direct instead of rembg

The previous rembg dependency dragged in `pymatting → numba → llvmlite/scipy/
scikit-image` (~237 MB) for optional alpha-matting. Running u2net directly on
onnxruntime produces a validated-identical matte (mean |Δalpha| ≈ 0.3/255, 99.97%
foreground agreement) for ~90 MB total, and removes the Python-3.12 `llvmlite`
build fragility. rembg remains available behind the Hi-Q toggle for anyone who
wants its extras.

## Verify

```
python -m tools.clean_cutout.test_smoke
```

Always tests the edge math (despill / defringe / coverage) with just numpy+Pillow;
additionally runs the full ONNX pipeline when onnxruntime is installed and the
u2net model is already cached (a smoke test never triggers the model download).

## Credits

The defringe/despill math is generalised from RupayanFlow's green-screen keyer.

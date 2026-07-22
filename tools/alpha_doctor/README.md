# Alpha Doctor

Remove image backgrounds and repair alpha — **deterministically**. No model
download, no GPU, no account, no network for the default methods; repeatable
output from identical inputs. Optional AI is one extra method, never required.

Batch-capable, mirrors the input folder structure, saves transparent RGBA PNGs.

## Methods

| Method | How | Deterministic? |
|---|---|---|
| **Auto solid background** (default) | detects the flat border colour and keys it out | ✅ |
| **Chroma key** | keys a chosen colour (green/blue/white/custom) — green/blue screen, flat logos | ✅ |
| **Edge flood-fill** | removes background regions connected to the image border | ✅ |
| **AI matte (u2net)** | ONNX Runtime salient-object matte for hard photographic subjects | optional — downloads a ~176 MB model on first use, CPU-only |

## Post-processing (all deterministic)

- **Defringe** — erode + feather the alpha edge to kill the 1-px halo.
- **Green despill** — remove green-screen tint that bleeds into edges.
- **Premultiply** — output premultiplied alpha (for engines that expect it).

Nothing is written until you turn off **Preview only**; originals are never
touched. A `cutout_manifest.csv` records every file when an output folder is set.

## Dependencies

- Default (deterministic): **Pillow** + **numpy** — `pip install pillow numpy`.
- Optional **AI matte** method only: `pip install onnxruntime` (model auto-downloaded).

Works fully without AI: product renders, sprites, logos, and green/blue-screen
images need none of it.

## Verify

```
python -m tools.alpha_doctor.test_smoke
```

The deterministic core is verified with just numpy + Pillow (no model); the AI
method is exercised only if onnxruntime and a cached model are present.

## Credits

Despill/defringe generalised from RupayanFlow's green-screen keyer.

# Alpha Doctor

Remove image backgrounds and repair alpha — **deterministically**. No model
download, no GPU, no account, no network for the default methods; repeatable
output from identical inputs. Optional AI is one extra method, never required.

Batch-capable, mirrors the input folder structure, saves transparent RGBA PNGs.

Every image runs as its own durable queue item. Jobs support pause, cancellation,
retry, quarantine, restart recovery, history, and a completion report while the
existing custom Alpha Doctor screen remains the control surface.

## Methods

| Method | How | Deterministic? |
|---|---|---|
| **Auto solid background** (default) | detects the flat border colour and keys it out | ✅ |
| **Chroma key** | keys a chosen colour (green/blue/white/custom) — green/blue screen, flat logos | ✅ |
| **Edge flood-fill** | removes background regions connected to the image border | ✅ |
| **AI matte — Compact U2NetP** | ONNX Runtime salient-object matte for hard photographic subjects | optional — about 5 MB, recommended for ordinary CPU-only installs |
| **AI matte — Full U2Net** | Higher-detail ONNX Runtime salient-object matte | optional — about 176 MB, requires approval, CPU-only |

## Post-processing (all deterministic)

- **Defringe** — erode + feather the alpha edge to kill the 1-px halo.
- **Green despill** — remove green-screen tint that bleeds into edges.
- **Premultiply** — output premultiplied alpha (for engines that expect it).

The normal action writes transparent PNG cutouts and never touches originals.
**Plan only** is available when you only want to validate the output paths; it
does not calculate a matte or create files. A `cutout_manifest.csv` records every
file when an output folder is set.

Before a real batch starts, Alpha Doctor resolves every destination. It refuses
an output that would replace a selected source and refuses two inputs that
would produce the same PNG. Numeric settings are finite and bounded, custom
keys must use `#RRGGBB`, and unknown methods/models fail visibly.

AI stays opt-in at both levels: selecting AI does not silently authorize a
network request. If the model is absent, the UI asks before downloading it.
In the source workspace, downloaded models live in the app's local `models/`
folder; a bundled build stores them beside the executable. Cached and downloaded
models are checksum-verified; a corrupt cached model is rejected rather than
handed to ONNX Runtime. A verified unchanged model is remembered for the app
session so batch items do not repeat the hash.

Successful cutouts are staged, decoded as RGBA, checked for original dimensions
and alpha coverage, hashed, and only then atomically committed. Stored results
are reused only while that exact PNG still validates; missing or same-size
corrupt outputs are regenerated. Model downloads are streamed under a 512 MiB
ceiling and can be cancelled without leaving a partial model.

## Dependencies

- Default (deterministic): **Pillow** + **numpy** — `pip install pillow numpy`.
- Optional **AI matte** method only: `pip install onnxruntime` (the model is
  downloaded only after explicit approval).

Works fully without AI: product renders, sprites, logos, and green/blue-screen
images need none of it.

## Verify

```
python -m tools.alpha_doctor.test_smoke
```

The deterministic core is verified with just numpy + Pillow (no model); the
suite also proves source/collision protection, strict settings, download
consent/cancellation, cached-model checksum rejection, staged cleanup, exact
reuse validation, and same-size corruption detection. The AI method is
exercised only if onnxruntime and a cached model are present.

## Credits

Despill/defringe generalised from RupayanFlow's green-screen keyer.

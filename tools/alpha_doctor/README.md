# Alpha Doctor

Remove image and video backgrounds and repair alpha — **deterministically**. No model
download, no GPU, no account, no network for the default methods; repeatable
output from identical inputs. Optional AI is one extra method, never required.

Batch-capable, mirrors the input folder structure, saves transparent RGBA PNGs for
images and audio-preserving **ProRes 4444 `.mov`** (`yuva444p10le`, 16-bit alpha)
clips for videos.

Every file runs as its own durable queue item. Jobs support pause, cancellation,
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

- **Decontaminate edges** — propagates clean interior subject RGB outward across
  eroded and semi-transparent boundary pixels before choke + feather, eliminating
  baked-in background halos (such as `#000000` grey-white rims or warm backlight
  halos) without eroding the interior subject. **Rim px** (`1–16`) sets the
  inward erosion distance.
- **Defringe** — erode + feather the alpha edge to kill the 1-px halo.
- **Despill** — *Auto (screen edges)* (default): suppresses green, blue, or red
  screen colour in the edge band and under transparent pixels only while keeping
  the opaque interior untouched. Also supports explicit **Red / orange rim (edges)**,
  **Green (edges)**, **Blue (edges)**, **Green, whole image**, or **Off**.
- **Edge flood: also remove enclosed background** (off by default) — edge flood only
  removes background connected to the image border, so background trapped inside the
  subject (e.g. between an arm and the head) stays opaque. This option also removes
  enclosed background-coloured areas thicker than about 9 px. Known limit: on line art
  or a light subject on a matching light background it also removes subject areas of
  the same colour (pale skin, white strings), so check the result or use the AI matte.
- **Premultiply** — output premultiplied alpha (for engines that expect it).

The normal action writes transparent RGBA PNG cutouts (images) or ProRes 4444
`.mov` files (videos, preserving source audio) and never touches originals.
**Plan only** is available when you only want to validate the output paths; it
does not calculate a matte or create files. A `cutout_manifest.csv` records every
file when an output folder is set.

Before a real batch starts, Alpha Doctor resolves every destination. It refuses
an output that would replace a selected source and refuses two inputs that
would produce the same output path. Numeric settings are finite and bounded, custom
keys must use `#RRGGBB`, and unknown methods/models fail visibly.

AI stays opt-in at both levels: selecting AI does not silently authorize a
network request. If the model is absent, the UI asks before downloading it.
In the source workspace, downloaded models live in the app's local `models/`
folder; a bundled build stores them beside the executable. Cached and downloaded
models are checksum-verified; a corrupt cached model is rejected rather than
handed to ONNX Runtime. A verified unchanged model is remembered for the app
session so batch items do not repeat the hash.

Successful cutouts are staged (`.part.png` / `.part.mov`), decoded and checked
for dimensions, alpha presence, and coverage, hashed, and only then atomically
committed. Stored results are reused only while that exact output still
validates; missing or same-size corrupt outputs are regenerated. Model downloads
are streamed under a 512 MiB ceiling and can be cancelled without leaving a
partial model.

## Dependencies

- Default (deterministic, images): **Pillow** + **numpy** — `pip install pillow numpy`.
- Video inputs (`.mp4`, `.mov`, etc.): **FFmpeg** + **FFprobe** (bundled `bin/` or on `PATH`).
- Optional **AI matte** method only: `pip install onnxruntime` (the model is
  downloaded only after explicit approval).

Works fully without AI: product renders, sprites, logos, and green/blue-screen
images and presenter videos need none of it.

## Verify

```
python -m tools.alpha_doctor.test_smoke
```

The deterministic core is verified with just numpy + Pillow (no model); the
suite also proves inward RGB edge decontamination, red/orange rim despill,
source/collision protection, strict settings, download consent/cancellation,
cached-model checksum rejection, staged cleanup, exact reuse validation, and
same-size corruption detection. Video ProRes 4444 `.mov` round-trip and
cancellation cleanup run whenever FFmpeg + FFprobe are available. The AI method
is exercised only if onnxruntime and a cached model are present.


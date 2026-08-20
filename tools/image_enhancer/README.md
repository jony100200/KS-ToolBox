# Image Enhancer

A standalone, local batch enhancer. It never starts or contacts ComfyUI.
Every output is a new PNG, with the normal Toolbox queue, report, retry, and
preview controls.

## Smart Enhance

Select one image or a folder, choose a mode, then press **Enhance Selected**.
Every item is profiled first and its measured plan is recorded in the batch
manifest.

- **Deterministic** — no model is loaded. It only performs bounded pixel
  operations: exposure, colour, contrast, noise reduction, and detail balance.
  Geometry, text, clothing, composition, and identity are not regenerated.
- **Hybrid** (default) — starts with the deterministic plan and uses the local
  Real-ESRGAN worker only when output resolution was requested or the optional
  auto-router identifies a small source that benefits from a restoration pass.
- **AI only** — explicitly runs the local restoration model. It is for damaged
  inputs and may alter fine detail, so it is never hidden behind the default
  Enhance behaviour.

The advanced **Experimental: decide mode per image** toggle lets the local
analysis choose among the three modes for each file. Its decision and confidence
are saved in the completion manifest; turn it off whenever you require one
fixed production route.

### Colour continuity guard

Before and after every run, Smart Enhance compares a conservative warm-surface
colour signature from the image itself. It does not classify ethnicity or a
person. If a model pass materially washes out a warm/brown complexion, the item
is marked **review** instead of silently reported as completed. The output is
kept for owner review and the measurements are written to its manifest.

## Phase 1 — restoration

Choose a preset for gentle restoration, detail, or colour recovery. Each is a
measured, typed filter stack. Profiles include portrait polish, texture cleanup,
sharp abstract, and smooth bilateral alongside the original restoration modes.
The engine keeps the source untouched, compacts compatible adjacent operations,
preserves alpha, and records the effective operation order in the batch result.

The advanced controls append to the selected profile in a fixed quality-safe
order: denoise → brightness/contrast/gamma → temperature/tint → hue/saturation/
vibrance → high-pass/edge detail/sharpen. Hue is degrees (-180 to 180),
saturation and tonal controls are explicit numeric factors, and the other
colour controls are bounded to avoid accidental destructive grades. Downscale
with **Image Rescale**; this tool uses AI only when the requested scale is 2×,
3×, or 4×.

This compact approach borrows the useful ideas—not code or runtimes—from the
archived reference work: typed stacked passes and masks from Rupayan/Chobi,
G'MIC-style HSV adjustment ordering, PixiEditor's composable colour controls,
and Krita's non-destructive adjustment-layer discipline.

## Phase 2 — targeted enhancement

- **Subject mask** uses local U2NetP to limit the enhancement to the main
  foreground subject.
- **Faces** uses local YuNet face boxes; optional face-detail mode applies the
  existing local Real-ESRGAN model to each detected face crop and feather-blends
  it back.
- **Manual box** accepts one `x,y,width,height` percentage region for a
  repeatable batch refinement. Enable debug outputs to save mask and box
  previews beside the result.

## Phase 3 — local repair

**Repair enclosed transparent holes** uses OpenCV Telea inpainting only on
transparent islands fully surrounded by opaque pixels. It fixes small cutout
holes and damage without altering the exterior alpha edge. It does not invent
missing anatomy, hands, or limbs; that is generative masked inpainting and must
remain a separately explicit model workflow.

## Local utility runtime

The ignored `models/` cache holds all optional model data:

- Real-ESRGAN NCNN general/anime models for 2×/3×/4× super-resolution;
- U2NetP (~5 MiB) for foreground masks;
- YuNet (~227 KiB) for face boxes.

OpenCV is an optional Toolbox dependency used for YuNet and alpha-hole repair.
All model imports are lazy, the app does not load a model at startup, and no
background model process remains after a queue item finishes.

## Verify

```powershell
python -m tools.image_enhancer.test_smoke
```

The smoke run exercises restoration, manual BBox/mask output, alpha-hole repair,
and a real local Real-ESRGAN 2× inference.

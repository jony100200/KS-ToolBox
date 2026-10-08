### alpha_doctor
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

### asset_auditor
# Asset Auditor

A deterministic, read-only batch inspector for a folder of images/assets. Point
it at a folder, it scans recursively and writes a report describing everything
worth knowing before you ship, pack, or train on those assets — **no AI, no
network, no GPU**. numpy + Pillow + stdlib only.

It **never modifies or deletes a source file.** The tool's output is three
report files written to your chosen folder.

## What it finds

| Check | Meaning |
|---|---|
| Exact duplicates | byte-identical files, grouped by sha256 |
| Near duplicates | perceptually similar — dHash Hamming distance ≤ threshold (union-find grouping) |
| Corrupt / unreadable | Pillow `verify()`/decode failure **plus** a magic-byte header check (PNG/JPEG/GIF/WEBP/BMP/TIFF) |
| Unsafe filenames | Windows reserved names (`CON`, `NUL`, `COM1`…), illegal chars `<>:"/\|?*`, control chars, trailing dot/space |
| Empty files | 0 bytes |
| Oversized files | larger than a configurable MB threshold |

### audio_tool
# Audio Tool

Deterministic **batch audio processing** with the bundled ffmpeg. No AI, no
network — every operation is a plain ffmpeg invocation, so the same input plus
the same options always yields the same output.

It complements **Format Converter**: Format Converter does one-shot
format changes across many file families; Audio Tool is audio-focused and adds
trimming, fades and loudness normalization, all applied in a single pass.

## Operations (any combination, one pass)

- **Convert** to a target format — `mp3 · wav · flac · aac · m4a · ogg · opus`.
  Lossy targets (mp3/aac/m4a/ogg/opus) honour the **bitrate** option.
- **Trim** — keep `[start, end]`. Each field takes seconds (`12.5`) or `mm:ss`
  / `hh:mm:ss`. Implemented as input-side `-ss` / `-to`.
- **Fade** — fade-in and/or fade-out (seconds) via the `afade` filter. The
  fade-out start is computed from the effective (post-trim) duration, which is
  probed with ffprobe.
- **Normalize** — loudness-normalize via the `loudnorm` filter.

### dataset_manager
# Dataset Manager

Deterministic file management for image + caption datasets — the boring, safe
plumbing that AI dataset prep needs, **without shipping any AI model, captioner,
or network call.** It pairs, audits, buckets, splits, and batch-edits captions,
and it does all of it **copy-only**: your source folder is only ever *read*.

> Helps you prepare training datasets (LoRA/fine-tune style image+caption sets)
> without any AI on board — no model weights, no VLM, no internet. Just correct,
> reproducible file handling.

## Operations

Point it at a folder of images. Sidecar captions are matched by **stem** —
`img_007.png` ↔ `img_007.txt` (or `.caption`).

1. **Pair report** — lists images *with* captions, images *missing* captions, and
   *orphan* captions (a `.txt` with no image). Written as `pair_report.csv` +
   `pair_report.json`.
2. **Caption find & replace** — literal or regex find/replace across caption text,

### font_builder
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

### format_converter
# Format Converter

Convert files between formats — **images, audio/video, and documents** — in batch,
mirroring the input folder structure into the output. Pick a source set, choose
**Convert to**, and go. The target dropdown only offers formats valid for the
files you loaded.

## What it converts

**Images** (Pillow): `png · jpg · webp · bmp · tiff · ico` ↔ each other.
- Alpha is flattened onto a background colour when the target can't hold it (jpg/bmp).
- Animated `gif ↔ webp` preserves frames; `gif → png/jpg` takes the first frame.
- `ico` output is written multi-size (16/32/48/256).

**Audio / Video** (bundled ffmpeg):
| From | To |
|---|---|
| video (mp4/mov/mkv/webm/avi/…) | `mp4 · mov · mkv · webm` |
| video | `gif` (palette-optimized) |
| video | `mp3 · wav · aac · m4a · flac` (extract audio) |

### icon_normalizer
# Icon / Sprite Normalizer

Make a whole batch of icons or sprites one **uniform, centred, square size**.
Each image is trimmed to its opaque bounds, scaled to fit — aspect preserved —
onto a transparent square canvas, and centred. Output is always RGBA PNG. This
is the daily "make all my icons the same clean 256×256" batch tool.

## What it does

1. **Trim** (optional) — crop away transparent borders down to the opaque bounds.
2. **Fit** — scale the subject to fit the canvas, keeping its aspect ratio.
3. **Pad** (optional) — leave an even transparent margin around the content.
4. **Centre** — place the result dead-centre of a square `size` × `size` canvas.

Saves RGBA PNGs. Nothing is written until you turn off **Preview only**; originals
are never touched. An `icon_manifest.csv` records every file when an output folder
is set. Fully-transparent images are reported as **skipped** (nothing to centre).
Jobs run through the shared durable queue, can pause or cancel at item boundaries,
resume from checkpoints, and reuse only outputs that reopen as square RGBA PNGs.

### image_enhancer
# Image Enhancer & Studio Restoration Suite

A local, non-destructive batch image enhancer, studio lighting editor, and surface restoration suite built for `KS-ToolBox`. It never connects to external cloud servers or requires heavy ComfyUI processes for enhancement. Every output is written cleanly as a new PNG while keeping the original file untouched, with full queue management, provenance manifests, retry mechanisms, and real-time interactive Before/After preview controls.

---

## 1. Core Philosophy: One-Button Auto Enhance

The primary workflow is **instant, single-button intelligence**:
1. Drop one image or an entire folder into the queue.
2. Select preset **Auto (Recommended)** and click **Enhance Selected**.

Each image is automatically profiled on CPU in milliseconds:
* **Linear-Light Luma & Dynamic Contrast:** Analyzes shadow clipping, midtone depth, and highlight blowout.
* **Color Balance & Cast:** Detects warm/cool/green illuminant casts on near-neutral surfaces and calculates correction factors.
* **Noise vs Edge Separation:** Uses Laplacian Median Absolute Deviation (MAD) to separate fine texture edges from sensor noise or AI generation grain.
* **Surface Quality & De-Gloss:** Evaluates specular highlight distance ($P_{92} - P_{45}$) to eliminate oily, waxy, or plastic artificial skin without flattening pores.
* **Dynamic Plan Compilation:** Compiles a custom-tailored multi-band filter pass specifically balanced for that individual image.

---

### image_rescale
# Image Rescale

Batch-resize images by one of four sizing modes, with sensible defaults (never
upscales unless you ask, sharpest resampling picked automatically). Mirrors the
input folder structure into the output.

## Modes

| Mode | Meaning |
|---|---|
| `longest_side` | scale the longest edge to N pixels |
| `max_mp` | scale to a target megapixel count (1 MP = 1024×1024) |
| `scale_factor` | multiply both dimensions by a factor |
| `fit_inside` | scale to fit within a W×H box (aspect preserved) |

## Options

| Option | Meaning | Default |
|---|---|---|
| Resample | `auto` (Lanczos down / bilinear up) · lanczos · bicubic · bilinear · nearest | `auto` |

### material_converter
# Material Converter

Batch-process folders of loose PBR texture maps: detect texture sets by
filename, pack/unpack channels, convert normal conventions, invert
gloss↔roughness, resize consistently, and rename to an engine's naming
convention — with a per-set material manifest.

**100% deterministic. No AI, no network, no GPU** — just numpy + Pillow.

## What it does

Point it at a folder of maps. It groups files that share a base name into
**texture sets**, classifying each by its suffix keyword:

| Role | Recognized suffixes |
|---|---|
| basecolor | `_BaseColor` `_albedo` `_diffuse` `_color` `_col` |
| normal | `_Normal` `_nrm` `_norm` |
| roughness | `_Roughness` `_rough` `_rgh` |
| gloss | `_Gloss` `_glossiness` |

### metadata_scrubber
# Metadata Scrubber

Batch-copies images into a delivery folder with every trace of provenance removed.

## Why

Generated images are self-documenting, which is excellent internally and bad on publish:

- **ComfyUI** writes its entire API graph into the PNG `prompt` and `workflow` chunks — every
  node, model filename, LoRA, and prompt.
- **stable-diffusion.cpp** writes prompt, seed, sampler, steps, LoRA and model names into
  `parameters`.
- **Cameras and phones** add EXIF, which can include GPS coordinates.

All of it travels with the file. Anyone who receives an image can read it in seconds.

## What it does

Copies each selected image to the output folder, stripping:

### package_extractor
# Package Extractor

Deterministic batch extraction of Unity `.unitypackage` files and `zip` / `tar`
archives, with original-folder reconstruction and SAFE handling of untrusted
input. No AI, no network, no subprocess — **Python standard library only**.

## Formats

| Format | How |
|---|---|
| `.unitypackage` | It's a **gzipped tar**. Each asset is a `<GUID>/` entry holding `asset` (the file bytes), `asset.meta`, and `pathname` (a text file with the original Unity project path, e.g. `Assets/Art/hero.png`). Extraction reads each GUID's `pathname` and writes its `asset` bytes to that reconstructed relative path — rebuilding the original `Assets/…` tree. |
| `.zip` | `zipfile` |
| `.tar`, `.tar.gz` / `.tgz`, `.tar.bz2`, `.tar.xz` | `tarfile` (transparent compression) |

Each archive extracts into its **own `<stem>/` subfolder** under the output
folder. A real batch is rejected before it starts if selected same-stem
archives would share that folder, so their contents cannot silently intermix.

## Security guarantees

### pixel_art
# Pixel Art Studio & Retro Palette Remapper

A standalone, batch-capable retro pixel-art converter and hardware palette remapping studio. Turn photos, illustrations, and 2D character renders into clean, authentic retro pixel-art sprites and scene art.

---

## Features

1. **Curated Retro Hardware & Indie Palettes:**
   * **PICO-8:** 16 iconic fantasy console colors.
   * **Game Boy (DMG-01):** 4 olive-green monochrome LCD shades.
   * **Game Boy Pocket:** 4 crisp grayscale LCD shades.
   * **NES / Famicom:** 54 authentic 8-bit console colors.
   * **Commodore 64:** 16 warm VIC-II CRT shades.
   * **CGA Mode 1 High / Low:** Classic 4-color PC gaming palettes.
   * **ENDESGA 32 (EDG32):** The indie game developer pixel-art standard.
   * **Cyberpunk Neon:** 8 electric synthwave neon shades.
   * **1-Bit Monochrome:** High-contrast pure black and white.
   * **Auto (Adaptive Median-Cut):** Dynamically quantized palette of $N$ colors.

### showcase
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

### sprite_viewer
# Sprite Viewer

Load a sprite sheet, an animated GIF/WebP/APNG, or a folder of numbered frames
and **view / play** them — step, scrub, adjust FPS, slice a grid, toggle an alpha
checkerboard. Deterministic, offline, **no AI, no network**. This is the free /
offline answer to paid "sprite analyzer" tools.

## What it does

Load ONE source and page through its frames:

- **Sheet grid** — set **rows × cols**; the sheet is sliced into equal frames.
- **Cell size** — set **cell width × height**; the sheet is tiled into fixed cells.
- **Auto-detect** — find discrete sprites on a transparent sheet by **alpha
  connected-components** (threshold → dilate → flood-fill → reading-order sort).
- **Animation / Folder** — an animated GIF/WebP/APNG loads its frames directly
  (Pillow `n_frames`/`seek`); a folder loads its images in natural-sorted order;
  a still image loads as a single frame.

Then **play** at an adjustable FPS, **step** prev/next, **scrub** the timeline

### texture_renderer
# Texture Renderer

Batch-export textures from **Substance 3D Designer** (`.sbsar`) and **Material
Maker** (`.ptex`) projects by driving their external command-line tools. A KS
ToolBox plugin.

Ported from the standalone *Universal Texture Batch Renderer*
(UniversalBatchRenderer) — behavior preserved, engine made pure and
cross-platform, anti-patterns fixed (see below).

## What it does

Two sub-modes, one per engine, in a tabbed panel over a shared console:

- **Substance (`.sbsar`)** — scans an input directory for `.sbsar` archives and
  renders each via `sbsrender` at the chosen resolution.
- **Material Maker (`.ptex`)** — scans for `.ptex` projects and exports each via
  `material_maker` for a target engine (Unreal / Godot / Unity / Blender), then
  cleans the non-PNG sidecars the exporter drops and, optionally, resizes the
  exported PNGs to a target size.

### tileset_checker
# Tileset Checker

Judge and visualize how seamlessly a texture tiles — **deterministic, no AI, no
network**. For each texture it computes a seam-continuity score and writes the
classic seam-test previews. This is the free/offline answer to paid "tileset"
tools: they *generate* seamless textures; this one *checks and previews* the
ones you already have (or the ones a generator produced).

## What it does

| Output | Meaning |
|---|---|
| **Seam score** | Compares opposite wrap edges — left↔right (X) and top↔bottom (Y) — with mean-abs-diff + RMSE, normalized `0..1` (1 = perfectly seamless). Reported per-axis and overall (overall = the worse axis). |
| **Wrap-offset preview** | Rolls the image by half in X and Y so any seam lands in the centre where it's obvious — the classic "offset filter" seam test. |
| **Tile montage** | An N×N (default 3×3) tiled grid to eyeball repetition and seam lines. |
| **Edge-diff heatmap** | A compact two-bar strip: the X-seam and Y-seam discontinuity as a heatmap (bright = large break). |

Previews are written next to an aggregate `seam_scores.csv` + `seam_scores.json`.
Everything is deterministic (same input → same score and pixels) and
non-destructive: it reads your sources and only writes previews + the report.

### to_svg
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

### video_chopper
# Video Chopper

Split one video into multiple clips at **black-frame gaps** — the transitions
that separate takes, scenes, or recorded segments. Batch-capable: point it at a
folder of hundreds of videos and it mirrors the input structure into the output.

## What it does

1. **Probe** duration (ffprobe).
2. **Detect** black stretches with ffmpeg's `blackdetect` filter (runs in C — fast
   even on long sources).
3. **Plan** the content segments between the black gaps, discarding any shorter
   than the minimum clip length.
4. **Cut** each clip — lossless stream-copy by default, or frame-accurate H.264.

Nothing is written until you turn off **Preview only**; originals are never
touched. A source with no black gaps is left unchanged instead of being copied
into one redundant clip. A `chop_manifest.csv` records every file when an output
folder is set.

### video_compressor
# 🎬 Video Compressor

Shrink videos **without losing quality** — and don't waste effort on files that
are already efficient.

## How it decides (per file)

1. **Probe** (ffprobe): codec, resolution, fps, bitrate → **bits-per-pixel** efficiency.
2. **Assess**:
   - already HEVC/AV1 at a lean bpp → **skip** ("already efficient")
   - H.264 but already lean → **skip** ("not worth re-encoding")
   - bloated → **compress**, with a rough expected saving
3. **Compress**: SVT-AV1 CRF (visually-lossless, default 20) — quality-targeted,
   not bitrate-targeted. AV1 NVENC is the fast GPU alternative.
4. **Verify**: measure **VMAF** of the result vs the source. If it's below your
   floor (default 92 ≈ "no visible difference"), or not actually smaller, the
   encode is **rejected and the original kept**. Quality is proven, not assumed.
5. **Organize / clean up**: output mirrors your input folder structure; the
   original is removed **only** after a verified-good output (→ Recycle Bin).


# Changelog

All notable changes to KS ToolBox. Format loosely follows Keep a Changelog.

## [Unreleased]

Image tools fix pass from the 2026-11-12 audit. Numbers are measured with the audit
images (PSNR in dB against the clean original, higher is better).

### Fixed
- **Metadata Scrubber: every JPEG failed** (`ValueError: Cannot use 'keep' when original
  image is not a JPEG`). The clean copy now reuses the source's quantization tables and
  subsampling explicitly, and bakes the EXIF orientation into the pixels before EXIF is
  dropped. Repro JPEG: before = failed; after = scrubbed, 60.1 dB vs source, no metadata left.
- **Image Rescale: colour, orientation, quality and filters.**
  - ICC profile kept (audit: dropped).
  - EXIF orientation applied, so phone photos come out upright (audit: 960x540 sideways; now 540x960).
  - JPEG/WebP quality is a user setting, default 92 with 4:4:4 at 90+ (audit: Pillow default 75).
    1080p photo to 960 px: 39.9 dB -> 45.2 dB vs a Lanczos reference.
  - `auto` now uses Lanczos when enlarging too: 2x upscale 39.2 dB -> 41.2 dB.
  - Palette/1-bit/LA images are filtered in RGB(A), not nearest-neighbour: 33.9 dB -> exact
    match with RGB Lanczos (`nearest` still keeps the exact palette for pixel art).
  - The AI path applies the source orientation to the NCNN output as well.
  - Smoke tests: `KS_TOOLBOX_SKIP_GPU=1` skips the optional Real-ESRGAN leg on a busy GPU.
- **Format Converter: ICC and orientation.** Still-image conversions keep an RGB ICC
  profile (audit: dropped for jpg and webp; now kept, PSNR unchanged at 42.2 / 41.7 dB),
  colour-manage CMYK sources with a profile to sRGB, and apply the EXIF orientation
  (audit: phone JPEG -> PNG came out 1920x1080 sideways; now 1080x1920).
  ICO output is upright too.
- **Image Enhancer: presets, filters and transparency.** Audit images, 1024x576, PSNR vs the
  clean original (before -> after):
  - The smart plan now runs only for Auto / auto-select. Default preset on a clean photo:
    33.0 -> 37.4 dB vs the input (no unrequested brightness or de-gloss).
  - Zero-strength passes no longer run: a 0-strength de-gloss boosted pores by 15 % and
    0-strength vignette/grain/glow ran on every image (the source of the mottled grain).
  - Denoise is a noise-adaptive bilateral filter (luma + chroma), not a Gaussian:
    sigma-12 noise 26.8 dB input -> 31.0 (old, denoise 1.0) -> 36.0 dB (nlmeans ref 33.2).
    Auto on the noisy image: 24.7 -> 31.0 dB (the plan now reads a full-resolution noise sigma).
  - Sharpen / detail use a luminance unsharp mask with threshold; High-Pass adds detail
    instead of blending towards grey. Blurred input 35.0 dB: Clarity & Detail 24.7 -> 35.5,
    Texture Cleanup 21.0 -> 34.4, Sharpen 2.0 27.8 -> 36.5 (USM ref 37.4).
  - Despeckle preset is the gated 3x3 median only: 24.0 -> 50.1 dB (3x3 median ref 42.5).
  - Edge Boost adds edge detail to luminance: mean brightness 82.6 -> 82.0 (was 66.5).
  - RGBA: hidden colour under alpha=0 is filled from visible neighbours before filtering:
    magenta edge bleed 4.91 -> 0.00.
  - CLAHE / cel / anime presets without OpenCV say so in the panel, name the fallback in the
    result and mark it needs-review (no silent weaker filter). OpenCV stays optional.
  - Panel: picking a preset resets the sliders to neutral; they used to echo the preset's own
    values, which applied them twice (e.g. Vivid Pop contrast 1.10 x 1.10).
  - Speed: 0.2-1.2 s per 1024x576 image; full HD Auto 1.7 s (was 1.6), Natural Skin 4.0 s
    (was 3.0; its denoise pass is now the bilateral).
  - Smoke tests: the new quality legs need only Pillow + numpy, so they run in the shipped
    build (the old enhancer smoke skipped entirely without OpenCV). `KS_TOOLBOX_SKIP_GPU=1`
    skips the Real-ESRGAN legs.

## [1.1.0] — 2026-10-12

Major update expanding KS ToolBox to **23 verified tools**, introducing the **Unified Workflow Orchestrator**, **bounded multi-worker batch acceleration**, and a **pure MIT icon design**.

### Workflows & Automation
- **Unified Workflow Orchestrator (`toolbox/workflow.py`)**: Multi-stage chained execution with staging isolation and manifest reporting.
  - Implemented **Workflow B (Image & Web Delivery Pipeline)**: Chained resize -> background cutout / defringe -> metadata scrub -> manifest generation.
  - Built-in presets: `web_delivery`, `transparent_cutout`, `clean_rescale`, and `privacy_only`.
  - Non-destructive execution: isolates intermediary stages into temporary run directories, writing only finalized clean assets to output.
  - Dedicated **Workflows Tab** integrated directly into `ToolBoxShell` with live preview, preset selector, and batch progress tracking (`Ctrl+2` shortcut).
- **Bounded Concurrency in `BatchRunner`**: Added `max_workers` thread pool execution while serializing SQLite state transitions on the orchestrator thread.
  - Achieves up to **6.99x speedup** (1042 items/sec) on 100-item image processing batches.
  - Preserves 100% serial fallback (`max_workers=1`) for low-spec hardware and single-core environments.
  - Isolated thread-safe failure quarantine and cooperative cancellation via `CancellationToken`.

### Tool Suite Expansion (23 Verified Tools)
- Verified and enabled full tool registry:
  - **Image Enhancer**: Contrast, sharpness, brightness, and color grading.
  - **Image to PBR**: Normal, roughness, displacement, and ambient occlusion map generation.
  - **Metadata Scrubber**: Complete EXIF, GPS, camera metadata, and thumbnail removal.
  - **Lossless Compressor**: PNG/JPEG/WebP size reduction without image quality loss.
  - **Voice Recorder**: Local audio capture and note recording.
  - **YouTube Downloader**: Headless media archival powered by yt-dlp.

### Architecture & Compliance
- **Pure MIT Licensing**: Replaced Font Awesome with Tabler Icons TTF, eliminating CC-BY attribution requirements and keeping the core application 100% MIT licensed.
- **Ported Compliance & Benchmarking Suite**: Complete benchmarking harness in `benchmarks/` (`build_release_compliance.py`, `measure_batch_concurrency.py`, `measure_batch_core.py`, etc.).
- **100% Automated Test Coverage**: All 58 unit and integration tests passing cleanly on pytest.

## [1.0.0] — 2026-07-22

First public release. **17 deterministic-first batch tools** under one portable,
cross-platform UI. Free, offline, no credits, no cloud.

### Tools

**Video & Audio**
- **Video Compressor** — VMAF-gated re-encode; skips already-efficient files; deletes originals only after a verified-good output (with confirmation).
- **Video Chopper** — split at black-frame gaps; lossless stream-copy or frame-accurate re-encode.
- **Audio Tool** — batch convert / trim / fade / loudness-normalize via bundled ffmpeg.

**Images**
- **Image Rescale** — 4 sizing modes, snap-to-grid, upscale gating.
- **Format Converter** — images ↔, audio/video (ffmpeg), documents (md/docx/html → pdf, pdf → image/text).
- **Pixel Art Converter** — pixelize + median-cut palette + hard alpha.
- **To SVG** — raster → vector (vtracer).
- **Icon Normalizer** — trim, square-pad, resize icons/sprites.
- **Showcase** — contact sheets, framed hero renders, before/after comparisons.

**Game / Asset workflow**
- **Alpha Doctor** — deterministic background removal (chroma / auto-solid / edge flood-fill) + despill/defringe/premultiply; optional u2net AI method for hard photos.
- **Material Converter** — ORM/MOS packing, DirectX↔OpenGL normals, gloss↔roughness, Unity/Unreal/Godot/Blender/glTF presets, material manifests.
- **Sprite Viewer** — view & play sprite sheets / animations; grid/cell/auto-detect slicing.
- **Tileset Checker** — seam-continuity score, offset preview, N×N tile montage.
- **Texture Renderer** — batch-export from Substance Designer & Material Maker (requires those tools).
- **Package Extractor** — Unity `.unitypackage` + zip/tar extraction with folder reconstruction; zip-slip / symlink / decompression-bomb safe.
- **Unity Packager** — build a `.unitypackage` from a project folder without opening Unity; preview, atomic write, deterministic output.

**Library / Dataset**
- **Asset Auditor** — duplicate (exact + perceptual), corrupt/empty/oversized/unsafe-name/resolution audit → HTML/JSON/CSV.
- **Dataset Manager** — image/caption pairing, train/val/test split, resolution bucketing, caption edit — copy-only, non-destructive.

### Architecture
- Plugin toolbox: one CustomTkinter shell auto-discovers self-contained tool folders.
- Engine ≠ UI: pure headless `engine.py` (error-envelope, no CustomTkinter) + thin `panel.py` on a worker thread.
- Every batch tool: dry-run preview, mirror-input-structure, atomic writes, per-run CSV manifest, confirmation before any destructive action.
- Deterministic-first: 16/17 tools use no AI; Alpha Doctor is deterministic by default with an optional u2net utility model. No CUDA, no cloud, no accounts.
- ~230 ms startup; heavy/optional deps load lazily per tool and degrade with a clear in-panel message.

### Notes
- Core deps are small (`customtkinter`, `send2trash`, `pillow`, `numpy`); per-tool extras are optional (see `requirements-optional.txt`).
- ffmpeg/ffprobe power the video/audio tools (bundle in `bin/` or install on PATH).
- A bundled FFmpeg build with x264/x265 is GPL — see `THIRD_PARTY_NOTICES.md` for redistribution obligations.
- Full deterministic-vs-AI analysis in `docs/KS_TOOLBOX_OPTIMIZATION_AUDIT.md`; reproducible benchmarks in `benchmarks/`.

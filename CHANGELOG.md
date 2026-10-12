# Changelog

All notable changes to KS ToolBox. Format loosely follows Keep a Changelog.

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

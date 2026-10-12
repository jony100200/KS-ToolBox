# Changelog

All notable changes to KS ToolBox. Format loosely follows Keep a Changelog.

## Unreleased

- Format Converter: documented the existing `pdf -> md` extraction (page/section-marked
  GitHub-Flavored Markdown) in the tool's capability table, which previously listed only
  `png`/`jpg`/`txt` for PDF sources.
- Format Converter: fixed Markdown output skipping the streaming UTF-8 validation applied
  to `html` and `txt`, so a corrupted `pdf -> md` artifact could be committed unvalidated.
- Format Converter: extended the smoke test with a real `pdf -> md` leg, asserting the
  converted document carries the page/section structure the capability promises.
- Added a documentation map, direct links to all 18 tool guides, documentation
  link/coverage validation, current queue/recovery guidance, and clear historical
  labels on older migration/audit records.
- Replaced the previous FFmpeg/x265 runtime path with the validated portable
  FFmpeg build and SVT-AV1 / AV1 NVENC Video Compressor encoders.

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

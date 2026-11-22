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

## Batch (100s of videos) & Headless CLI

- **Add folder** scans recursively.
- **Mirror input folder structure** rebuilds the same tree under your output root.
- **Skip already-done** makes runs resumable — re-run anytime, finished files are skipped.
- A **`compression_manifest.csv`** is written to the output root (before/after size,
  saved %, VMAF, decision) for auditing a large run.
- **Verify & Cleanup CLI**: `python -m tools.video_compressor.cli cleanup -o <out_dir>`
  re-verifies `<out>.json` proof sidecars (output exists, smaller size, VMAF floor,
  ffprobe duration match) before removing larger originals.
- **HDD-gentle staging**: pass `--work-dir <ssd_temp_dir>` to `cli.py compress` so
  encode + VMAF run on SSD before atomically committing verified outputs to an HDD,
  or `--in-place` to place verified outputs beside each source.

## Options

| Option | Meaning |
|---|---|
| Quick Preset | One-click profiles (Archival AV1, Space Saver 1080p, Compatible HEVC MP4, Intel Arc QSV, NVIDIA NVENC) |
| Encoder | `svt_av1` (best size) · `nvenc_av1` / `qsv_av1` (GPU AV1) · `nvenc_hevc` / `qsv_hevc` (compatible GPU HEVC) |
| CRF | lower = higher quality (18–28; 20 default) |
| Min VMAF | quality floor an encode must beat to be accepted (92 default) |
| Max resolution | Keep original, 1080p, or 720p (never upscales) |
| Max FPS | Keep original, 30 fps, or 24 fps (never upsamples) |
| Audio | Copy original, or shrink raw/bloated audio (>256 kbps / PCM / FLAC) to Opus/AAC |
| Dry run | analyze + report only, no writes (default on — safe) |
| Delete original | remove source after a verified-good output (→ Recycle Bin) |

## Requirements

The portable release bundles an LGPL-3.0-or-later FFmpeg/ffprobe build with
`libsvtav1`, `av1_nvenc`, and `libvmaf`. A source run may instead use compatible
`ffmpeg` + `ffprobe` on PATH; the tool reports missing capabilities as an error.

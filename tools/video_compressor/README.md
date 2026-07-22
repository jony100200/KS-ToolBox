# 🎬 Video Compressor

Shrink videos **without losing quality** — and don't waste effort on files that
are already efficient.

## How it decides (per file)

1. **Probe** (ffprobe): codec, resolution, fps, bitrate → **bits-per-pixel** efficiency.
2. **Assess**:
   - already HEVC/AV1 at a lean bpp → **skip** ("already efficient")
   - H.264 but already lean → **skip** ("not worth re-encoding")
   - bloated → **compress**, with a rough expected saving
3. **Compress**: x265 CRF (visually-lossless, default 20) — quality-targeted, not
   bitrate-targeted. NVENC (GPU) and HandBrake are selectable alternatives.
4. **Verify**: measure **VMAF** of the result vs the source. If it's below your
   floor (default 92 ≈ "no visible difference"), or not actually smaller, the
   encode is **rejected and the original kept**. Quality is proven, not assumed.
5. **Organize / clean up**: output mirrors your input folder structure; the
   original is removed **only** after a verified-good output (→ Recycle Bin).

## Batch (100s of videos)

- **Add folder** scans recursively.
- **Mirror input folder structure** rebuilds the same tree under your output root.
- **Skip already-done** makes runs resumable — re-run anytime, finished files are skipped.
- A **`compression_manifest.csv`** is written to the output root (before/after size,
  saved %, VMAF, decision) for auditing a large run.

## Options

| Option | Meaning |
|---|---|
| Encoder | `x265` (best quality/size) · `nvenc_hevc` (GPU-fast) · `handbrake` |
| CRF | lower = higher quality (18–24; 20 default) |
| Min VMAF | quality floor an encode must beat to be accepted (92 default) |
| Dry run | analyze + report only, no writes (default on — safe) |
| Delete original | remove source after a verified-good output (→ Recycle Bin) |

## Requirements

`ffmpeg` + `ffprobe` on PATH (VMAF needs ffmpeg built with `libvmaf` — the
gyan.dev / most distro builds include it). `HandBrakeCLI` optional.

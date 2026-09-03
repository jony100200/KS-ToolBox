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

Multiple filters chain into a single `-af` (order: fade-in, fade-out,
loudnorm).

## Inputs

Accepts `.mp3 .wav .flac .aac .m4a .ogg .opus .wma .aiff`. Add files or a
folder (folders are scanned recursively).

## Safety

- **Non-destructive** — the source is never overwritten. Output goes to the
  chosen folder, or an `./audio` folder beside each source. A run that would
  overwrite the source in place is skipped with a clear reason.
- **Preview first** — "Preview only" (the default) lists exactly what each file
  would become, with no writes.
- **Atomic writes** — each output is written to a `name.part.ext` temp then
  independently probed, hashed, and renamed on success, so an interrupted or
  invalid encode never replaces a completed destination.
- **Mirror** — optionally recreate the input folder structure under the output
  root; a real batch also writes an `audio_manifest.csv` audit log.
- **Durable queue** — pause/resume, process-tree cancellation, per-file retry,
  checkpoints, quarantine, manifest/report finalization, and validated reuse.
- **Collision protection** — flat same-stem outputs and any output targeting a
  selected input are blocked before a real run instead of being overwritten.

Stored outputs are reused only when their planned path, exact byte count, and
streamed SHA-256 still match the candidate that ffprobe validated before commit.

## Dependencies

Only **ffmpeg / ffprobe**, resolved from the bundled `bin/` first, then PATH —
no pip audio packages. If neither is found the tool announces it (the Options
card shows a missing-tools hint) rather than failing silently.

## Verify

```
$env:PYTHONPATH="."; uv run --no-project --python 3.12 --with customtkinter python -m tools.audio_tool.test_smoke
```

The pure leg always runs (filter-chain + codec/muxer maps). The ffmpeg leg
synthesizes a 3s sine tone and runs convert / trim / normalize / fade plus
artifact validation, collision detection, cancellation cleanup, and malformed
time handling end-to-end; it SKIPs cleanly if ffmpeg isn't available.

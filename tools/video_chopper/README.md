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
touched. A `chop_manifest.csv` records every file when an output folder is set.

## Options

| Option | Meaning | Default |
|---|---|---|
| Min black gap (s) | shortest black stretch that counts as a cut point | `0.10` |
| Black threshold (0–1) | pixel blackness cutoff — lower is stricter | `0.10` |
| Min clip length (s) | discard clips shorter than this | `0.50` |
| Frame-accurate | re-encode H.264 for exact boundaries (else lossless copy) | off |
| Mirror input structure | rebuild the source folder tree under the output | on |
| Preview only | plan and report clips without writing | on |

## Dependencies

- **ffmpeg** and **ffprobe** — found in the app's bundled `bin/` first, then PATH.

No OpenCV/numpy: this is a from-scratch ffmpeg rebuild of the original OpenCV
chopper, which re-encoded every frame to mpeg4 (large, lossy, slow).

## Verify

```
python -m tools.video_chopper.test_smoke
```

Synthesizes a real `content · black · content` sample and asserts the engine
cuts exactly two clips. Skips cleanly if ffmpeg isn't installed.

## Notes

- Stream-copy (default) snaps each cut to the nearest prior keyframe — imperceptible
  for black-gap splits and instant. Enable **Frame-accurate** when you need the
  boundary on an exact frame.

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
| audio (mp3/wav/flac/…) | `mp3 · wav · flac · aac · m4a · ogg · opus` |

**Documents** (optional libraries):
| From | To | Needs |
|---|---|---|
| `.md` | `html`, `pdf` | `markdown` (+ `xhtml2pdf` for pdf) |
| `.html` | `pdf` | `xhtml2pdf` |
| `.docx` | `html`, `pdf` | `mammoth` (+ `xhtml2pdf` for pdf) |
| `.pdf` | `png`, `jpg` (one image per page) | `pypdfium2` |
| `.pdf` | `txt` | `pypdfium2` |

Nothing is written until you turn off **Preview only**; originals are never
touched. A `convert_manifest.csv` records every file when an output folder is set.

All real conversions run through the shared durable queue. File converters write
to a format-preserving candidate, KS validates the candidate, and only then is it
atomically committed. FFmpeg, image-frame loops, PDF pages, hashing, and text
validation observe cancellation. A malformed input or unavailable optional
dependency is isolated to that item instead of stopping the batch.

Stored results are reused only while their exact planned artifact set still
matches:

- images reopen successfully with the recorded geometry, mode, frame count,
  byte count, and SHA-256;
- audio/video passes ffprobe duration validation plus exact hashing;
- PDF output has a PDF signature; HTML/text must be valid streaming UTF-8;
- PDF page directories contain exactly the numbered pages recorded by the job,
  with every page reopened and hashed.

PDF page rendering uses a staged sibling directory and commits the complete page
set at once. An existing page-output directory is not overwritten implicitly.
Choose another output folder or deliberately remove the old generated directory.
PDF jobs are capped at 10,000 pages and animated image preservation at 10,000
frames so report/checkpoint memory cannot grow without a defined ceiling.

## Options

| Option | Applies to | Default |
|---|---|---|
| JPEG/WebP quality | image + extract targets | 90 |
| GIF width / fps | video → gif | 480 / 12 |
| PDF→image DPI | pdf → png/jpg | 150 |
| Mirror input structure | all | on |
| Preview only | all | on |

When several input families are selected, the target menu prefers formats common
to every family. If no common target exists, the UI asks you to split the mixed
batch instead of queuing predictable per-file failures. Same-output collisions
between selected files are rejected before processing.

## Dependencies

- **Pillow** and the **bundled ffmpeg** — already part of the app; images and A/V work out of the box.
- Document conversions are **optional**: `pip install markdown xhtml2pdf mammoth pypdfium2`.
  Missing ones are reported per-file ("install X"), never a crash.

Document libraries are all permissive-licensed and pure-Python/prebuilt-wheel
(no system compiler or GUI toolkit). **PyMuPDF is intentionally avoided** — its
AGPL license would be viral for a distributed app; `pypdfium2` (Apache/BSD) does
the PDF rendering instead.

## Verify

```
python -m tools.format_converter.test_smoke
```

Tests dispatch, strict options, collisions, cancellation cleanup, exact image and
media reuse, corruption rejection, and page-set validation. Markdown and real
PDF render/text legs run when their optional dependencies are present and skip
cleanly otherwise.

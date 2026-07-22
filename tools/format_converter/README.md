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

## Options

| Option | Applies to | Default |
|---|---|---|
| JPEG/WebP quality | image + extract targets | 90 |
| GIF width / fps | video → gif | 480 / 12 |
| PDF→image DPI | pdf → png/jpg | 150 |
| Mirror input structure | all | on |
| Preview only | all | on |

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

Tests the dispatch table (no deps), the Pillow image leg, the ffmpeg A/V leg, and
the markdown document leg — each skipping cleanly if its dependency is absent.

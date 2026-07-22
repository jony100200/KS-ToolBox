<p align="center">
  <img src="assets/KSToolBox.png" width="128" alt="KS ToolBox">
</p>

<h1 align="center">KS ToolBox</h1>

<p align="center"><b>One window, many daily batch tools.</b><br>
A fast, portable, cross-platform desktop app that brings a set of practical
file / image / video / document batch utilities under a single UI.</p>

---

KS ToolBox is a **plugin toolbox**: one CustomTkinter shell that auto-discovers
self-contained tools. Every tool is built the same way — a **dry-run preview**, a
**mirror-the-input-folder** batch, atomic writes, a per-run manifest, and clear
errors instead of crashes. It is **deterministic-first**: only one tool uses an AI
model, and only where a model is genuinely the right tool.

## Tools

| | Tool | What it does |
|---|---|---|
| 🎞️ | **Video Compressor** | Shrink videos without visible quality loss — decides if a file is even worth re-encoding, then proves the result with a VMAF quality gate before touching the original. |
| ✂️ | **Video Chopper** | Split a video into clips at black-frame gaps (scene/take boundaries). Lossless stream-copy or frame-accurate re-encode. |
| 🧹 | **Clean Cutout** | Remove image backgrounds (u2net on ONNX Runtime) and clean the edge fringe. Optional green-screen despill. |
| ▦ | **Pixel Art Converter** | Turn images into clean pixel art — chunky pixels, reduced palette, sharp alpha. |
| ⤢ | **Image Rescale** | Batch-resize images four ways (longest-side, megapixels, scale factor, fit-inside) with snap-to-grid and upscale gating. |
| ➜ | **Format Converter** | Convert images ↔, audio/video (via ffmpeg), and documents (md/docx/html → pdf, pdf → image/text). |
| ⬡ | **To SVG** | Vectorize raster images to SVG (vtracer). |
| ▨ | **Icon Normalizer** | Trim, square-pad, and resize icons/sprites to a uniform canvas. |
| ▤ | **Texture Renderer** | Batch-export textures from Substance Designer (`.sbsar`) and Material Maker (`.ptex`) projects. |

Every batch tool **previews before it writes**, **never touches originals** unless
you explicitly opt in (with a confirmation), and writes a CSV manifest.

## Run it

**Portable build (no Python needed).** Grab/build the one-folder release and
double-click `KS ToolBox.exe` — a Python interpreter and all dependencies are
bundled. Copy the folder anywhere; it's self-contained.

**From source:**
```bash
pip install -r requirements.txt          # core (small)
pip install -r requirements-optional.txt # extras for the tools you use (optional)
python main.py
```
External binaries: **ffmpeg + ffprobe** power the video tools and Format
Converter's audio/video (bundle them in `bin/` or install on PATH).

## Build a portable release

```powershell
powershell -File build.ps1
```
Produces `dist/KS ToolBox/` — a portable folder with a bundled Python. See
`THIRD_PARTY_NOTICES.md` for FFmpeg's license obligations when redistributing.

## Design

- **Engine ≠ UI.** Each tool is pure headless logic (`engine.py`) + a thin UI
  (`panel.py`). Errors are values (a standard envelope), never crashes.
- **Deterministic-first.** 8 of 9 tools use no AI at all; Clean Cutout uses one
  specialist matting model. Nothing heavy loads at startup (~230 ms to open).
- **Lazy & portable.** A tool's heavy dependency loads only when you open it; a
  missing one is announced in-panel, never a startup failure.

Full analysis in [`docs/KS_TOOLBOX_OPTIMIZATION_AUDIT.md`](docs/KS_TOOLBOX_OPTIMIZATION_AUDIT.md).
Reproducible benchmarks in [`benchmarks/`](benchmarks/).

## Add a tool

Drop a folder in `tools/` exposing a module-level `TOOL` — discovery finds it, the
shell shows it, no other file changes. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

KS ToolBox's code is **MIT** (see [`LICENSE`](LICENSE)). Bundled/optional
third-party components carry their own licenses — notably a bundled FFmpeg build
with x264/x265 is GPL; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

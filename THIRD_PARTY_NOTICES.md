# Third-Party Notices

KS ToolBox's own code is MIT-licensed (see `LICENSE`). It builds on, bundles, or
optionally downloads the components below. **This matters for public
distribution** — especially FFmpeg. Verify each component's exact license for the
specific build you ship.

## Bundled with the app

| Component | Used by | Typical license | Note |
|---|---|---|---|
| **FFmpeg / ffprobe** (`bin/`) | Video Compressor, Video Chopper, Format Converter (A/V) | **GPL** if built with x264/x265 (as ours is); LGPL otherwise | ⚠️ **Redistributing a GPL FFmpeg binary carries GPL obligations for that binary** (you must offer its corresponding source and license text). This does NOT relicense KS ToolBox's own code. This is the same arrangement HandBrake and many apps use. Ship FFmpeg's `LICENSE`/`COPYING` and a source offer/link alongside the build. |
| **Font Awesome 6 Free (Solid)** (`assets/fa-solid-900.ttf`) | all icons | Icons **CC BY 4.0**, Font **SIL OFL 1.1**, code MIT | Attribution appreciated; no redistribution restriction. |
| **CustomTkinter** | the whole UI | MIT | — |
| **send2trash** | recoverable "delete original" | BSD-3-Clause | — |

## Optional Python dependencies (installed/downloaded on demand, per tool)

| Package | Tool | License |
|---|---|---|
| Pillow | image tools, Format Converter | MIT-CMU (HPND) — permissive |
| numpy | Clean Cutout | BSD-3-Clause |
| onnxruntime | Clean Cutout | MIT |
| rembg (+ u2net model) | Clean Cutout | MIT (code); the u2net model weights are Apache-2.0 — downloaded on first use |
| vtracer | To SVG | MIT |
| markdown | Format Converter (docs) | BSD-3-Clause |
| xhtml2pdf (+ reportlab, pypdf, html5lib) | Format Converter (→PDF) | Apache-2.0 |
| mammoth | Format Converter (DOCX) | BSD-2-Clause |
| pypdfium2 (PDFium) | Format Converter (PDF) | Apache-2.0 / BSD-3-Clause |

**Deliberately avoided:** PyMuPDF (AGPL-3.0 — viral for a distributed app). `pypdfium2`
provides PDF rendering under a permissive license instead.

**External CLIs (user-provided, NOT bundled):** Substance Designer `sbsrender` and
Material Maker `material_maker` are located by the user for the Texture Renderer
tool; KS ToolBox does not ship or relicense them.

If in doubt for a commercial or wide release, run a license scan over the exact
`requirements` set you freeze and the FFmpeg build you bundle.

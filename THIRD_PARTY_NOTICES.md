# Third-Party Notices

KS ToolBox's own code is MIT-licensed (see `LICENSE`). It builds on, bundles, or
can integrate with the components below. The build generates
`RELEASE_COMPONENTS.md`, `DEPENDENCY_MANIFEST.json`, and `SBOM.spdx.json` from
the actual PyInstaller analysis and bundled FFmpeg configuration. Those
generated files are the authority for a particular release; an unknown bundled
Python distribution stops the build.

## Bundled with the app

| Component | Used by | Licence | Note |
|---|---|---|---|
| **FFmpeg / ffprobe** (`bin/`) | Video Compressor, Video Chopper, Audio Tool, Format Converter (A/V) | Derived at build time; the current Gyan 8.1.2 full build is **GPL-3.0-or-later** | Keep the shipped GPL text, recorded configure flags, and equivalent no-charge access to the corresponding source beside every binary download. This requirement applies to FFmpeg; it does not change the licence of KS ToolBox's separate original code. |
| **Python and Tcl/Tk** | application runtime | PSF-2.0 / TCL | Exact runtime version and notices are copied into each release. |
| **PyInstaller bootloader** | portable executable | GPL-2.0-or-later WITH Bootloader-exception | The exact PyInstaller notice is copied into each release. |
| **Font Awesome 6 Free (Solid) font** (`assets/fonts/fa-solid-900.ttf`) | all icons | SIL OFL-1.1 | Only the font file is bundled; its exact upstream licence is copied into each release. |
| **CustomTkinter** | the whole UI | MIT | — |
| **send2trash** | recoverable "delete original" | BSD-3-Clause | — |
| **Pillow** | image decoding, transforms, and export | HPND | — |
| **NumPy** | Alpha Doctor, asset inspection, and texture analysis | BSD-3-Clause | — |

Transitive Python packages present in the actual frozen build are also listed in
the generated release inventory with their exact installed versions and copied
notices.

## Optional integrations (not bundled in the default portable build)

| Package | Tool | License |
|---|---|---|
| onnxruntime | Alpha Doctor optional inference runtime | MIT |
| Segmentation model weights | Alpha Doctor optional AI path | Not shipped; audit the exact model licence separately before any bundled distribution |
| vtracer | To SVG | MIT |
| markdown | Format Converter (docs) | BSD-3-Clause |
| xhtml2pdf (+ reportlab, pypdf, html5lib) | Format Converter (→PDF) | Apache-2.0 |
| mammoth | Format Converter (DOCX) | BSD-2-Clause |
| pypdfium2 (PDFium) | Format Converter (PDF) | Apache-2.0 / BSD-3-Clause |

**Deliberately avoided:** PyMuPDF (AGPL-3.0 copyleft). `pypdfium2` provides PDF
rendering under a permissive licence instead.

**External CLIs (user-provided, NOT bundled):** HandBrakeCLI, Substance Designer
`sbsrender`, Material Maker `material_maker`, and other user-selected workers are
located at runtime; KS ToolBox does not ship or relicense them.

This file is an engineering compliance record, not legal advice. Re-run the
release gate and review its generated inventory for every public artifact.

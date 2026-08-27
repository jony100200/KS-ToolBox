# Third-Party Notices

KS ToolBox's own code is MIT-licensed (see `LICENSE`). This notice records the
components shipped with, or optionally used by, the app. Each portable release
also generates `RELEASE_COMPONENTS.md`, `DEPENDENCY_MANIFEST.json`, and
`SBOM.spdx.json` from its actual PyInstaller analysis and bundled FFmpeg build.

## Bundled with the app

| Component | Used by | Licence | Note |
|---|---|---|---|
| **FFmpeg / ffprobe** (`bin/`) | Video Compressor, Video Chopper, Audio Tool, Format Converter (A/V) | BtbN `win64-lgpl`, verified as **LGPL-3.0-or-later** at build time | The release inventory records the exact build, configure flags, binary hashes, and source evidence and includes the matching LGPL text. Video Compressor uses SVT-AV1 or AV1 NVENC. |
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
| Segmentation model weights | Alpha Doctor optional AI path | Not shipped with the default portable build |
| vtracer | To SVG | MIT |
| markdown | Format Converter (docs) | BSD-3-Clause |
| xhtml2pdf (+ reportlab, pypdf, html5lib, pyHanko) | Format Converter (→PDF) | Apache-2.0 |
| svglib | Format Converter (→PDF, via xhtml2pdf) | **LGPL-3.0-or-later** |
| python-bidi | Format Converter (→PDF, via xhtml2pdf) | **LGPL** |
| mammoth | Format Converter (DOCX) | BSD-2-Clause |
| pypdfium2 (PDFium) | Format Converter (PDF) | Apache-2.0 / BSD-3-Clause |

**External CLIs (user-provided, NOT bundled):** HandBrakeCLI, Substance Designer
`sbsrender`, Material Maker `material_maker`, and other user-selected workers are
located at runtime; KS ToolBox does not ship or relicense them.

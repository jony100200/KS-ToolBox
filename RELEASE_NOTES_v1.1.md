# KS ToolBox v1.1.0 Release Notes

**Kalponic Studio ToolBox v1.1.0 — Unified Workflows, Bounded Batch Concurrency & Pure MIT Licensing**

> Free, local-first creator workstation combining 23 offline tools for video, audio, graphics, gamedev, and AI dataset preparation. No cloud subscriptions, zero telemetry, non-destructive batch pipelines.

---

## 🌟 Highlights in v1.1.0

### 1. Unified Workflow MVP (Workflow B: Image & Web Delivery)
Rather than manually running files through separate tools (e.g. Resizing &rarr; Background Cutout &rarr; Metadata Scrubbing), KS ToolBox now features an automated multi-stage pipeline orchestrator:
- **Zero-Friction Chaining:** Automatically stages files through `Image Rescale` &rarr; `Alpha Doctor` &rarr; `Metadata Scrubber`.
- **Non-Destructive Staging:** Intermediary steps run inside an isolated, temporary scratch space. Originals are never touched; only final validated assets are moved to your target destination.
- **Auditable Manifests:** Generates atomic `workflow_manifest.csv` and `workflow_summary.json` capturing execution metrics, file sizes, and step timings.
- **Built-in Presets:**
  - `web_delivery`: Fit to 1200px max edge, auto-solid background removal, full metadata scrub.
  - `transparent_cutout`: Fit to 2048px, chroma key transparency, defringe, keep ICC profiles.
  - `clean_rescale`: Longest-side 1920px resize + EXIF strip.
  - `privacy_only`: Zero image re-encoding, pure byte-level metadata scrub.
- **Integrated UI:** Dedicated `Workflows` tab in `ToolBoxShell` with real-time preview of planned stages, input counts, and live batch progress (`Ctrl+2` hotkey).

---

### 2. Bounded Concurrency & Multi-Worker Batch Acceleration
Batch operations across large asset sets now leverage multi-threaded CPU pipelines without sacrificing crash-safety:
- **Thread Pool Orchestration:** `BatchRunner` supports bounded concurrent workers (`max_workers=N`) via `ThreadPoolExecutor`.
- **Thread-Safe SQLite Transactions:** Heavy image processing and file I/O execute concurrently on background threads, while SQLite lifecycle transitions (`begin_item`, `finish_item`) remain serialized on the main runner thread to prevent database locking or corruption.
- **Empirical Benchmarks (100-item 512x512 Image Rescaling):**
  - **1 Worker (Serial Fallback):** 0.67s (149.1 items/sec, 1.00x)
  - **2 Workers:** 0.23s (439.4 items/sec, 2.95x speedup)
  - **4 Workers:** 0.13s (787.1 items/sec, 5.28x speedup)
  - **8 Workers:** 0.10s (1042.5 items/sec, 6.99x speedup)
  - **Integrity:** 100/100 outputs verified with byte-level fidelity.
- **Hardware-Preserving Fallback:** Retains `max_workers=1` as the default fallback for low-spec machines, single-core CPUs, or constrained memory environments.

---

### 3. Pure MIT Icon Architecture (Tabler Icons)
- Replaced Font Awesome with **Tabler Icons TTF**.
- Eliminated all CC-BY-4.0 attribution requirements from third-party font notices.
- The entire application core and icon layer is now **100% pure MIT-licensed**, simplifying commercial and open-source redistribution.

---

### 4. 23 Verified Industrial Tools
All 23 tools are verified, regression-tested, and available in the portable shell:

| Category | Tools Included |
| :--- | :--- |
| **Video & Audio** | Video Compressor, Video Chopper, Audio Tool, Voice Recorder, YouTube Downloader |
| **Graphics & 2D** | Image Rescale, Format Converter, Alpha Doctor, Image Enhancer, Lossless Compressor, Metadata Scrubber, Pixel Art Converter, To SVG, Icon Normalizer, Showcase |
| **Game Dev & 3D** | Material Converter (PBR / ORM / MOS), Image to PBR, Sprite Viewer, Tileset Checker, Texture Renderer, Package Extractor, Unity Packager |
| **Datasets & System** | Asset Auditor, Dataset Manager |

---

## 🧪 Verification & Test Suite
- **pytest:** 58/58 tests passing (100% pass rate in 5.2s).
- **Compliance Suite:** `benchmarks/build_release_compliance.py` verifies all package policies, licenses, and tool exports.
- **Crash Recovery:** SQLite WAL durable recovery tested across simulated process termination and quarantine scenarios.

---

## 📦 How to Run

### From Source
```bash
git clone https://github.com/jony100200/KS-ToolBox.git
cd KS-ToolBox
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Portable Package
Run `build.ps1` in PowerShell to generate the standalone single-folder release:
```powershell
.\build.ps1
```
The packaged binary will be built to `dist/KS ToolBox/`.

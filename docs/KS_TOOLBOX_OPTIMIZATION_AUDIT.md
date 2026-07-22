# KS ToolBox — Deterministic-First Architecture & Optimization Audit

**Date:** 2026-07-22 · **Scope:** entire repository · **Method:** full-source inspection + real measurements (no assumptions). Every number below was measured on this machine; the scripts are in `benchmarks/`.

---

## Executive summary

KS ToolBox is **already a deterministic-first, cartridge-disciplined application.** It is a 2,900-LOC CustomTkinter shell that auto-discovers self-contained tool plugins. Of five shipped tools, **four are 100 % deterministic** (ffmpeg / Pillow), and the fifth (Clean Cutout) uses **one specialist ONNX matting model** — which is the *correct* level for that job (a background matte is not something a rule or an LLM should do). There are **zero** LLM/VLM calls, **zero** network calls, **zero** GPU libraries, and **nothing** heavy is loaded at startup.

So the headline finding is not "too much AI" — there is essentially none to remove. The headline finding is **package weight**: two dependency clusters account for virtually the entire installed footprint, and both can be cut hard **without losing any capability**.

| The whole app's disk cost is two numbers | Measured |
|---|---|
| Bundled `ffmpeg.exe` + `ffprobe.exe` | **462 MB** (231 + 231) |
| Clean Cutout's Python dependency stack | **358 MB** (of which **~237 MB is the `pymatting`→`numba`→`llvmlite`/`scipy`/`scikit-image` chain**, pulled in only for optional alpha-matting) |
| Everything else (code + base deps + assets + font) | **~4 MB** |

**Top 5 highest-value, lowest-risk moves** (detail in `KS_TOOLBOX_OPTIMIZATION_PLAN.md`):

1. **Replace the `rembg` wrapper with direct ONNX Runtime inference** of the same u2net model → drops ~237 MB, removes the Python-3.12 `llvmlite` build fragility, **identical output** (we already own the defringe). *High value, medium risk.*
2. **Ship a shared/essentials ffmpeg build** (ffmpeg+ffprobe sharing DLLs, only the codecs we use: x265, vmaf, vpx, mp3lame, aac) → ~462 MB down to ~120–150 MB. *High value, medium risk — must verify codecs.*
3. **Content-addressed cache** for ffprobe metadata, VMAF scores, and mattes keyed on `(content-hash + settings + tool version)` → re-runs skip recompute and AI entirely. *High value, low risk.*
4. **Exclude the 1.5 MB icon source** (`KSToolBox_1024.png`) and other build-only files from the PyInstaller bundle. *Trivial, zero risk.*
5. **Extend batch resumability** from "output exists" to a content-hash manifest so changed settings/inputs invalidate correctly. *Medium value, low risk.*

Nothing in this audit recommends removing a feature, weakening output, or adding a cloud/Docker/AI dependency.

---

## Current architecture

```
main.py ── discover() ──> ToolRegistry ──> ToolBoxShell (sidebar + lazy content)
   │                          ▲                     │
   │                          │ scans tools/*/      │ build_panel() on first open
   ▼                          │                     ▼
toolbox/            tools/<name>/               tools/<name>/panel.py  (thin UI, worker thread)
  tool.py  (contract)  __init__.py -> TOOL          │ calls
  shell.py (UI host)   tool.py     (meta+build)     ▼
  discovery.py         panel.py    (BaseBatchPanel) tools/<name>/engine.py (pure logic, headless)
  components.py        engine.py   (pure)              │ uses
  theme.py             test_smoke.py                   ▼
  icons.py                                          toolbox/engine_common.py
  batch_panel.py  (shared UI base)                    (envelope, resolve_tool, run_cmd)
  engine_common.py (shared logic)                   external: bundled bin/ffmpeg  ·  rembg/ONNX (Clean Cutout only)
```

Two layers, strictly separated: **engine** (pure, no CustomTkinter, error-envelope, headless, unit-testable) and **panel** (CustomTkinter only, work on a worker thread). The shell knows nothing about any tool's internals. Adding a tool = dropping a folder. This is a clean cartridge model.

---

## Phase 1 — Repository inventory

Measured: **34 Python files, 2,908 LOC** (toolbox 351 + tools 2,543, plus the two new shared modules). Source tree excl. venv/dist ≈ 4 MB of code+assets (the 680 MB figure is dominated by `bin/`).

| Component | Purpose | Loaded when | Disk | Runtime cost | Deterministic | AI | Recommendation | Risk |
|---|---|---|---|---|---|---|---|---|
| `main.py` | entry point | startup | ~1 KB | negligible | Yes | — | keep | — |
| `toolbox/shell.py` | window, sidebar, lazy content, app icon, `_safe_build` | startup | ~7 KB | 232 ms total startup | Yes | — | keep | — |
| `toolbox/discovery.py` | scan `tools/`, isolate broken plugins | startup | ~1 KB | 34 ms for 5 tools | Yes | — | keep | — |
| `toolbox/tool.py` | Tool protocol + registry | startup | ~2 KB | negligible | Yes | — | keep | — |
| `toolbox/theme.py`,`components.py`,`icons.py` | palette, widgets, FontAwesome loader | startup | ~10 KB + 416 KB font | font register once | Yes | — | keep | — |
| `toolbox/batch_panel.py` | **shared** file-picker/run/results base (new) | first tool open | ~7 KB | negligible | Yes | — | keep — good dedup | — |
| `toolbox/engine_common.py` | **shared** envelope/binary/subprocess (new) | first engine call | ~3 KB | negligible | Yes | — | keep — good dedup | — |
| `tools/video_compressor` | VMAF-gated smart re-encode | on open / job | ~14 KB | ffmpeg subprocess | Yes (ffmpeg) | — | keep | — |
| `tools/video_chopper` | split at black gaps | on open / job | ~6 KB | ffmpeg subprocess | Yes (ffmpeg blackdetect) | — | keep | — |
| `tools/pixel_art` | pixelize + palettize | on open / job | ~5 KB | Pillow, in-proc | Yes (Pillow) | — | keep | — |
| `tools/image_rescale` | 4-mode batch resize | on open / job | ~9 KB | Pillow, in-proc | Yes (Pillow) | — | keep | — |
| `tools/clean_cutout` | background removal + defringe | on open / job | ~10 KB | **ONNX matte** + numpy/PIL | **Mixed** (Level-2 model + deterministic edge math) | **rembg / u2net** | optimize (see Plan #1) | Med |
| `bin/ffmpeg.exe` | encode/probe/detect | on job | **231 MB** | subprocess | Yes | — | **optimize (shared build)** | Med |
| `bin/ffprobe.exe` | metadata | on job | **231 MB** | subprocess | Yes | — | **optimize (shared build)** | Med |
| `assets/fa-solid-900.ttf` | icon font | startup | 416 KB | register once | Yes | — | keep | — |
| `assets/KSToolBox.ico/.png` | app icon | startup | 98 + 65 KB | — | Yes | — | keep | — |
| `assets/KSToolBox_1024.png` | icon **source** for `.ico` regen | never at runtime | **1.5 MB** | — | Yes | — | **exclude from bundle** | Low |

**No** databases, background threads (beyond the per-job worker), scheduled tasks, network integrations, caches, model files (rembg downloads u2net on first use to `~/.u2net`), or update mechanisms exist. Build/packaging: `KS ToolBox.spec` (PyInstaller one-folder windowed), launchers (`.cmd`, `.vbs`), `requirements.txt`.

---

## Phase 2 — Feature-to-mechanism map

| Feature | Operation | Level | Mechanism | AI justified? |
|---|---|---|---|---|
| Video Compressor | probe metadata | **L0** | ffprobe (deterministic) | n/a |
| Video Compressor | "already efficient?" decision | **L1** | bits-per-pixel heuristic + codec table | n/a |
| Video Compressor | re-encode | **L0** | ffmpeg libx265/nvenc (deterministic transform) | n/a |
| Video Compressor | quality gate | **L1** | VMAF (deterministic metric) | n/a |
| Video Chopper | find scene breaks | **L1** | ffmpeg `blackdetect` (deterministic) | n/a |
| Video Chopper | cut clips | **L0** | ffmpeg stream-copy / re-encode | n/a |
| Pixel Art | pixelize + palette | **L0/L1** | Pillow NEAREST + median-cut | n/a |
| Image Rescale | compute size + resample | **L0** | pure math + Pillow | n/a |
| Clean Cutout | **subject matte** | **L2** | u2net ONNX (specialist model) | **Yes** — pixel-accurate salient-object matting is not achievable by rules/thresholds at acceptable quality; a *specialist* model (not an LLM/VLM) is exactly the right tool |
| Clean Cutout | despill / defringe | **L0** | numpy + Pillow morphology (deterministic) | n/a |

**Answer to "which features use AI unnecessarily": none.** The single model is a Level-2 specialist doing a job Levels 0–1 cannot. This already satisfies the governing rule.

---

## Phase 3 — AI-call audit

**Every AI call site, exhaustively** (sweep in `benchmarks/measure_ai_calls.ps1`):

| # | Location | Trigger | Model | Load cost | Resident? | Cached? | Output validated? | Deterministic alt? |
|---|---|---|---|---|---|---|---|---|
| 1 | `clean_cutout/engine.py: remove_background()` (`from rembg import remove/new_session`) | user runs Clean Cutout, per image | u2net (or u2netp/isnet/birefnet) ONNX | model download once (~176 MB to `~/.u2net`), session build per model | **session cached per model** for the batch (`_SESSIONS`) — good; freed on process exit | **matte NOT cached across runs** — re-processing the same image re-runs inference | Yes — `alpha_coverage` rejects empty mattes; downstream defringe is deterministic | No equal-quality deterministic alternative for the matte itself |

There are **no other AI, network, LLM, OCR, transcription, embedding, or image-generation calls** in the codebase. `requests`/`urllib`/`socket`/`torch`/`llama`/`comfy`/`whisper`: **zero matches.**

**Avoidable inference:** the one real opportunity is **caching the matte by content hash** (Phase 5). A user who re-runs a folder (added one new image, or re-ran after a crash) currently re-infers every image. With a content-addressed cache keyed on `(sha256(input) + model + alpha_matting + rembg/model version)`, unchanged images skip inference entirely. This is the highest-value AI-call reduction and it is **deterministic and safe** (the key includes the model identity, so it never serves a stale matte).

---

## Phase 4 — Batch-processing audit

All five tools already implement the good batch shape: **discover → dry-run preview → per-file process → atomic `.part` write → manifest CSV → per-file failure isolation.** Cancellation (`_stop` Event), progress, and mirroring exist. `BaseBatchPanel` now shares this across tools.

Gaps vs. the ideal graph:

| Ideal stage | Present? | Note |
|---|---|---|
| content-identity skip | **partial** | Compressor/Chopper skip when *output exists*; none skip by *content hash* |
| group compatible ops | n/a | each file independent; no fusion opportunity |
| route only uncertain items to AI | **partial** | Clean Cutout runs the model on every image; a cache would route only *new* images to inference |
| retry failed only | **no** | a re-run reprocesses everything except existing outputs |
| serial vs parallel | **serial** | one worker thread; files processed sequentially |
| commit atomically | **yes** | `.part` + `os.replace` everywhere |
| checkpoint/resume | **partial** | resume = "skip existing output"; no mid-file checkpoint (correct — files are atomic units) |

**Concurrency:** currently one worker thread per tool, files serial. For CPU-bound Pillow tools (Pixel Art, Image Rescale) and independent ffmpeg jobs, a **bounded process/thread pool** (size = measured throughput, not blind `os.cpu_count()`) would cut wall-clock on large batches. This is *experimental* — ffmpeg is already internally multi-threaded, so parallel ffmpeg jobs can cause disk/CPU contention; must be measured before adopting. See Plan (experimental).

---

## Phase 5 — Content-addressed caching (biggest structural opportunity)

Nothing is cached across runs today. High-value, reusable intermediates:

| Intermediate | Cost to recompute | Cache key | Deterministic? |
|---|---|---|---|
| ffprobe metadata (codec, bitrate, fps, duration) | one subprocess/file | `sha256(file) + ffprobe version` | Yes |
| VMAF score of an encode | a full decode+compare pass | `sha256(src) + sha256(out) + libvmaf version` | Yes |
| Clean Cutout matte | one ONNX inference | `sha256(src) + model + alpha_matting + model version` | **model-versioned** (safe) |

A tiny sidecar cache (a `.kstoolbox_cache/` JSON or sqlite keyed by the hashes) would make re-runs near-instant and eliminate redundant inference. **Provenance is mandatory in the key** — for the matte, the model name and version are part of the key so a model change invalidates correctly (the spec's "do not cache nondeterministic outputs as deterministic" rule is satisfied because the key pins the exact model). Low risk, high reward.

---

## Phase 6 — Incremental execution

The unit of work is one file, and files are independent (no cross-file global result). So incrementality reduces to **"skip files whose (content, settings) already produced a valid output"** — i.e. Phase 5's cache plus the existing manifest. There is no dependency graph to track between files. Recommendation: fold incremental behavior into the content-hash manifest (Plan, medium-term). "1 of 1,000 files changed → 1 reprocessed" falls out for free.

---

## Phase 7 — Procedure reuse

Each tool already *is* a parameterized procedure (`process(path, Options)`), and the panels persist settings within a session. There is **no AI-generated workflow to promote** — the tools are hand-written deterministic procedures, which is the end state Phase 7 aims for. The only enhancement worth considering is **saved presets** (name a set of options, e.g. "PNG→1024 longest-side", reload later). Low priority, pure UX. No procedure-learning system is warranted (there is no ambiguous NL request layer to learn from).

---

## Phase 8 — Dependency & package audit

Measured with `uv pip install --target` + directory sizing (`benchmarks/measure_deps.ps1`):

**Base (shell + deterministic tools): 1.3 MB** — `customtkinter` + `send2trash`. Minimal and justified.

**Clean Cutout stack: 358.6 MB.** Breakdown:

| Package | MB | Needed for the matte itself? |
|---|---|---|
| **llvmlite** | **115.3** | **No** — pulled by `numba` ← `pymatting` (alpha-matting only) |
| **scipy** (+libs 19) | **83** | **No** — via `scikit-image`/`pymatting` |
| onnxruntime | 37.7 | **Yes** (inference backend) |
| scikit-image | 21 | **No** — via `pymatting` |
| numpy (+libs 20) | 39 | **Yes** (array ops) |
| PIL | 14 | **Yes** (already shipped anyway) |
| numba | 11.5 | **No** — `pymatting` |
| networkx | 6.6 | **No** — via `scikit-image` |

**~237 MB (llvmlite + scipy + scikit-image + numba + networkx) exists solely to serve `rembg`'s optional alpha-matting path.** The actual matte needs only `onnxruntime + numpy + Pillow` (~90 MB). Two ways out, both preserving capability:

- **Preferred:** call ONNX Runtime directly on the u2net model (rembg is a thin wrapper); we already do our own defringe, so we don't need `pymatting` at all. Drops ~237 MB and removes the Python-3.12 `llvmlite` build failure documented in `requirements.txt`.
- **Alternative:** keep `rembg` but drop the alpha-matting toggle and its `pymatting` extra; slightly softer edges recovered by our feather. Smaller code change, similar saving if the deps can be excluded.

No duplicate image libs / HTTP clients / archivers / model runtimes exist. Nothing dev/test-only is shipped (`test_smoke.py` files are tiny and skip cleanly; exclude from the bundle for cleanliness).

---

## Phase 9 — Startup & lazy loading

**Measured startup: 232 ms** headless-ready (customtkinter import 198 ms + discover 5 tools & shell import 34 ms). What loads at startup: CustomTkinter, the FontAwesome font, the tool **registry** (metadata only — panels build lazily on first open). What does **not** load at startup: rembg, ONNX, numpy, Pillow, ffmpeg, any model, any GPU runtime, any network/update check. Discovery is tolerant (`engine_common`/`batch_panel` import cleanly; a tool with missing heavy deps still registers because `tool.py` defers the panel import). **This phase is already correct** — the app opens without initializing anything heavy. No action.

---

## Phase 10 — Resource scheduling

The app runs one job at a time from one worker thread; ffmpeg manages its own internal threading. There is **no GPU residency, no model pool, no multi-job scheduler** — and for a single-user desktop utility that is the right amount of machinery (the spec warns against maximizing concurrency blindly). If batch parallelism is later added (Phase 4), it needs a **bounded, resource-aware pool** (separate caps for CPU-bound Pillow work vs. already-threaded ffmpeg vs. the single ONNX session), not naive fan-out. Deferred / experimental.

---

## Phase 11 — Reliability audit

Strong already: **dry-run default** on every tool, **atomic writes** (`.part` → `os.replace`) everywhere, **per-file failure isolation** (one bad file fails its own `Result`, batch continues), **cancellation**, **manifests** for provenance, **VMAF gate + never-delete-before-verify** in the compressor, **Recycle Bin** deletes via send2trash with an explicit Confirm dialog. Simulated failure modes: crash mid-batch → completed files intact (atomic), partial file is a `.part` that's ignored/overwritten on resume; missing ffmpeg/rembg → clean `dep.missing` envelope, no crash; malformed input → per-file `failed`. **Gap:** stray `.part` files after a hard kill are not swept on next run (harmless but untidy) — add a cleanup pass. Low priority.

---

## Phase 12 — Security audit

| Surface | Status |
|---|---|
| Subprocess construction | **Safe** — list-form args, no `shell=True`, `CREATE_NO_WINDOW`; no user string is ever concatenated into a shell command |
| Path handling | pathlib throughout; mirror uses `relative_to` guarded by `try/except`; `commonpath` for input root |
| Archive extraction / zip-bombs | **N/A** — no archive tool exists yet (revisit if Format Converter or an extractor is added) |
| Model-generated paths/commands | **N/A** — the one model outputs pixels, never paths or commands; nothing executes model output |
| Network | **None** — no outbound calls (rembg's model download is the only network touch, on first use, to its own source) |
| Secrets / API keys | **None** — no cloud, no keys |
| Native binaries | bundled ffmpeg (trusted build); document its provenance/license in the release |
| Path traversal on output | mirror keeps outputs under `out_root`; a maliciously crafted `input_root` can't escape because `relative_to` fails closed |

**No unrestricted shell execution, no eval, no injectable command paths.** Posture is good for a local desktop tool. The one forward-looking note: any future tool that extracts archives or accepts model-suggested file operations must add a deterministic policy layer (allowed paths/extensions, decompression limits).

---

## Phase 13 — Functionality preservation

Every recommendation in the Plan is **capability-preserving**: the ONNX-direct swap produces the same matte from the same model; the ffmpeg shared build keeps every codec the tools invoke; caching only *skips* recompute of identical work; excluding the icon source removes a build artifact, not a feature. Where a change *could* alter output (e.g. dropping alpha-matting), the Plan marks it and keeps the original path behind a toggle until evidence justifies otherwise.

---

## Benchmark results (baseline)

| Metric | Value | Source |
|---|---|---|
| Python files / LOC | 34 / 2,908 | `measure_repo.ps1` |
| Startup (headless-ready) | **232 ms** | `measure_startup.py` |
| — customtkinter import | 198 ms | " |
| — discover 5 tools + shell import | 34 ms | " |
| Base install footprint | **1.3 MB** | `measure_deps.ps1` |
| Clean Cutout install footprint | **358.6 MB** | " |
| — recoverable (pymatting chain) | **~237 MB** | " |
| Bundled ffmpeg + ffprobe | **462 MB** | `measure_repo.ps1` |
| AI call sites | **1** (specialist ONNX) | `measure_ai_calls.ps1` |
| Network call sites | **0** | " |
| Models loaded at startup | **0** | `measure_startup.py` |

---

## Scorecard (0–5)

| Category | Current | Target | Evidence |
|---|---|---|---|
| Deterministic-first | **5** | 5 | 4/5 tools pure; the 1 model is a justified specialist; no LLM/network |
| Dependency efficiency | **2** | 4 | base is 1.3 MB, but Clean Cutout drags 358 MB (~237 MB unnecessary) |
| Lazy loading | **5** | 5 | 232 ms startup, nothing heavy loaded, discovery tolerant |
| Batch efficiency | **3** | 4 | good shape; no content-hash skip; serial only |
| Cache effectiveness | **1** | 4 | nothing cached across runs |
| Incremental execution | **2** | 4 | "skip existing output" only |
| Memory efficiency | **4** | 4 | one job at a time; session reused; no leaks (handle-leak fixed) |
| Reliability | **4** | 5 | dry-run/atomic/isolation/gate; minor `.part` sweep gap |
| Security | **4** | 4 | list-form subprocess, no shell/eval/network; add policy layer only if extractors arrive |
| Function preservation | **5** | 5 | all proposed changes preserve capability |
| Portability | **4** | 5 | PyInstaller bundles Python (no user install); size is the only blemish |
| Maintainability | **5** | 5 | 2,900 LOC, engine/UI split, shared bases, smoke tests per tool |

---

## Highest-value opportunities (ranked)

1. **ONNX-direct matte** — −237 MB, removes build fragility, no quality loss. *(Plan: medium-term)*
2. **Shared ffmpeg build** — −~320 MB. *(Plan: medium-term, verify codecs)*
3. **Content-addressed cache** — eliminates redundant probe/VMAF/inference on re-runs. *(Plan: medium-term)*
4. **Bundle hygiene** — exclude `KSToolBox_1024.png` (1.5 MB) + `test_smoke.py` from PyInstaller. *(Plan: immediate)*
5. **Content-hash resumable manifest** — true incremental batches. *(Plan: medium-term)*
6. **`.part` sweep on startup of a job** — reliability polish. *(Plan: immediate)*

Combined, #1 + #2 take a full portable bundle from an estimated **~820 MB → ~260 MB** with **no capability loss.**

---

## Rejected optimization ideas

- **Replace ffmpeg with a pure-Python encoder** — rejected: no pure-Python encoder matches x265/VMAF quality; violates "don't replace a reliable component with a fragile custom one."
- **Drop ffprobe, parse containers ourselves** — rejected: fragile, reinvents a mature parser for marginal size (ffprobe shrinks anyway under the shared-build plan).
- **One-file PyInstaller build** — rejected as default: unpacks a ~460 MB payload to temp on every launch; one-folder is faster and truly portable.
- **PyMuPDF for any future PDF work** — rejected preemptively: **AGPL-3.0** would force the whole app under AGPL; use `pypdfium2` (Apache/BSD).
- **Quantize/prune the u2net model to shrink it** — deferred: the 176 MB model is a runtime download, not part of the package; quantization risks matte quality and needs evidence first.
- **Naive parallel batch (`os.cpu_count()` workers)** — rejected: ffmpeg is already multi-threaded; blind fan-out causes disk/VRAM/CPU contention. Only a *measured, bounded* pool is acceptable (experimental).
- **Remove `send2trash`, hard-delete** — rejected: recoverable delete is a safety feature, and it's 30 KB.

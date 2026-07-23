# KS ToolBox — Optimization Plan

Companion to `KS_TOOLBOX_OPTIMIZATION_AUDIT.md`. Every item preserves capability. Ordered by risk. **Nothing high-risk is implemented before it is documented, prototyped, benchmarked, and approved** (per the implementation policy).

---

## Opportunity table

| Opportunity | Current behavior | Proposed behavior | Functionality impact | Disk saving | RAM saving | Runtime saving | AI-call reduction | Complexity | Risk | Deps affected | Tests required | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A. Exclude icon source + tests from bundle** | `KSToolBox_1024.png` (1.5 MB) + `test_smoke.py` shipped in dist | PyInstaller `datas` excludes build-only files | none | ~1.6 MB | — | — | — | Low | **Low** | none | build boots; icon still shows | **Implement** |
| **B. `.part` sweep before a job** | orphaned `.part` after a hard kill linger | job start removes stale `.part` in its output dir | none (safer) | — | — | — | — | Low | **Low** | none | crash-recovery bench | **Implement** |
| **C. Direct compact ONNX matte** | historical `rembg` proposal | Alpha Doctor uses opt-in U2NetP/U2Net directly through ONNX Runtime; no rembg stack ships | implemented local matte route | avoids the old heavy rembg chain | lower import cost | lazy model/runtime load | — | Med | **Low** | onnxruntime + Pillow/numpy | Alpha Doctor deterministic and optional-model smoke | **Implemented** |
| **D. LGPL FFmpeg AV1 build** | two Gyan GPL static exes (484.8 MB) | BtbN `win64-lgpl` static pair with SVT-AV1, AV1 NVENC and VMAF; GPL codecs disabled | AV1 replaces x265 while VMAF gate/output workflow remain | **256.7 MB (52.9%)** | — | — | — | Med | **Low** | bundled `bin/` | compressor + chopper + full Toolbox smoke, release-license gate | **Implemented** |
| **E. Content-addressed cache** | probe/VMAF/matte recomputed every run | sidecar cache keyed on `hash(input)+settings+tool/model version` | none (only skips identical work) | small cache cost | — | **large on re-runs** | **skips inference on unchanged images** | Med | **Low** | none (new module) | cache hit/miss + invalidation bench | **Implement (medium-term)** |
| **F. Content-hash resumable manifest** | resume = "output file exists" | manifest records input hash+settings; changed settings/inputs reprocess, rest skip | none (more correct) | — | — | medium on re-runs | — | Med | **Low** | none | incremental bench (1/1000 changed) | **Implement (medium-term)** |
| **G. Bounded parallel batch** | one worker, files serial | measured, resource-aware pool (separate CPU / ffmpeg / ONNX caps) | none | — | higher peak (bounded) | **large on CPU-bound batches** | — | High | **Med-High** | none | throughput bench vs serial; contention check | **Prototype only** |
| **H. Saved presets** | settings live for the session | name/reload option sets | none (additive) | — | — | — | — | Low | **Low** | none | UX | **Defer (nice-to-have)** |

The earlier combined-size estimate is historical. Package size is release-artifact
evidence, not a source-tree estimate; run the package measurement after a build.

---

## Immediate low-risk improvements (safe to implement now)

These meet all criteria for immediate action (behavior understood, tests exist, capability preserved, trivial rollback, measurable benefit):

- **A — Bundle hygiene.** In `KS ToolBox.spec`, exclude `assets/KSToolBox_1024.png` and `tools/**/test_smoke.py` from `datas`/analysis. Keep `.ico`, `.png` (256), and the font. *Rollback:* revert the spec change. *Benefit:* smaller, cleaner dist; the icon still resolves (shell prefers `.ico`, falls back to the 256 `.png`).
- **B — Stale `.part` sweep.** At the start of a batch, delete stale `*.part` / `*.part.*` temp files under the output root (they are always incomplete writes we own). *Rollback:* remove the sweep call. *Benefit:* clean recovery after a hard kill; no orphaned temp files.
- **Doc hygiene (complete).** The obsolete rembg/numba guidance was removed from
  the active optional requirements after the direct ONNX route shipped.

---

## Medium-term architectural improvements (controlled refactor + benchmark first)

- **C — Direct compact ONNX matte (implemented).** Alpha Doctor uses the local
  U2NetP/U2Net route through ONNX Runtime, with explicit model-download consent,
  checksums, deterministic post-processing, and optional-model smoke coverage.
  No rembg compatibility layer is retained.
- **D — LGPL FFmpeg AV1 build (implemented).** The 2026-07-23 BtbN `win64-lgpl` pair replaces Gyan's GPL pair. It provides `libsvtav1`, `av1_nvenc`, `libvmaf`, and the codecs needed by the other video/audio tools while disabling `libx264` and `libx265`. The compressor now outputs AV1; the quality and size gates are unchanged. **Validation:** Video Compressor produced a 74% smaller AV1 result at VMAF 98.5; Video Chopper and all 18 tool smokes passed. *Rollback:* obtain and validate a replacement pair in a temporary staging location; do not keep GPL binaries in the live tree.
- **E — Content-addressed cache.** New `toolbox/cache.py`: `get(key)`/`put(key, value)` over a per-output-root `.kstoolbox_cache/` (json or sqlite). Wire ffprobe, VMAF, and matte results through it. Key **must** include tool version + settings + input content hash + (for the matte) model name+version. **Validation:** hit/miss + invalidation benchmark; verify a model/settings change invalidates.
- **F — Content-hash resumable manifest.** Extend the existing manifest to store input hash + settings hash; on re-run, skip a file only if both match an existing valid output. Gives true incremental batches ("1 of 1,000 changed → 1 reprocessed").

---

## Experimental improvements (prototype + measure before adoption)

- **G — Bounded parallel batch.** A resource-aware pool with *separate* limits for CPU-bound Pillow work (Pixel Art, Image Rescale), already-threaded ffmpeg jobs (parallelism here often *hurts* — measure), and the single ONNX session (serialize). Must be proven faster than serial on representative batches without disk/CPU/VRAM contention. Do **not** default to `os.cpu_count()` fan-out.
- **Model quantization for u2net** — only if the runtime model download size becomes a concern; needs matte-quality evidence first.

---

## Rejected approaches (would reduce functionality / reliability / portability)

- Pure-Python video encoder instead of ffmpeg — quality/reliability loss.
- Dropping ffprobe and hand-parsing containers — fragile; **D** removes its size anyway.
- One-file PyInstaller as the default — slow cold start (unpacks ~460 MB to temp each launch).
- **PyMuPDF** for any future PDF feature — **AGPL-3.0**, viral for a distributed app; use `pypdfium2` (Apache/BSD).
- Blind `os.cpu_count()` parallelism — contention; only bounded/measured pools allowed.
- Removing `send2trash` — recoverable delete is a safety feature (30 KB).
- Mandatory cloud/Docker/AI for any deterministic operation — violates the governing rule and portability.

---

## Implementation order (policy-compliant)

1. Inventory + baseline measurements (this audit — done, evidence in `benchmarks/`).
2. **A, B** — immediate, ship now.
3. **C** — complete: direct local ONNX matte is the supported optional route.
4. **D** — complete: the LGPL AV1 build is promoted and smoke-verified.
5. **E, F** — cache + incremental manifest, with hit/miss + invalidation benchmarks.
6. **G** — prototype + throughput benchmark only; adopt solely if it beats serial without contention.

Re-run `benchmarks/` after each step; record before/after here. No improvement is claimed without the paired measurement.

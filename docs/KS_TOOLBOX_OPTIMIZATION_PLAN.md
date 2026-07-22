# KS ToolBox — Optimization Plan

Companion to `KS_TOOLBOX_OPTIMIZATION_AUDIT.md`. Every item preserves capability. Ordered by risk. **Nothing high-risk is implemented before it is documented, prototyped, benchmarked, and approved** (per the implementation policy).

---

## Opportunity table

| Opportunity | Current behavior | Proposed behavior | Functionality impact | Disk saving | RAM saving | Runtime saving | AI-call reduction | Complexity | Risk | Deps affected | Tests required | Recommendation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A. Exclude icon source + tests from bundle** | `KSToolBox_1024.png` (1.5 MB) + `test_smoke.py` shipped in dist | PyInstaller `datas` excludes build-only files | none | ~1.6 MB | — | — | — | Low | **Low** | none | build boots; icon still shows | **Implement** |
| **B. `.part` sweep before a job** | orphaned `.part` after a hard kill linger | job start removes stale `.part` in its output dir | none (safer) | — | — | — | — | Low | **Low** | none | crash-recovery bench | **Implement** |
| **C. ONNX-direct matte (drop rembg/pymatting)** | `rembg` pulls pymatting→numba→llvmlite/scipy/skimage (358 MB) | run u2net via `onnxruntime` directly; keep our defringe | **none** (same model, same output) | **~237 MB** | lower (no numba/llvm) | faster import | — | Med | **Med** | rembg→(onnxruntime, numpy, pillow) | clean_cutout smoke unchanged; matte pixel-diff vs rembg baseline | **Prototype → Implement** |
| **D. Shared/essentials ffmpeg build** | two 231 MB static exes (462 MB) | shared build: small ffmpeg+ffprobe + shared avcodec DLLs, only needed codecs | none if codecs verified | **~300–340 MB** | — | — | — | Med | **Med** | bundled `bin/` | compressor + chopper smoke on the new build (x265, vmaf, blackdetect, vpx, mp3lame) | **Prototype → Implement** |
| **E. Content-addressed cache** | probe/VMAF/matte recomputed every run | sidecar cache keyed on `hash(input)+settings+tool/model version` | none (only skips identical work) | small cache cost | — | **large on re-runs** | **skips inference on unchanged images** | Med | **Low** | none (new module) | cache hit/miss + invalidation bench | **Implement (medium-term)** |
| **F. Content-hash resumable manifest** | resume = "output file exists" | manifest records input hash+settings; changed settings/inputs reprocess, rest skip | none (more correct) | — | — | medium on re-runs | — | Med | **Low** | none | incremental bench (1/1000 changed) | **Implement (medium-term)** |
| **G. Bounded parallel batch** | one worker, files serial | measured, resource-aware pool (separate CPU / ffmpeg / ONNX caps) | none | — | higher peak (bounded) | **large on CPU-bound batches** | — | High | **Med-High** | none | throughput bench vs serial; contention check | **Prototype only** |
| **H. Saved presets** | settings live for the session | name/reload option sets | none (additive) | — | — | — | — | Low | **Low** | none | UX | **Defer (nice-to-have)** |

Estimated combined effect of C + D: a full portable Windows bundle from **~820 MB → ~260 MB**, no capability lost. Splitting Clean Cutout's model stack into an optional add-on download takes the *core* toolbox to **~120 MB**.

---

## Immediate low-risk improvements (safe to implement now)

These meet all criteria for immediate action (behavior understood, tests exist, capability preserved, trivial rollback, measurable benefit):

- **A — Bundle hygiene.** In `KS ToolBox.spec`, exclude `assets/KSToolBox_1024.png` and `tools/**/test_smoke.py` from `datas`/analysis. Keep `.ico`, `.png` (256), and the font. *Rollback:* revert the spec change. *Benefit:* smaller, cleaner dist; the icon still resolves (shell prefers `.ico`, falls back to the 256 `.png`).
- **B — Stale `.part` sweep.** At the start of a batch, delete stale `*.part` / `*.part.*` temp files under the output root (they are always incomplete writes we own). *Rollback:* remove the sweep call. *Benefit:* clean recovery after a hard kill; no orphaned temp files.
- **Doc hygiene.** `requirements.txt` already pins `numba>=0.59`/`llvmlite>=0.42` for the Python-3.12 build failure — once **C** lands, those pins and `rembg` disappear entirely (the fragility is designed out, not worked around).

---

## Medium-term architectural improvements (controlled refactor + benchmark first)

- **C — ONNX-direct matte.** Load the u2net ONNX model with `onnxruntime.InferenceSession`, do the documented pre/post-process (resize→normalize→sigmoid mask→resize back), then hand the RGBA to our existing `despill`/`defringe`. This *is* what `rembg` does internally; doing it directly drops the entire `pymatting`/`numba`/`llvmlite`/`scipy`/`scikit-image` chain. **Validation:** pixel-diff the direct-ONNX matte against the current `rembg` output on a fixed sample set; require visually-identical mattes before switching. Keep `rembg` selectable behind a flag for one release as a compatibility path. Model file stays a first-use download (unchanged).
- **D — Shared ffmpeg build.** Replace the two full static exes with a "shared" or "essentials" build (e.g. gyan.dev shared on Windows; distro/static-shared on Linux/mac) that keeps `libx265`, `libvmaf`, `libvpx`, `libmp3lame`, `aac`, and `blackdetect`. **Validation:** run `video_compressor` + `video_chopper` smoke tests against the new `bin/`; confirm VMAF and blackdetect still resolve. *Rollback:* restore the current `bin/`.
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
3. **C** — prototype, pixel-diff validate, then implement (biggest single win + removes build fragility).
4. **D** — prototype on the new ffmpeg build, smoke-verify codecs, then implement.
5. **E, F** — cache + incremental manifest, with hit/miss + invalidation benchmarks.
6. **G** — prototype + throughput benchmark only; adopt solely if it beats serial without contention.

Re-run `benchmarks/` after each step; record before/after here. No improvement is claimed without the paired measurement.

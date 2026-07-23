# KS ToolBox — Performance and Intelligence Audit

> **Historical audit.** Its 2026-07-22 findings, tool counts, and proposed next
> steps are evidence for that snapshot. Current product behavior is documented
> in the repository README, tool guides, and `docs/README.md`.

**Evidence date:** 2026-07-22

**Scope:** the current 17-tool repository and the dedicated `.venv`

**Authority:** source, tool READMEs, `requirements*.txt`, and `benchmarks/run_all_smoke.py`

This replaces the obsolete five-tool audit. It is a current-state audit, not a claim that proposed optimizations are already implemented.

## Executive finding

KS ToolBox has the right cartridge architecture and mostly the right deterministic-first mechanisms. Its largest gap is not excessive AI. Batch execution, cache provenance, resource budgets, and recovery were previously panel-local across 17 independently built tools.

This audit implemented the first shared execution slice: a headless `BatchRunner`,
transactional `SQLiteJobStore`, typed lifecycle, per-item retry/quarantine,
cancellation/resume, validated previous-result reuse, and atomic JSON completion
reports. Image Rescale and Video Compressor now prove deterministic-library and
external-process paths through one lazy shell-owned priority queue. The queue
includes pause/resume/cancel controls, persisted history, and a CustomTkinter
Queue/History view. Resource-aware scheduling, dependency graphs, and a
content-addressed artifact cache are still missing and must not be claimed.

The best next move is therefore:

1. migrate additional deterministic tools through the proven queue contract;
2. measure representative CPU, disk, and external-process workloads;
3. add resource profiles and dependency edges only where those measurements require them;
4. improve exact algorithms and mature native backends;
5. add optional specialist models only behind validation-driven escalation.

No existing useful feature needs to be removed to do this.

## Evidence gathered

### Live launch

- The app launched through `.venv\Scripts\pythonw.exe main.py`.
- PID `31248` remained responsive after startup.
- Discovery found all 17 plugins.
- The initial environment had CustomTkinter and `send2trash`, but not Pillow or NumPy even though both are core requirements.
- `uv pip install --python .venv\Scripts\python.exe --requirements requirements.txt` restored Pillow and NumPy without adding optional AI packages.

The launch proves the lazy shell works. It does not prove every panel or operation works; the smoke suite is the tool-level evidence.

### Smoke baseline before environment repair

`benchmarks/run_all_smoke.py` discovered 17 tools:

- Real passes: Audio Tool, Package Extractor, Video Chopper, Video Compressor.
- Clean dependency skips: Asset Auditor, Dataset Manager, Format Converter, Icon Normalizer, Image Rescale, Material Converter, Pixel Art, Showcase, Sprite Viewer, Texture Renderer, Tileset Checker, To SVG.
- Failure: Alpha Doctor imported NumPy at test-module load and crashed instead of skipping.

The Alpha Doctor smoke test was corrected to skip cleanly when NumPy is unavailable. The complete harness now reports `17/17 ok — all green`. To SVG's deterministic safety/queue path passes while only its optional real-vtracer leg skips because that package is absent; Alpha Doctor's optional ONNX model leg skips while its deterministic pipeline passes.

## Architecture review

| Contract | Current state | Audit result |
|---|---|---|
| One tool per auto-discovered folder | 17 independent plugins expose `TOOL` | Good |
| Engine/UI split | Engines are headless; panels own CustomTkinter | Good |
| Lazy optional dependencies | Tool metadata avoids optional engine imports | Good |
| Worker-thread UI | Migrated panels use the shell queue; compatibility panels stay off Tk | Good; continue migration |
| Preview, confirm, logging | Batch tools use preview/manifests; destructive compressor confirms | Good, verify per new feature |
| Atomic output | Common pattern is temporary output then replace | Good, standardize and test crash recovery |
| Error envelope | Shared `ok`/`err` helpers exist | Good, add conformance tests |
| Durable batch execution | Runner/store plus shell queue implemented for sixteen contrasting image, vector, media, document, material, external-render, analysis, archive, grouped-audit, dataset, and hybrid workflows | Good proven core; keep interactive viewers distinct and add future modules individually |
| Failure isolation/recovery | Per-item quarantine/retry, pause/resume/cancel, history, and finalization warnings | Good contract; manual item retry remains |
| Content identity/cache | No shared persistent content-addressed cache | Missing |
| Incremental recomputation | Mostly output-exists checks | Partial and settings-blind |
| Resource scheduling | Serial batch loops; external tools manage themselves | Safe but unmeasured |
| Presets/procedure reuse | Mostly session-local UI settings | Missing shared preset format |
| Benchmark coverage | Smoke tests exist; representative throughput/quality corpus does not | Missing |

## Current mechanism map

| Tool | Current mechanism | Class | Main gap worth testing |
|---|---|---:|---|
| Video Compressor | cancellable ffprobe/SVT-AV1 or AV1 NVENC, staged candidate, VMAF rejection gate | Deterministic | per-title encode search, sampled/full VMAF policy, probe/VMAF cache |
| Video Chopper | cancellable ffmpeg blackdetect/cuts, staged ffprobe validation, durable multi-artifact queue, exact clip-set reuse, collision protection | Deterministic | adaptive/content cuts and fades before optional neural shot detection |
| Audio Tool | cancellable ffmpeg convert/trim/fade/normalize, staged ffprobe validation, durable queue, exact artifact reuse, collision protection | Deterministic | two-pass loudness measurement, silence/chapters, optional stem separation |
| Image Rescale | Pillow resampling and fit modes; durable validated shell-queue execution | Deterministic | content hashes, streaming/native backend benchmark, optional restoration upscale |
| Format Converter | cancellable staged Pillow/ffmpeg/document adapters, strict capability routing, typed artifact validation, durable queue, collision protection | Deterministic | metadata/color-profile policies, modern formats, backend provenance |
| Pixel Art | nearest-neighbor pixelization, median-cut palette, durable queue, validated artifact reuse | Deterministic | perceptual palette benchmark, palette locking, alpha-aware quantization |
| To SVG | durable vtracer queue with strict settings, collision/source guards, staged writes, bounded XML/SHA-256 validation, exact reuse/repair, failure quarantine, and atomic provenance | Deterministic | expose presets/path controls, benchmark process isolation, score path complexity and reconstruction error |
| Icon Normalizer | alpha geometry, fixed canvas placement, durable queue, validated RGBA reuse | Deterministic | perceptual visual-mass normalization and platform keyline presets |
| Showcase | Pillow compositing, mode-aware durable queue, grouped identity, exact output validation | Deterministic | attention/entropy smart crop and reusable layout presets |
| Alpha Doctor | durable per-image chroma/solid/edge flood; exact RGBA/hash/coverage validation and repair; explicit-consent, checksum-verified optional U2Net ONNX | Hybrid | uncertainty routing, matte cache, process-isolated inference benchmark, better optional high-resolution specialist |
| Material Converter | map discovery, channel operations, grouped durable queue, set-wide identity and validated provenance | Deterministic | canonical MaterialX/OpenPBR mapping, color-space validation, packed-map presets |
| Sprite Viewer | custom worker-backed Pillow viewer; bounded frames/grid/connected components; staged decode-validated GIF/JSON export | Deterministic | Aseprite metadata, trim/extrude/deduplicate, atlas export and source timing preservation |
| Tileset Checker | opposite-edge score, deterministic previews, durable queue, exact artifact validation, collision protection | Deterministic | multi-scale/gradient/perceptual seam score calibrated on labeled examples |
| Texture Renderer | custom tabbed durable queue over bounded/cancellable user-provided Substance/Material Maker CLIs; owned staging, artifact hashes, exact reuse/repair, atomic provenance, source/destination protection, and bounded log tails | Deterministic orchestration | executable/version capability checks, format-aware Substance validation, shared-stage cache, preset/schema normalization |
| Package Extractor | cancellable safe stdlib extraction, preview bomb budgets, atomic reports, durable queue, recursive hash validation, collision protection | Deterministic | optional Unity bundle adapter, entry-count profiling, stronger fuzz corpus |
| Asset Auditor | durable grouped corruption/exact-duplicate checks, indexed dHash grouping, health analysis, cancellable exact report-set validation/reuse | Deterministic | optional embedding escalation only for ambiguous semantic similarity after deterministic candidates |
| Dataset Manager | durable grouped pair/replace/split/bucket; copy-only planner; JSON-last provenance; exact output reuse and repair | Deterministic | stable hash splits, leakage/duplicate checks, class balance and optional label QA |

## Cross-cutting performance findings

### Startup

The shell and discovery are correctly lazy. CustomTkinter necessarily imports
Pillow, so Pillow is not a valid optional-heavy startup gate. Do not import
NumPy, ONNX Runtime, tool image engines, ffmpeg wrappers, or document stacks
during discovery. Any future model/catalog UI must read lightweight metadata only.

The old audit's startup and package-size numbers were measured against five tools and must not be reused as current 17-tool evidence. Re-run `measure_startup.py`, `measure_repo.ps1`, `measure_deps.ps1`, and the packaged build measurement after the dependency repair.

### Batch processing

Files are independent and most batch tools run them serially. This is a safe
baseline. All sixteen batch-capable workflows now run serially through the
durable shared runner. Sprite Viewer remains intentionally interactive and uses
one cancellable worker with UI-owned result polling rather than pretending to
be a queue job. Concurrency is not automatically an optimization:

- Pillow/NumPy work may benefit from a small bounded pool.
- ffmpeg and native encoders are already threaded; multiple jobs may reduce throughput.
- ONNX batches can reuse one session but may exceed RAM/VRAM if fanned out.
- archive and dataset jobs are often storage-bound.

Each workload needs a measured worker cap based on elapsed time, peak RAM/VRAM, CPU utilization, disk throughput, and cancellation latency.

### Cache and incrementality

The implemented checkpoint key uses tool/workflow versions, normalized settings,
and input path/size/mtime. Stored Image Rescale outputs are deterministically
reopened and validated before reuse. This is recovery identity, not a
content-addressed cache.

The shared missing primitive is a versioned content key:

```text
sha256(input bytes)
+ normalized operation/options
+ engine/tool version
+ dependency/model identity
+ hardware-sensitive mode only when output can differ
```

High-value reusable results include ffprobe metadata, VMAF scores, source hashes, image metadata, alpha mattes, vector traces, duplicate fingerprints, material-set discovery, dataset pairing, and external-render manifests. Cache writes must be atomic; corrupt or mismatched entries must miss loudly and be replaceable.

### AI routing

The current portfolio uses AI only where it is plausible: optional Alpha Doctor matting. Future AI paths should be narrow:

- Video Chopper: only ambiguous transitions after deterministic detectors disagree or score near threshold.
- Audio Tool: only requested source separation/restoration.
- Image Rescale: only requested restoration upscale or detected insufficient deterministic quality.
- Alpha Doctor: only when chroma/solid/edge confidence is poor.
- Asset Auditor/Dataset Manager: embeddings or label-quality models only after exact and perceptual checks.

Generative models are not justified for conversion, packing, archive extraction, exact resize, or validation tasks.

## Dependency and storage review

- Core dependencies are appropriate for the current default feature set: CustomTkinter, `send2trash`, Pillow, NumPy.
- ffmpeg/ffprobe are shared external binaries and should remain shared rather than duplicated per cartridge.
- ONNX Runtime, VTracer, document adapters, and future specialist engines belong in optional feature packs unless packaging measurements justify inclusion.
- A mature native backend such as libvips or OpenImageIO is acceptable only after a corpus benchmark proves a meaningful throughput, memory, format, or color-management advantage.
- A model or backend must declare download/install size, resident memory, supported hardware, cold/warm latency, output provenance, and failure fallback.

## Quality and functionality risks

1. Existing thresholds such as “seamless >= 0.85” are heuristic until calibrated on labeled samples.
2. VMAF alone is not a universal perceptual guarantee; codec and content-specific testing is still needed.
3. Neural super-resolution can invent detail. It must be labeled restoration, retain exact resize, and provide comparison/validation.
4. Matting models can remove thin structures or keep background fragments. Matte confidence and edge diagnostics are required.
5. Perceptual/embedding duplicate detection can merge legitimately distinct assets. It should propose groups, never destructively decide.
6. New format backends can change metadata, ICC profiles, bit depth, alpha, animation, or orientation. Round-trip fixtures are required.
7. More parallelism can worsen elapsed time and responsiveness through oversubscription.

## Significant-change review template

Every implementation slice in the roadmap must record:

1. Current behavior and measured baseline.
2. Proposed behavior and acceptance threshold.
3. Deterministic, AI-assisted, or hybrid rationale.
4. Output-quality and functionality result.
5. Package/dependency delta.
6. Startup, cold-run, and warm-run delta.
7. Peak RAM/VRAM, CPU, disk I/O, and GPU-transfer delta.
8. Single-file and batch throughput delta.
9. Cache hit/miss and incremental rerun behavior.
10. Risks, tests, benchmark artifacts, feature flag, and rollback procedure.

## Audit conclusion

The architecture is being evolved, not rewritten. Sixteen durable batch slices,
the shell-owned queue, and one bounded custom interactive viewer are implemented
and verified without changing CustomTkinter or proven output algorithms. They
cover in-process image, external-media/rendering, grouped-material, analytical,
secure archive, and interactive sprite-inspection contracts.
Future batch modules should enter through this proven core; measure resource
profiles before adding scheduling lanes or dependency graphs.
Content-addressed caching remains later work. Improve deterministic
paths with mature native algorithms and add specialist AI only as optional,
lazy, confidence-triggered cartridges with exact validation around outputs.

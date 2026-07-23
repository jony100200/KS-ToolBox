# Incremental Batch-Core Migration

The goal is to strengthen the existing application without replacing working
tools. Every migration preserves engine output and keeps the old path easy to
restore until the full release gate passes.

## Repository audit result

- 17 discovered and UI-constructible tools.
- 15 panels inherit `BaseBatchPanel`; the custom Texture Renderer also runs a
  batch worker.
- 16 panels define their own `_work` loop.
- 15 panels define their own manifest path/writer.
- `BaseBatchPanel` centralizes file selection, run/stop controls, and one worker
  thread, but not durable job state.
- Tool engines are already headless and mostly use atomic outputs.
- Optional model and heavy tool imports are lazy.

## Migration order

### Slice 1 — implemented

- Add typed job/item states, definitions, cancellation, reports, and runner.
- Add lazy standard-library SQLite persistence.
- Prove exception isolation, retry, cancellation/resume, and result reuse.
- Migrate Image Rescale without changing its algorithms or CustomTkinter UI.

### Slice 2 — implemented contrasting external process

Video Compressor now uses the durable runner and a cancellable process adapter
that terminates its owned FFmpeg/ffprobe/HandBrake process tree. Encoding writes
a candidate; VMAF runs before atomic final commit. Delete confirmation and the
quality gate remain intact, and an unproven VMAF result never authorizes original
deletion.

### Slice 3 — implemented shared queue application service

Worker ownership for Image Rescale and Video Compressor now lives in one lazy,
shell-owned `JobQueue`. `AppServices` injects it into panels, and the
CustomTkinter Queue/History view exposes priority-ordered submissions,
pause/resume/cancel, progress, finalization warnings, and persisted history.
Tk widgets poll immutable snapshots on the main thread; queue workers never
touch Tk. `BaseBatchPanel` retains its old worker only as a migration adapter.
Icon Normalizer is the third migrated slice and adds deterministic square-RGBA
artifact validation before a stored output is reused.
Pixel Art Converter is the fourth migrated slice. Reuse validation reopens both
source and output and verifies PNG/RGBA format, expected native or upscaled
dimensions, palette limit, and binary alpha before trusting the artifact.

#### Pixel Art change report

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-local compatibility worker → shell queue with the same Pillow transform |
| Architecture/language | Existing Python/Pillow engine retained; shared Python queue/report contracts reused |
| Functionality and quality | Pixel size, median-cut palette, dithering, hard alpha, and upscale behavior unchanged; smoke remains ≤8 colors at 64×64 |
| Code/dependencies | 216 → 311 production lines (+95 for queue adapter and validation); zero dependencies added |
| Package/startup/runtime | No package component added; ready-to-mainloop 127 → 120 ms (within normal range); transform throughput not claimed changed |
| RAM/VRAM/CPU/disk/GPU | No model, GPU, or new image buffer; SQLite checkpoint/report metadata is the only new disk work |
| Batch/cache/AI | Per-item recovery and validated reuse added; no AI calls; processing algorithm unchanged |
| Reliability/security | Failures isolate per item; manifests no longer fail silently; corrupt or policy-mismatched artifacts are rejected |
| Tests/benchmarks | Tool smoke, 17 unit tests, 17-panel construction, four-tool queue flow, full 17-tool smoke, compile and startup gates pass |
| Risk/rollback | Validator adds reopen cost only during reuse; revert panel/tool adapter and `validate_result` to restore the compatibility worker |

### Slice 3e — implemented grouped Material Converter

Material Converter demonstrates grouped durable work. One detected PBR texture
set is one executable item and failure boundary; all member maps are explicit
identity dependencies. Changing roughness, metallic, AO, normal, or another
member invalidates the checkpoint even when the representative anchor is
unchanged. Reuse validation opens every emitted image and verifies the versioned
per-set manifest, its operations, source paths, and output names. A manifest
write failure now fails visibly and records any already committed outputs.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-local set loop → one durable queue item per detected texture set |
| Architecture/language | Existing Python/NumPy/Pillow algorithms retained; grouped identity added to the shared batch contract |
| Functionality and quality | Detection, packing, normal flip, inversion, resizing, and engine naming unchanged; existing channel-algebra/full-pipeline smoke remains green |
| Code/dependencies | Material production source 578 → 667 lines (+89 for grouped adapter, validation, and visible partial-output provenance); zero dependencies added |
| Package/startup/runtime | No package component added; ready-to-mainloop 120 → 137 ms within the measured range; set-processing throughput is not claimed changed |
| RAM/VRAM/CPU/disk/GPU | Existing image buffers unchanged; no GPU/model; SQLite stores one record per set plus report metadata |
| Batch/cache/AI | Any member-map change invalidates the set; valid complete sets are reusable; no AI calls |
| Reliability/security | Per-set retry boundary prevents partial channel packing; atomic images remain; manifest failure no longer disappears |
| Tests/benchmarks | 18 unit tests, material smoke including corrupt-output and manifest-failure cases, real two-map/one-set queue flow, 17/17 panels and smokes; durable core 9,876 items/s |
| Risk/rollback | Validation reopens all set outputs during reuse; revert the panel adapter/validator while retaining the backward-compatible dependency field |

### Slice 3f — implemented grouped/per-file Showcase

Showcase selects work granularity by mode. A contact sheet is one durable item
whose identity includes every source; hero mode remains one item per source;
before/after mode also includes every possible counterpart candidate, including
missing paths, so adding a counterpart invalidates a prior missing-pair result.
Useful contact sheets that skip unreadable images use the typed warning outcome
instead of being mislabeled clean or quarantined.

Because different contact jobs share `contact_sheet.png`, each rendered result
records a streamed SHA-256. Reuse requires the exact bytes plus PNG and
mode-specific geometry to match, preventing a later job's overwrite from being
accepted as an earlier result.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-local mixed loop → mode-correct grouped/per-file queue definitions |
| Architecture/language | Existing Python/Pillow compositors retained; grouped dependencies, warning outcome, and shared SHA-256 reused |
| Functionality and quality | Contact, hero, before/after, labels, backdrops, shadow, watermark, and atomic PNG behavior preserved |
| Code/dependencies | Showcase production source 586 → 692 lines (+106 for mode routing, structured counts, hash/geometry validation); zero dependencies added |
| Package/startup/runtime | No package component/model added; ready-to-mainloop measured 124 ms; output hashing adds one streamed read after each render |
| RAM/VRAM/CPU/disk/GPU | Hashing is bounded streaming I/O; existing Pillow buffers unchanged; no GPU/VRAM/AI |
| Batch/cache/AI | Every relevant source/counterpart affects identity; exact output hash blocks path-collision reuse; no AI calls |
| Reliability/security | Degraded sheets are warning-bearing; overwritten/corrupt PNGs fail validation; manifest errors remain visible |
| Tests/benchmarks | 20 unit tests; 17/17 panels and smokes; full contact/hero/before-after, degraded/corrupt overwrite, and real grouped queue checks; durable core 10,725 items/s |
| Risk/rollback | Extra output read costs disk bandwidth; revert panel/validator/result metadata while retaining compatible core contracts |

### Slice 3g — implemented analytical Tileset Checker

Tileset Checker now submits one durable item per texture while preserving the
existing NumPy seam metric and Pillow previews. Results record the exact selected
artifact set and a streamed SHA-256 for each PNG. Reuse validates score
invariants, hashes, formats, modes, and mode-derived geometry without decoding
the large preview pixels again.

The slice also proved a universal output-safety primitive. A tool supplies its
side-effect-free output plan; `engine_common.find_output_collisions` normalizes
paths using host filesystem rules and detects cross-source writes, duplicate
writes within one plan, and outputs that target selected inputs. Tileset Checker
blocks a real run with visible remediation instead of silently overwriting data.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-local loop with silent manifest/collision failures → shell queue, visible finalization errors, and pre-run collision rejection |
| Architecture/language | Existing Python/NumPy/Pillow analysis retained; generic path safety lives in `engine_common`, queue/report mechanics stay shared |
| Functionality and quality | X/Y/overall score, offset, montage, heatmap, dry run, mirroring, and reports are unchanged for non-conflicting inputs |
| Code/dependencies | Tool production source 340 → 482 nonblank lines; shared `engine_common` 161 → 191 (+172 combined); zero dependencies added |
| Package/startup/runtime | No package component/model added; ready-to-mainloop 124 → 128 ms; a 12.8 MB three-preview sample measured 401.2 ms processing and 9.9 ms for the added hash pass |
| RAM/VRAM/CPU/disk/GPU | Hashing uses the shared 1 MiB streaming buffer; validation reads bytes plus image headers without pixel decompression; no GPU/VRAM/AI |
| Batch/cache/AI | Per-texture checkpoint/retry/quarantine and exact artifact reuse added; unchanged analytical method; zero AI calls |
| Reliability/security | Source/output collisions are blocked; corrupt, missing, wrong-sized, wrong-mode, or inconsistent stored results rerun; partial outputs are reported |
| Tests/benchmarks | 21 unit tests; focused math/preview/hash/collision smoke; seven-tool real queue flow; 17/17 panels and smokes; durable core 10,611 items/s |
| Risk/rollback | Hashing adds bounded output I/O and strict collision checks reject formerly destructive cases; revert the panel/tool adapter and result validator while retaining the generic helper |

### Slice 3h — implemented cancellable Audio Tool

Audio Tool retains its existing single-pass FFmpeg conversion, trim, fade, and
loudness filter construction. FFmpeg and ffprobe now run through the shared
cancellable process-tree adapter. Each candidate remains staged until ffprobe
proves a positive duration; the engine then records its exact byte count and a
cancellable streamed SHA-256 before atomic replacement. Timeout/probe failures
retain retryability for one controlled per-item retry.

Flat same-stem and selected-input collisions are rejected before a real batch.
The shared collision presentation moved into `BaseBatchPanel` after Audio became
its second user. Manifest failures are visible finalization warnings, and stored
outputs are reused only while their planned path, size, and exact hash match.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-owned blocking loop and commit-on-FFmpeg-exit → shell queue with process-tree cancellation, staged validation, retries, checkpoints, and reports |
| Architecture/language | Existing Python command builder and native FFmpeg/ffprobe retained; shared queue, process, hash, collision, and completion primitives reused |
| Functionality and quality | Formats, bitrate presets, trim, fade, normalize, mirroring, dry run, and filenames unchanged; invalid/non-finite times now fail visibly |
| Code/dependencies | Audio production source 399 → 567 nonblank lines; shared `engine_common`/`batch_panel` 511 → 533 (+190 combined); zero dependencies added |
| Package/startup/runtime | No dependency/model/binary added; ready-to-mainloop 126 → 122 ms; isolated 3 s WAV→MP3 median 50.5 → 83.9 ms (+33.4 ms) from independent pre-commit probing/hashing |
| RAM/VRAM/CPU/disk/GPU | One sequential ffprobe validation plus one streamed output read with a 1 MiB buffer; FFmpeg remains single-process; no model, GPU, VRAM, or transfer change; child peak RAM not claimed measured |
| Batch/cache/AI | Per-file pause boundary, cancellable active process, one retry for retryable failures, quarantine, checkpoint/reuse validation; zero AI calls |
| Reliability/security | Invalid candidates never replace destinations; cancellation removes staged files; collisions are blocked; no shell invocation; manifest errors no longer disappear |
| Tests/benchmarks | 21 unit tests; full audio convert/trim/normalize/fade/hash/collision/cancellation smoke; real WAV→MP3 queue output + manifest + UI collision gate; eight-tool queue flow; 17/17 panels and smokes; durable core 10,387 items/s |
| Risk/rollback | Independent validation adds measured latency, most visible on tiny clips; revert Audio panel/tool adapter and engine metadata/candidate probe while retaining backward-compatible shared helpers |

### Slice 3i — implemented multi-output Video Chopper

Video Chopper retains its existing FFmpeg black-gap detection, clip planning,
stream-copy default, and optional H.264 re-encode. It now submits one durable
item per source through the shell queue. Every generated clip is staged,
ffprobe-validated, hashed with the shared cancellable streaming helper, and
atomically committed. The result records the exact ordered clip paths, planned
ranges, byte counts, durations, and hashes; reuse succeeds only while the whole
recorded set still validates.

Continuous sources with no detected black gaps are now explicit skips instead
of redundant full-video copies. Different selected sources that would share a
generated clip directory are rejected before processing. Cancellation reaches
active FFmpeg/ffprobe work, retryable failures receive one controlled retry,
partial committed clips remain visible in failure provenance, and manifest
errors surface as finalization warnings.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-owned blocking loop and commit-on-FFmpeg-exit → shell queue with process-tree cancellation, staged validation, exact multi-artifact records, retry, checkpoint, and report |
| Architecture/language | Existing Python planner and native FFmpeg/ffprobe retained; the shared media-duration, process, hash, collision, queue, and completion primitives are reused |
| Functionality and quality | Black detection, minimum clip filtering, lossless stream-copy, optional H.264/CRF, naming, mirroring, dry run, and clip order remain; no-gap input now avoids an unnecessary copy; invalid/non-finite settings fail before media work |
| Code/dependencies | Video Chopper production source 353 → 661 nonblank lines (+308 for queue adapter, staged multi-output provenance, cancellation, and validation); shared `engine_common` plus Audio engine 594 → 620 (+26 net while replacing Audio's duplicate probe); zero dependencies added |
| Package/startup/runtime | No dependency, model, or binary added; ready-to-mainloop measured 139 ms; isolated 5 s/two-clip stream-copy median 136.4 → 198.5 ms (+62.1 ms, +45.5%) from two per-clip probes and streamed hashes |
| RAM/VRAM/CPU/disk/GPU | Clips remain sequential with one FFmpeg/ffprobe process at a time; hashing uses a 1 MiB buffer; staged files do not add a second full output copy; no model, GPU, VRAM, or transfer change; child peak RAM is not claimed measured |
| Batch/cache/AI | Per-source pause boundary, active-process cancellation, one retry for retryable failures, quarantine, checkpointing, and exact whole-set reuse validation; zero AI calls |
| Reliability/security | Invalid candidates never replace destinations; cancellation removes the active staged file; shared output directories are blocked; subprocesses never use a shell; partial artifacts and manifest failures remain visible |
| Tests/benchmarks | 22 unit tests; focused real-media chop/duration/hash/corruption/collision/cancellation/no-gap smoke; Audio regression smoke; nine-workflow real queue flow; 17/17 panels and tool smokes; durable core 10,843 items/s |
| Risk/rollback | Independent validation adds measured latency, especially for tiny clips; committed clips before a later clip failure remain for inspection and are reported rather than group-rolled back; revert the Video Chopper panel/tool adapter and staged metadata/validator while retaining compatible shared helpers |

### Slice 3j — implemented secure durable Package Extractor

Package Extractor retains its existing standard-library ZIP, TAR, compressed
TAR, and Unity-package parsing. One archive is now one durable queue item and
failure boundary. Streaming extraction observes active cancellation; an
interrupted attempt removes only the member paths it recorded as newly created,
while pre-existing collision targets remain untouched. Same-stem selected
archives that would share an extraction directory are rejected before a real
batch begins.

Declared byte limits now run during preview as well as extraction, actual-byte
limits remain enforced while streaming, and a 100,000-member default cap bounds
generated report and per-entry bookkeeping. Per-archive CSV and JSON reports are
staged, flushed, and published atomically with JSON as the completion marker.
The stored result records both report hashes; reuse recursively validates every
top-level and nested output path, size, and SHA-256 from that trusted report
tree. Security rejections and per-member extraction errors are visible warning
outcomes instead of clean completions.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-owned archive loop with boundary-only stop and silent manifest/report failure → shell queue with streaming cancellation, attempt-local rollback, checkpoints, exact recursive validation, warnings, and reports |
| Architecture/language | Existing Python stdlib `zipfile`/`tarfile` algorithms and CustomTkinter UI retained; shared queue, collision, hash, completion, and report contracts reused; no parser rewrite or service added |
| Functionality and quality | ZIP/TAR/Unity reconstruction, filters, rename/skip, nested depth, hashes, dry run, and safe paths remain; `.bz2`/`.xz` suffixes now reach already-supported TAR dispatch; preview now rejects declared bombs and invalid limits before writes |
| Code/dependencies | Package Extractor production source 580 → 1,144 nonblank lines (+564 for durable UI adapter, cancellation/rollback, atomic provenance, recursive validation, bounds, and visible failure paths); zero dependencies added |
| Package/startup/runtime | No package component/model/binary added; ready-to-mainloop measured 123 ms with no heavy imports; controlled 100-file/1.6 MiB ZIP process median 91.3 → 100.9 ms (+9.6 ms, +10.5%) across five runs |
| RAM/VRAM/CPU/disk/GPU | Existing 1 MiB streaming member buffer remains; 100,000-member default and 1,000,000 hard ceiling bound KS report bookkeeping, while `zipfile` still reads the central directory as an acknowledged stdlib format cost; report flush/hash adds small I/O; no GPU, VRAM, model, or transfer use |
| Batch/cache/AI | Per-archive pause/recovery boundary, active streaming cancellation, quarantine, and validator-backed whole-tree reuse; exact warm reuse validation measured 572.5 ms on the 100-small-file workload, so no speed claim is made; zero AI calls |
| Reliability/security | Traversal and link rejection remain; preview/actual byte and entry caps, staged-member cleanup, cancellation rollback, atomic report marker, same-destination rejection, strict options, and warning-bearing partial results are added |
| Tests/benchmarks | 22 unit tests; focused traversal/Unity/nested/hash/corruption/atomic-report/bomb/entry-cap/collision/options/cancellation rollback smoke; ten-workflow real queue flow; 17/17 panels and tool smokes; durable core 10,810 items/s |
| Risk/rollback | Exact validation reopens every output and can be slower than re-extraction for many tiny, highly compressible files; cancellation leaves harmless empty directories; revert the panel/tool adapter and new validation/report metadata while retaining the original extraction handlers if rollback is required |

The third slice also justified one shared `batch_reporting` primitive: all three
panels now reuse typed completion artifacts and BaseBatchPanel's item-display
flow rather than maintaining duplicate finalization loops.

Measured production source for the three panels plus `BaseBatchPanel` was 995
lines before extraction and is 974 lines including the new shared module after
extraction (−21 lines). Two focused unit tests were added. Ready-to-mainloop was
121 ms before and 127 ms after, within the existing 99–197 ms measured range;
no dependency, process, model, RAM/VRAM, or output-algorithm change was added.

After the fourth migration proved identical terminal control/status/report UI,
that behavior moved into `BaseBatchPanel._finish_queue_ui`. Measured production
source across the base and four durable panels fell from 1,078 to 1,027 lines
(−51). Ready-to-mainloop remained 120 ms, and the real four-tool queue flow plus
all 17 unit tests remained green.

After seven migrations, completion preparation was the remaining exact clone:
seven 10-line methods differed only by tool ID. It now lives in
`BaseBatchPanel._prepare_queue_completion`; tool-specific job definitions,
classification, metrics, manifests, and result reconstruction remain local.
Across `BaseBatchPanel` and the seven panels, measured nonblank production source
fell from 1,608 to 1,550 lines (−58). Ready-to-mainloop measured 128 → 126 ms
within normal variance; 21 unit tests and the seven-tool real queue flow pass.
No dependency, process, algorithm, output, or persistence schema changed.

### Slice 4 — next: resource profiles and dependencies

Priorities already order waiting jobs. Add dependency edges and resource
profiles only for demonstrated workflows. Start with measured CPU, disk, and
external-process limits. GPU/model residency belongs later, when an integrated
model tool needs it.

### Slice 5 — content-addressed cache

Add incremental hashes and validator-backed artifact reuse. The current
path/size/mtime job identity remains a checkpoint key, not an artifact-cache
claim.

## Per-tool gate

1. Record current smoke output and representative timing.
2. Adapt existing result to `ItemOutcome`; do not rewrite the engine.
3. Verify cancellation semantics and atomic output behavior.
4. Test malformed input, retry classification, interruption, resume, and report.
5. Run standalone smoke, all-panel UI construction, full smoke suite, startup,
   and relevant output-quality comparison.
6. Remove the old loop only after parity is proven.

## Rollback

For Slice 1, revert the Image Rescale panel to its former `_work` loop and remove
the two new core modules/tests. Its processing algorithm was not changed. For
Slice 2, restore Video Compressor's former loop and `run_cmd` calls; remove the
staged candidate changes only if the former pre-VMAF destination exposure is
explicitly accepted. The SQLite database is application metadata and may be
left in place; deleting it only discards job history, not user assets.

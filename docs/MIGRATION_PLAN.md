# Incremental Batch-Core Migration

The goal is to strengthen the existing application without replacing working
tools. Every migration preserves engine output and keeps the old path easy to
restore until the full release gate passes.

## Repository audit result

- 17 discovered and UI-constructible tools.
- 16 panels inherit `BaseBatchPanel`; Texture Renderer keeps a custom two-tab
  layout while reusing its durable queue lifecycle. Sprite Viewer remains a
  custom interactive viewer.
- Four panels retain a compatibility `_work` loop; sixteen production workflows
  use the shell-owned durable queue.
- Fifteen panels define a manifest writer; Texture Renderer keeps its
  format-specific manifest in the headless engine.
- `BaseBatchPanel` centralizes ordinary file selection plus durable queue
  controls, polling, completion, reporting, and recovery UI.
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

### Slice 3k — implemented multi-family Format Converter

Format Converter keeps its established Pillow, FFmpeg, Markdown, HTML, DOCX,
and PDF adapters and remains one first-party CustomTkinter plugin. Each selected
file is now a durable queue item. Strict option and capability checks reject an
invalid mixed batch before it starts, while same-stem sources that would share
an output are reported as collisions instead of racing.

File outputs use format-preserving sibling candidates; PDF page sets use an
exclusive staged directory. KS reopens and measures images, probes media,
checks PDF signatures, streams UTF-8 validation, and hashes the exact artifact
set before atomic publication. Stored results are reused only while that same
typed artifact record still validates. PDF text extraction now streams to its
candidate instead of retaining all pages in memory, and explicit 10,000-page
and 10,000-frame ceilings bound report/checkpoint growth.

| Review item | Evidence |
|---|---|
| Current behavior | Panel-owned worker loop called converters directly; successful engine return was treated as sufficient, PDF pages appeared incrementally, output collisions and mixed-family incompatibility were not rejected as a batch, and cancellation did not reach active conversion work |
| Proposed behavior | Shell queue with one file per recovery boundary, strict planning, process-tree/loop cancellation, staged conversion, typed validation, atomic commit, one controlled retry for retryable failures, quarantine, checkpoint, and completion report |
| Architecture/language | Existing Python plugin, CustomTkinter panel, Pillow algorithms, native FFmpeg/ffprobe, and optional document adapters remain; shared queue, process, hash, collision, completion, and artifact contracts are composed around them rather than rewriting mature engines |
| Functionality and quality | Existing image, media, Markdown/HTML/DOCX, PDF image/text, mirroring, quality, bitrate, DPI, CSS, and preview behavior remains; malformed settings fail earlier, page sets publish completely, and outputs must independently decode/probe before success |
| Code and dependencies | Format Converter production source 546 → 1,149 nonblank lines (+603 for strict multi-family planning, cancellation, staged page/file handling, exact provenance, validation, queue adapter, and failure paths); zero dependencies added or removed |
| Package-size impact | No model, library, binary, asset, or background service was added, so this slice adds no dependency/binary payload; the complete packaged artifact was not rebuilt and no total-package-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 129 ms with 36 ms discovery and no optional-heavy imports; five-run medians: 1024² PNG→JPEG 11.3 → 19.9 ms (+8.7 ms, +76.7%) and 3 s 320×240 MP4→MP3 52.4 → 87.6 ms (+35.2 ms, +67.2%) because success now includes independent reopen/probe/hash validation |
| RAM, VRAM, CPU, disk, and GPU transfer | Image/PDF loops remain sequential; PDF text is now streamed; hashing/UTF-8 checks use bounded buffers; one FFmpeg/ffprobe process runs at a time; staged paths do not duplicate a full output; no model, GPU, VRAM, or GPU transfer is introduced; child peak RAM is not claimed measured |
| AI and model-loading impact | Zero AI calls and zero model loads; exact format conversion and deterministic validation are sufficient for this tool |
| Batch, cache, and incremental impact | Per-file retry/quarantine/checkpoints and exact validator-backed whole-result reuse replace panel-only execution; unchanged media reuse validation measured 37.2 ms across five runs and avoids conversion, but no universal speedup or content-addressed cache is claimed |
| Reliability, security, tests, and benchmarks | Invalid candidates never replace destinations; cancellation cleans the active candidate; existing PDF page folders are not implicitly overwritten; subprocesses do not use a shell; focused base and optional-dependency smokes cover corruption, exact page sets, collision, bounds, cancellation, and real conversions; the full 22-unit/17-panel/11-workflow/17-smoke release gate is required below |
| Risks and rollback | Independent validation is proportionally expensive for tiny files; exact page manifests can be large up to their cap; a fixed sibling candidate assumes the shell queue is the destination owner. Roll back the panel/tool queue adapter and engine validation/candidate layer together while retaining compatible shared helpers; original inputs are never modified |

Cartridge-grade score for this slice: functional completeness 5, output quality
5, runtime performance 4, startup efficiency 5, memory efficiency 4, storage
efficiency 4, batch efficiency 5, cache effectiveness 4, incremental execution
4, AI efficiency 5, reliability 5, maintainability 4, portability 5, and security
5. The 4s are deliberate boundaries rather than unmeasured 5s: validation has
a measured small-file runtime cost, animated frames still use a capped in-memory
list, provenance consumes bounded metadata/storage, reuse is whole-item rather
than a shared content cache, and the multi-family adapter necessarily carries
more failure-path code.

### Slice 3l — implemented indexed, durable Asset Auditor

Asset Auditor replaces exhaustive dHash pair comparison with an exact BK-tree
Hamming-metric index feeding the existing union-find. One selected collection
is now one durable queue item; every selected source contributes to grouped job
identity. The queue persists only a compact summary and exact report artifacts,
while the detailed per-file records remain in `audit.json` rather than bloating
SQLite checkpoints.

HTML and CSV render to staged files first. JSON records their size and SHA-256
and commits last as the report-set completion marker. Recovery reuses the audit
only while all three artifacts and the stored settings/summary still match;
corruption causes deterministic re-execution. Hashing, decode boundaries,
metric grouping, folder traversal, thumbnails, report writes, and validation
observe cancellation.

| Review item | Evidence |
|---|---|
| Current behavior | Every valid image hash was compared with every later hash; the panel owned one blocking aggregate loop; report files committed independently without cross-file provenance; stop was boundary-only and report failures were UI-local |
| Proposed behavior | Exact metric-index search plus union-find; grouped shell job with all-source identity, strict bounds, active cancellation, bounded warnings, staged report set, JSON completion marker, exact validation, retry/quarantine, checkpoint, repair, and morning report |
| Architecture/language | Existing Python/Pillow/NumPy inspection, CustomTkinter panel, dHash, and union-find remain; a compact measured index and shared queue/hash/report contracts are composed around them without a rewrite, service, or new abstraction layer |
| Functionality and quality | Filename, corruption, exact/near duplicate, size, empty-folder, dimension, health, histogram, HTML, JSON, CSV, and offline thumbnail behavior remains; grouping is exact, omitted thumbnails and unreadable folders are now announced, and invalid settings fail before scanning |
| Code and dependency impact | Asset Auditor production source 682 → 1,178 nonblank lines (+496 for indexed search, cancellation, strict settings/resource ceilings, compact durable result, staged provenance, exact validation, UI adapter, and failure paths); zero dependencies, models, assets, binaries, processes, or services added |
| Package-size impact | Source-only change with no dependency/binary payload; the complete packaged artifact was not rebuilt, so no total package-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 132 ms with 29 ms discovery and no optional-heavy imports; 2,000 seeded hashes at threshold 8 measured 4,451.3 → 509.5 ms (8.7×, −88.6%); full 200-image audit+three reports measured 174.9 → 186.0 ms (+11.1 ms, +6.4%) from provenance/hashing |
| RAM, VRAM, CPU, disk, and GPU transfer | The metric index adds O(n) compact nodes; one audit caps at 100,000 files; queue data stores summary/artifacts instead of full records; hashes stream in bounded buffers; warning lists cap at 101 entries; report rendering remains in process and detailed JSON remains O(n); no GPU, VRAM, model, or transfer; peak RAM is not claimed measured |
| AI and model-loading impact | Zero AI calls and model loads; Hamming radius search is an exact deterministic metric problem |
| Batch, cache, and incremental impact | Whole-collection retry/quarantine/checkpoints and validator-backed reuse replace the panel loop; all selected files affect identity; unchanged 200-image report validation measured 1.9 ms and avoids audit work; granularity is collection-level, not per-file or per-stage content caching |
| Reliability, security, tests, and benchmarks | JSON commits last and authenticates HTML/CSV; cancellation cleans candidates; strict finite/resource bounds apply; subscriber work never calls Tk from the worker; parity tests cover five thresholds; focused smoke covers real issues, degradation, corruption, options, cancellation; twelve-workflow UI integration proves reuse and repair |
| Risks and rollback | BK-tree lookup can approach quadratic work for dense hashes; HTML still embeds duplicate thumbnails and detailed JSON remains proportional to the collection; multi-file publication uses JSON validation rather than impossible cross-platform transactional renames. Revert panel/tool adapter and process/validation/report-marker layer together; revert `_group_near_dups` separately if the index itself must roll back |

Cartridge score: functional completeness 5, output quality 5, runtime 5,
startup 5, memory 4, storage 4, batch 5, cache 4, incremental execution 4,
AI efficiency 5, reliability 5, maintainability 4, portability 5, and security 5.
The 4s are explicit boundaries: peak RAM is not yet measured, reports retain
professional detail, reuse is whole-collection rather than content-addressed
per stage, and the necessary report/recovery failure paths increase local code.

### Slice 3m — implemented safe, durable Dataset Manager

The audit reproduced two data-integrity defects. Selecting a source folder as
the real output changed an original caption during Replace while reporting zero
failures. Selecting two same-name images from different folders reported two
copies but silently produced one output. Resolved-path guards and a complete
destination plan now reject both configurations before the first output write.
The plan includes images, captions, manifests, pair reports, and the completion
marker, and carries dimensions into execution instead of decoding twice.

One selected dataset is now one durable queue item. All selected images and
discovered sidecars contribute to its identity. Copy, caption read/write,
dimension inspection, report generation, hashing, and exact validation observe
cancellation at bounded checkpoints. Files commit atomically, SHA-256 is
collected from the same bounded byte stream used for each copy, and
`dataset_provenance.json` commits last only after the completed artifact set
exactly matches the planned paths. Reuse re-hashes every recorded output;
same-size corruption causes deterministic re-execution and repair.

Strict normalized settings reject unsafe or duplicate caption suffixes,
non-finite/negative/empty split ratios, invalid or oversized regex text,
replacement text over 1 MiB, missing real-run destinations, captions over
16 MiB, and grouped runs over 100,000 selected images. Detailed manifests stay
on disk while SQLite receives a bounded result summary and one marker artifact.
Provenance cannot name duplicate outputs, itself, or a path outside the chosen
output root.

| Review item | Evidence |
|---|---|
| Current behavior | Panel-local aggregate execution; boundary-only stop; no durable pause/recovery; copy/report failures could be undercounted; reports committed independently; no exact stored-result validation; unsafe source-root and same-name plans could alter or lose data |
| Proposed behavior | One grouped durable job with all-source identity, strict planning/settings, bounded cancellation, controlled retry/quarantine, atomic outputs, JSON-last provenance, exact reuse, corruption repair, history, and completion report |
| Architecture/language | Existing headless Python engine, CustomTkinter panel, deterministic pairing/split/bucket/replace primitives, Pillow metadata path, and shared shell queue are composed without changing the plugin contract, adding a service, or introducing a rewrite |
| Functionality and quality | Pair reports, copy-only replace, dimension/aspect buckets, deterministic train/val/test splits, CSV/JSON manifests, preview, degradation reporting, and source preservation remain; invalid UTF-8 captions now fail visibly instead of being silently rewritten with replacement characters |
| Code and dependency impact | Dataset Manager production source 781 → 1,287 nonblank lines (+506 for typed compact results, one-pass planning, cancellation, atomic artifact capture, provenance, validation, durable UI adapter, and explicit failure paths); zero dependencies, models, assets, binaries, processes, or services added |
| Package-size impact | Source-only change with no dependency/binary payload; the complete packaged artifact was not rebuilt, so no package-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 157 ms with 28 ms discovery and no optional-heavy imports; paired five-run 200 image-caption split medians measured 722.3 → 910.4 ms (+188.0 ms, +26.0%) for atomic streaming plus complete provenance; an earlier double-read design measured 2,033.4 ms and was rejected; exact reuse validation measured 508.5 ms |
| RAM, VRAM, CPU, disk, and GPU transfer | The grouped plan, manifest rows, and provenance are O(n); selected images cap at 100,000, persisted warnings at 101, captions at 16 MiB, copy buffers at 1 MiB; initial copies hash their write stream rather than rereading outputs; exact reuse performs one streamed read per output; peak RAM/CPU/disk throughput are not claimed measured; no GPU, VRAM, model, or transfer |
| AI and model-loading impact | Zero AI calls and model loads; pairing, path planning, copying, replacement, bucketing, splitting, hashing, and validation are exact deterministic work |
| Batch, cache, and incremental impact | Whole-dataset retry/quarantine/checkpoint and exact output-set reuse replace the panel loop; unchanged work avoids recopying after 508.5 ms validation versus 910.4 ms execution on the measured sample; identity includes discovered caption sidecars; reuse remains whole-dataset rather than per-stage content caching |
| Reliability, security, tests, and benchmarks | Source-root and all-output collision gates run before writes; old provenance is invalidated before execution; item failures continue scanning but fail the grouped job; marker-last publication prevents partial work being trusted; paths are confined to the output root; focused smoke covers source snapshots, collisions, malformed settings, cancellation cleanup, forged provenance, same-size corruption, and exact validation; thirteen-workflow UI integration proves reuse and repair |
| Risks and rollback | A grouped collection is the recovery/quarantine unit, JSON/manifest construction remains O(n), exact reuse deliberately rereads all outputs, Pillow decode cannot be interrupted inside one native call, and cross-file atomic commit is unavailable. Revert panel/tool adapter and process/provenance/validation layer together; the earlier source guard, planner, settings validation, and deterministic engine can remain independently |

Cartridge score: functional completeness 5, output quality 5, runtime 4,
startup 5, memory 4, storage 4, batch 5, cache 4, incremental execution 4,
AI efficiency 5, reliability 5, maintainability 4, portability 5, and security 5.
The 4s are explicit boundaries: durable validation adds measured runtime,
peak resources are not yet instrumented, professional manifests/provenance are
O(n), reuse is whole-dataset rather than stage-level, and explicit recovery
paths necessarily add local code.

### Slice 3n — checkpoint 1: Alpha Doctor integrity and explicit AI consent

The opening Alpha Doctor audit reproduced three defects on committed code. A
real Chroma run with the output root set to the source folder returned `cut`
while replacing the selected PNG. A PNG and JPEG with the same stem converged
on one PNG, so two apparent item outcomes produced one file. Finally, a
17-byte corrupt `u2net.onnx` in a normal cache directory was accepted without
any integrity check.

The focused checkpoint adds normalized bounds for method, model, color,
tolerance, feathering, erosion, and minimum coverage; shared all-output
collision planning; a headless single-source overwrite guard; explicit UI
approval before the first model network request; and checksum validation for
both cached and downloaded models. Verification is cached only for the same
resolved path, size, modification time, and expected checksum, so compatible
batch items avoid repeatedly hashing an unchanged 176 MB model.

| Review item | Evidence |
|---|---|
| Current behavior | Self-targeting could replace a source; same-stem inputs silently shared one output; malformed/NaN settings reached NumPy/Pillow; AI selection implicitly authorized a large download; cached models bypassed the configured checksum |
| Proposed behavior | Validate options and every destination before work; preserve every source; ask before network access; reject corrupt/unknown models; cache only a verified unchanged model fingerprint |
| Architecture/language | Existing NumPy/Pillow deterministic engine, CustomTkinter screen, optional ONNX Runtime, and shared collision primitive remain; the custom interface is preserved and no durable abstraction is introduced prematurely |
| Functionality and quality | Solid/chroma/edge-flood, despill, defringe, premultiply, mirror output, preview, and optional AI remain; invalid inputs now fail visibly and valid deterministic pixels are unchanged |
| Code and dependency impact | Alpha Doctor production source 430 → 602 nonblank lines (+172 for strict options, output planning, consent, checksum/session cache, UI preflight, and failure paths); zero dependencies, models, binaries, services, or processes added |
| Package-size impact | Source-only change with no shipped payload; no package-size change is claimed because a package was not rebuilt |
| Startup and runtime impact | Ready-to-mainloop measured 123 ms with 29 ms discovery and no heavy optional startup import; paired seven-run 512² deterministic cutout median 10.6 → 11.2 ms (+0.6 ms, +5.6%); a synthetic 64 MiB model verified in 84.7 ms cold and 0.253 ms from the unchanged-session cache |
| RAM, VRAM, CPU, disk, and GPU transfer | Deterministic NumPy/Pillow behavior is unchanged; planning is O(n) path metadata; model verification streams 1 MiB chunks once per changed file version; cache entries are O(models); no model was loaded and RAM/VRAM/GPU transfer were not measured |
| AI and model-loading impact | AI remains optional and CPU-only; zero calls/model loads in the measured deterministic path; absent models require user-approved network access and cached weights must pass their configured digest before ONNX Runtime sees them |
| Batch, cache, and incremental impact | This checkpoint blocks unsafe batches before the legacy worker starts and reuses verified model integrity within a session; durable job recovery, output validation, and per-item reuse are intentionally deferred to the next Alpha Doctor checkpoint |
| Reliability, security, tests, and benchmarks | Smoke snapshots the source, reproduces same-stem planning, rejects eight malformed setting classes, rejects corrupt cached weights, proves no-download permission failure, and retains all three deterministic pipeline checks; all 17 panels construct |
| Risks and rollback | Configured upstream digests are MD5 corruption checks rather than modern authenticity signatures; model hashing can delay the first AI item; path identity uses filesystem semantics; the legacy panel still owns execution/manifests. Revert normalized/preflight/model-verification changes together; deterministic matte primitives are untouched |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 4, storage 5, batch 3, cache 3, incremental
execution 2, AI efficiency 5, reliability 4, maintainability 4, portability 5,
and security 4. The sub-4 scores are the explicit next work: Alpha Doctor is
still on the compatibility loop, has no output-provenance cache, and cannot
incrementally reuse individual cutouts. The 4s retain measured or known
boundaries rather than claiming the active slice is finished.

#### Checkpoint 2 — durable, validated per-image execution

Alpha Doctor now submits one durable item per source image through the shared
shell queue while retaining its custom CustomTkinter options screen. Pause and
restart recovery occur at image boundaries; copy-independent failures retry
once and then quarantine without stopping later images. Chroma, edge-flood,
post-processing boundaries, model verification/download, output validation,
and streamed hashes observe cancellation. A native Pillow operation or ONNX
inference call remains non-interruptible only for that individual call.

Successful PNGs render to a staged candidate, decode as RGBA at the original
dimensions, recompute alpha coverage, stream SHA-256, and commit atomically.
The compact persisted result records that artifact. Completed work is reused
only while the exact PNG reopens and its dimensions, mode, coverage, size, and
hash still match; a same-size corruption deterministically misses and repairs.
The CSV manifest now rewrites atomically per completed batch rather than
appending duplicate rows or silently dropping write errors.

Model downloads now stream instead of using an uncancellable convenience call,
enforce a 512 MiB ceiling, clean candidates on cancellation/failure, and hash
the received bytes without a second model read. Compatible AI items retain the
verified ONNX session, preserving the useful residency behavior.

| Review item | Evidence |
|---|---|
| Current → proposed behavior | Panel-owned sequential loop, boundary-only stop, append-only best-effort CSV, and assumed PNG success → per-image durable queue, retry/quarantine/checkpoints/history, active cancellation boundaries, staged validated PNGs, atomic CSV, exact reuse and repair |
| Architecture/language | Existing custom CustomTkinter screen and NumPy/Pillow/optional ONNX engine remain; `services.queue`, typed item/result contracts, shared completion reporting, and engine-local validators are composed at the real UI/worker boundary |
| Functionality and quality | All deterministic and optional AI methods, post-ops, mirror paths, preview, coverage gate, and model reuse remain; every claimed cutout now proves RGBA mode, source dimensions, alpha coverage, nonempty bytes, and SHA-256 before commit |
| Code and dependency impact | Checkpoint-1 Alpha Doctor production source 602 → 870 nonblank lines (+268 for cancellable model/output paths, typed artifacts, exact validation, queue adapter, atomic manifest, and explicit failure handling); zero dependencies, models, assets, binaries, processes, or services added |
| Package-size impact | Source-only change with no runtime payload; a packaged artifact was not rebuilt, so no total package-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 132 ms with 28 ms discovery and no NumPy/ONNX startup import; paired seven-run 512² cutout medians measured 12.3 → 15.4 ms (+3.1 ms, +25.5%) for independent decode/coverage/hash validation; exact unchanged-output validation measured 2.8 ms |
| RAM, VRAM, CPU, disk, and GPU transfer | One image remains the bounded work unit; validation reuses Pillow decode memory plus a NumPy view and streams hashes in 1 MiB chunks; model download caps at 512 MiB and streams at 1 MiB; ONNX sessions remain resident for compatible batches; peak RAM/VRAM/CPU/disk/GPU transfer are not claimed measured |
| AI and model-loading impact | Deterministic jobs make zero AI calls; AI loads only when explicitly selected and installed/approved, verifies once per changed model version, then retains one compatible session; inference runs CPU-only in the existing process |
| Batch, cache, and incremental impact | Recovery/retry/quarantine granularity is one source image; identical job identity validates each stored item and invokes no matte for valid outputs; measured validation is 81.8% faster than execution on the sample; cache identity remains path/size/mtime plus normalized output-affecting settings rather than a shared content-addressed stage cache |
| Reliability and security | Output/source collision preflight and headless self-target guard remain; candidates validate before atomic replace; cancellation cleans partial PNG/model files; same-size corruption repairs; ephemeral download consent is excluded from output identity; network access remains opt-in and bounded |
| Tests and benchmarks | Focused smoke covers three deterministic methods, source snapshots, collisions, malformed settings, model consent/checksum/download cancellation, PNG artifact validation, same-size corruption, and immediate cancellation; real fourteen-workflow CustomTkinter queue check proves first execution, exact reuse, repair, manifest, history, and control recovery; full 22-unit/17-panel/17-smoke gate passes |
| Risks and rollback | Pillow/NumPy/ONNX still run in the shared app process; one native decode/filter/inference call cannot be cancelled mid-call; skipped low-coverage work can leave a prior destination untouched but reports that it was not saved; manifest is completion metadata rather than part of per-item cache validation; input job identity is metadata-based. Revert tool/panel queue adapter and artifact/cancellation layer together; checkpoint-1 safety guards remain independently useful |

Completed-slice cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 4, storage 5, batch 5, cache 4, incremental
execution 4, AI efficiency 5, reliability 5, maintainability 4, portability 5,
and security 5. The 4s are deliberate measured boundaries: professional
validation has a 3.1 ms sample cost, peak resources remain uninstrumented,
reuse is per-output rather than stage-content-addressed, and native/AI process
isolation remains a separate evidence-driven decision.

### Slice 3o — checkpoint 1: Texture Renderer output ownership

The audit reproduced a destructive mismatch between the Material Maker
confirmation and implementation. With a successful stub engine, a pre-existing
`KEEP_LICENSE.txt` was deleted and an unrelated existing PNG was resized from
32×16 to 64×64. Cleanup recursively processed the entire selected destination,
not only files produced by the current export. External engines also wrote
directly into final folders, so a nonzero exit could leave partial artifacts.

Both Substance and Material Maker now render into a deterministic per-project
stage under the output root. A marker proves the stage is KS-owned before any
recursive removal; an unmarked or symlinked collision fails without deletion.
Only a successful engine exit reaches publication. Substance publishes the
nonempty generated file set; Material Maker cleans/resizes only its staged
files, rejects cleanup errors, and publishes only nonempty PNGs. Existing
unrelated destination files remain untouched.

Publication preflights confinement, duplicate targets, symlinks, selected
project paths, all batch project paths, and the selected engine executable
before the first move. Existing same-name outputs are backed up inside the
owned stage. Generated files move atomically on the same volume, and a caught
later failure restores prior outputs or removes newly created ones.

| Review item | Evidence |
|---|---|
| Current behavior | External tools wrote directly to final destinations; Material Maker recursively deleted every non-PNG and resized every PNG already there; nonzero exits cleaned/left partial destination state; no ownership proof preceded recursive stage deletion |
| Proposed behavior | Isolated marked staging, successful-exit-only publication, complete target preflight, source/engine protection, atomic same-volume moves, existing-output backup, caught-failure rollback, and final preservation of unrelated files |
| Architecture/language | Existing custom tabbed CustomTkinter screen, headless Python command builder, shared no-shell runner, Pillow post-processing, and user-provided native CLIs remain; safety is localized to the engine output boundary |
| Functionality and quality | Recursive discovery, Substance resolutions, Material Maker targets, grouping, dry run, PNG resize, generated-sidecar cleanup, and arbitrary Substance output formats remain; cleanup errors now fail instead of claiming a complete render |
| Code and dependency impact | Texture Renderer production source 460 → 679 nonblank lines (+219 for owned staging, confinement, output enumeration, atomic publication/rollback, batch source protection, and explicit failures); zero dependencies, models, binaries, services, or background processes added |
| Package-size impact | Source-only change; external engines remain user-provided and no packaged artifact was rebuilt, so no package-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 119 ms with 28 ms discovery and no optional-heavy imports; five-run 100 × 16 KiB stubbed Substance medians measured direct 23.9 ms → isolated/atomic 71.5 ms (+47.6 ms, +199.0%, 0.476 ms/output), excluding the normally dominant external render |
| RAM, VRAM, CPU, disk, and GPU transfer | Target plans/backups are O(generated files); new outputs use atomic moves without a duplicate copy; only pre-existing replaced files are copied once for rollback; Pillow resize remains per staged image; peak resources and external GPU behavior are not claimed measured |
| AI and model-loading impact | Zero AI calls and model loads; this is exact filesystem/process orchestration |
| Batch, cache, and incremental impact | Per-project stage identity is stable and stale owned work self-heals; this checkpoint does not add durable queue state, output provenance, or reusable render caching |
| Reliability, security, tests, and benchmarks | Stubbed success proves license/existing-PNG preservation and generated-only resize; nonzero exit publishes nothing; unmarked stages survive; symlink/output confinement checks are explicit; source publication is refused; a forced second-output failure restores the first output; focused smoke passes |
| Risks and rollback | A hard process kill during the brief multi-file publish can still leave a valid but partial final set because cross-file atomic rename is unavailable; external CLIs are not yet cancellable and have no enforced timeout; generated output names cannot be known before rendering; existing same-name maps are intentionally replaceable after confirmation. Revert the stage/publish helpers and render adapters together; command builders and cleanup primitive are unchanged |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 4, storage 4, batch 3, cache 2, incremental
execution 2, AI efficiency 5, reliability 4, maintainability 4, portability 5,
and security 4. The sub-4 scores explicitly schedule the next checkpoint:
cancellable durable execution, completion provenance, exact reuse, and
restart repair. The 4s retain measured staging cost, unmeasured peaks, necessary
rollback storage, and the hard-kill multi-file boundary.

### Slice 3o — checkpoint 2: Texture Renderer execution control

The custom two-tab Texture Renderer UI remains because Substance and Material
Maker have genuinely different settings. Its external execution path is now
strict, bounded, and actively cancellable without forcing that screen into the
ordinary `BaseBatchPanel` shape. Discovery is case-correct, prunes the selected
output tree, rejects project symlinks, observes cancellation, and stops at
10,000 projects. Both renderers validate normalized settings and enforce a
user-visible 1–1,440 minute per-project timeout.

The shared subprocess runner gained an opt-in bounded-capture mode. Two
short-lived reader threads continuously drain stdout and stderr to prevent a
verbose native process from blocking while retaining only the final 1 MiB of
each stream. Stop terminates the exact owned process tree, waits for exit, and
then the renderer removes its owned stage. Other tools retain their existing
runner behavior because bounded capture is opt-in.

| Review item | Evidence |
|---|---|
| Current behavior | Stop was checked only between projects; one hung renderer could block indefinitely; `capture_output` could retain unbounded native logs; recursive discovery missed uppercase extensions on case-sensitive hosts, could rescan output, and had no item ceiling; invalid headless settings could silently reach fallback values |
| Proposed behavior | Active owned-process-tree cancellation, a strict per-project timeout, continuously drained 1 MiB stdout/stderr tails, strict normalized settings, case-correct/cancellable discovery, output-tree pruning, project-symlink rejection, and a 10,000-project ceiling |
| Architecture and language | The existing Python headless engine, custom tabbed CustomTkinter panel, and user-provided native CLIs remain; reusable process ownership and bounded capture live in `engine_common`, while renderer-specific validation/staging stays in the tool |
| Functionality and quality | Substance/Material Maker modes, recursive/flat scanning, grouping, dry run, all four Substance sizes, all four Material Maker targets, and headless resize values from 16–16,384 remain; rendered pixels and native CLI arguments are unchanged |
| Code and dependency impact | Shared runner 251 → 350 nonblank lines (+99); Texture Renderer engine/panel/tool 679 → 961 (+282) since checkpoint 1 for validation, bounded discovery, cancellation, timeout, cleanup propagation, UI state, and tests; zero dependencies, models, binaries, services, or resident workers added |
| Package-size impact | Source-only change; no packaged artifact was rebuilt, so no release-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop is 120 ms with 29 ms discovery and no optional-heavy imports; nine-run trivial child medians measured 35.5 → 36.0 ms (+0.4 ms), and an owned sleeping process cancelled in 297.0 ms |
| RAM, VRAM, CPU, disk, and GPU transfer | At most two 1 MiB diagnostic tails plus two reader threads exist for one active renderer process; tails replace unbounded captured logs and create no disk log; no AI/model/VRAM/GPU-transfer change; external-engine peak resources are not claimed measured |
| Batch, cache, and incremental impact | The active project can now stop instead of waiting for the native CLI; scan work is bounded and output work is isolated per project. Durable queue/checkpoint/history, output provenance, exact reuse, and incremental render caching remain for checkpoint 3 |
| AI and model-loading impact | Zero AI calls and model loads; deterministic orchestration remains the correct method |
| Reliability, security, tests, and benchmarks | Focused unit tests cover bounded stream tails, cancellation, timeout, invalid limits, and resource-warning cleanup; renderer smoke covers strict settings, uppercase/output-pruned/capped/cancellable discovery and staged cancellation cleanup; a 2,000-file/500-project scan measured 9.8 → 20.0 ms (+10.2 ms) for the new guarantees |
| Risks and rollback | Diagnostic output is intentionally limited to final tails; detached grandchildren outside the owned process group may escape platform controls; multi-file publication still has a hard-kill boundary; no render cache/history yet. Revert renderer timeout/cancellation calls independently; omitting `capture_limit_bytes` preserves the prior shared-runner behavior |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 5, storage 4, batch 3, cache 2, incremental
execution 2, AI efficiency 5, reliability 4, maintainability 4, portability 5,
and security 5. Batch/cache/incremental remain below 4 until durable execution,
validated provenance, exact reuse, and restart repair are implemented. Storage
and reliability remain 4 because rollback staging is necessary and a hard kill
cannot make a multi-file destination transaction atomic.

### Slice 3o — checkpoint 3: Texture Renderer durable queue and provenance

Texture Renderer keeps its purpose-built Substance/Material Maker tabs but now
inherits the proven `BaseBatchPanel` queue lifecycle. Each discovered project is
one durable item. The renderer executable is an identity dependency, normalized
settings are part of the job key, timeouts receive one controlled retry, failed
projects quarantine independently, and pause/cancel/restart recovery come from
the shell-owned queue rather than another private worker implementation.

Published files now carry path, size, and SHA-256 records. Completed grouped
jobs with distinct project destinations validate every stored artifact before
reuse; missing or same-size-corrupted outputs reset only their project.
Multi-project flat exports preserve their legacy sequential shared-namespace
behavior but deliberately disable completed-job reuse because independent
validation cannot prove overlapping external filenames safe. A versioned atomic
JSON manifest records executable identity, settings, results, and artifacts;
reuse repairs missing provenance without rerendering valid outputs.

| Review item | Evidence |
|---|---|
| Current behavior | A private panel worker held only in-memory progress; no pause, persisted item state, retry/quarantine, restart recovery, artifact identity, completion report, render reuse, or batch provenance existed |
| Proposed behavior | Shared durable per-project queue, pause/resume/cancel, one retry for timeout, quarantine, restart recovery, engine/stat-aware job identity, bounded artifact records, exact hash validation/reuse/repair, atomic provenance, and morning report |
| Architecture and language | The custom two-tab CustomTkinter layout remains; it subclasses `BaseBatchPanel` only for its proven queue lifecycle while tool-specific discovery/options/UI stay local and all render/validation/manifest logic remains headless Python around user-provided native CLIs |
| Functionality and quality | Substance and Material Maker settings, flat/grouped output, dry run, resize, confirmation, and native render output remain; no pixel algorithm or CLI setting changed. Material Maker reuse additionally decodes PNGs and verifies requested dimensions; arbitrary Substance formats receive nonempty size plus SHA-256 validation |
| Code and dependency impact | `batch_reporting` 69 → 70 nonblank lines (+1 opt-in provenance-on-reuse flag); Texture Renderer engine/panel/tool 961 → 1,220 (+259 for artifact bounds/hashes, validation, manifest, queue adapter, reuse policy, and recovery UI); zero dependencies, models, binaries, services, or extra resident workers added |
| Package-size impact | Source-only change; no packaged artifact was rebuilt, so no release-size change is claimed |
| Startup and runtime impact | Ready-to-mainloop measured 120 ms before and a five-run 115 ms median after, with 29 ms median discovery, within the established 99–197 ms range and with no optional-heavy imports. For 100 × 16 KiB staged outputs, publication measured 71.5 → 581.3 ms (+509.8 ms, +713%, 5.10 ms/output) because professional SHA-256 provenance reads every output; exact reuse validation measured 53.0 ms and atomic manifest writing 1.6 ms |
| RAM, VRAM, CPU, disk, and GPU transfer | Hashes stream through the shared 1 MiB buffer; one project is the queue work unit; artifact records cap at 4,096 outputs and stage enumeration at 100,000 entries; SQLite/report storage grows with bounded artifact metadata; no model, GPU, VRAM, or GPU transfer is introduced; external-engine peak resources are not claimed measured |
| Batch, cache, and incremental impact | Fifteen real shell workflows now use the durable queue. Recovery/retry/quarantine and repair granularity is one project; valid grouped/single-project results skip native inference/rendering; flat or duplicate grouped destinations rerun sequentially instead of claiming unsafe cache reuse; no shared content-addressed render-stage cache exists yet |
| AI and model-loading impact | Zero AI calls and model loads; exact deterministic orchestration and validation remain appropriate |
| Reliability, security, tests, and benchmarks | Smoke covers output/stage ceilings, hashes, MM decode/dimensions, same-size corruption, atomic manifest, retryable timeout, cancellation cleanup, rollback, source protection, and strict options. The real CustomTkinter queue test proves first native-stub execution, identical-job reuse with zero renderer calls, deleted-provenance repair, corrupted-output rerender, controls, and history. Generic queue tests cover persistence, retry, pause, cancellation, recovery, and subscriber isolation |
| Risks and rollback | SHA-256 publication adds a measured 5.10 ms per tiny output on this Windows filesystem; Substance formats cannot all receive codec-specific decoding; executable identity is path/size/mtime rather than a queried engine version; detached grandchildren and hard-kill multi-file publication remain platform limits; no cross-job stage cache exists. Revert the panel queue adapter, result artifact fields/validator/manifest, and opt-in reporting flag together; checkpoint-2 staging and process controls remain independently usable |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 4, storage 4, batch 5, cache 4, incremental
execution 4, AI efficiency 5, reliability 5, maintainability 4, portability 5,
and security 5. The 4s are explicit boundaries: cryptographic validation has a
measured cost, artifact/provenance records consume bounded storage, cache reuse
is per project rather than a shared stage graph, peak resources remain
uninstrumented, and the custom adapter necessarily owns mode-specific wiring.

### Slice 3p — safe, durable To SVG

The audit reproduced three concrete failures in the legacy adapter. A stub
vtracer that wrote a partial file and raised left `a.part.svg` behind. Two
different source folders containing `same.png`/`same.jpg` planned the same flat
`same.svg` destination with no collision gate. Invalid settings such as a
`bogus` color mode and negative speckle were accepted by dry run. The UI also
claimed Preview + Confirm + Logging while real runs had no confirmation and an
append-only manifest silently ignored write failures.

To SVG now validates settings and source bounds before all modes, preflights
batch destinations, confirms every real batch, and submits one image per durable
queue item. Vtracer still runs lazily and synchronously through its mature Rust
binding. The candidate SVG is size-bounded, streamed into SHA-256, rejects
document/entity declarations, and is parsed with bounded element counting
before atomic publication. Exact artifact validation enables completed-job
reuse and same-size corruption repair.

| Review item | Evidence |
|---|---|
| Current behavior | Private worker loop, boundary-only stop, no persistence/pause/recovery, same-name flat overwrite, invalid dry-run settings, no source/resource bounds, assumed nonempty output, stale `.part` on failure, append-only non-atomic CSV, swallowed manifest errors, and no overwrite confirmation |
| Proposed behavior | Strict settings/input bounds, collision/source guard, explicit confirmation, durable per-image queue, pause/cancel/recovery/history, failure quarantine, staged cleanup, bounded XML/SHA-256 artifact validation, exact reuse/repair, atomic CSV, and JSON morning report |
| Architecture and language | Existing CustomTkinter/BaseBatchPanel, headless Python adapter, and lazy official vtracer Rust extension remain. The panel only builds the job/confirmation/report; vectorization, validation, and output safety stay in `engine.py`. No AI or replacement tracer was introduced |
| Functionality and quality | Color/binary modes, stacked/cutout hierarchy, speckle, color/path precision, beside-source output, flat output, mirrored output, and dry run remain. Output bytes from vtracer are unchanged; new checks prove valid bounded SVG XML and exact content but do not claim perceptual reconstruction quality |
| Code and dependency impact | To SVG engine/panel/tool 221 → 535 nonblank lines (+314 for strict contracts, cancellation cleanup, XML/hash artifact validation, collision/confirmation, durable adapter, exact reuse, and atomic provenance); zero dependencies, models, binaries, services, or resident workers added |
| Package-size impact | Source-only change; vtracer remains an optional lazy dependency and no packaged artifact was rebuilt, so no release-size change is claimed |
| Startup and runtime impact | Five-run ready-to-mainloop median measured 115 → 114 ms with 29 ms discovery in both revisions and no vtracer import. For a 26,829-byte/1,002-element stub SVG, legacy staged write measured 0.47 ms versus 9.56 ms with bounded XML/SHA-256 validation (+9.09 ms); exact reuse validation measured 1.83 ms. Native tracing time is excluded |
| RAM, VRAM, CPU, disk, and GPU transfer | Input/output cap at 512 MiB, XML at 1,000,000 elements, hash chunks at 1 MiB, and iterparse clears completed elements; one image is the work unit. The Rust tracer's peak native RAM/CPU is not claimed measured; no GPU, VRAM, model, AI, network, or GPU transfer is introduced |
| Batch, cache, and incremental impact | Sixteen real shell workflows now use the durable queue. Recovery/quarantine/reuse/repair granularity is one source image; job identity includes normalized settings and input metadata; valid SVGs skip vtracer; no cross-job content-addressed trace cache exists |
| AI and model-loading impact | Zero AI calls/model loads. Exact deterministic vtracer is faster, reproducible, and more appropriate than generative vector reconstruction for this operation |
| Reliability, security, tests, and benchmarks | Always-on smoke uses a deterministic tracer stub to cover settings, collisions, failed/cancelled cleanup, destination preservation, DTD/entity rejection, artifact metadata, exact validation, and corruption repair; the optional real-vtracer leg skips cleanly when absent. The real CustomTkinter queue check proves first conversion, zero-call reuse, repair, manifest, controls, and history |
| Risks and rollback | The in-process native vtracer call cannot pause/cancel mid-call and a native crash could still terminate the UI; structural XML/hash checks do not score visual fidelity or path simplicity; the 512 MiB ceiling still allows a heavy trace; vtracer was absent on this machine so the optional real leg skipped. Revert the panel queue adapter, engine validator/artifact fields, and manifest rewrite together; deterministic stub coverage remains usable independently |

Checkpoint cartridge score: functional completeness 5, output quality 4,
runtime 4, startup 5, memory 4, storage 5, batch 5, cache 4, incremental
execution 4, AI efficiency 5, reliability 4, maintainability 4, portability 4,
and security 5. The 4s retain measured validation cost, unmeasured native peak
resources, structural rather than perceptual quality checks, per-job rather than
content-addressed reuse, optional wheel availability, and the in-process native
cancellation/crash boundary.

### Slice 3q — bounded custom Sprite Viewer

Sprite Viewer remains a purpose-built interactive CustomTkinter screen rather
than being forced through `BaseBatchPanel`. The audit reproduced a million-row
grid request that was accepted and iterated, and a synthetic GIF encoder failure
that left `out.part.gif`. Decode, alpha detection, slicing, GIF encoding, and
JSON export also ran on Tk's UI thread; source/frame counts were unbounded;
exports were not read back; and timer teardown silently swallowed exceptions.

The headless engine now prepares typed viewer state with strict frame/pixel/grid
budgets and cooperative cancellation. The custom panel owns one worker and a
UI-polled result queue, so worker threads never call Tk. A failed replacement
load preserves the current viewer. Exports require replacement confirmation,
write to a unique same-directory stage, flush and parse/decode the full result,
then atomically publish. Cancellation or validation failure removes the stage
and preserves an earlier destination.

| Review item | Evidence |
|---|---|
| Current behavior | Synchronous UI-thread decode/detection/export; accepted million-row grid; unbounded resident folders/animations; preallocated one-Python-pointer-per-pixel detector queue; no export read-back; failed GIF left a deterministic `.part` file; replacement loads cleared sheet state before success; unbounded checker cache; swallowed timer-cancellation errors |
| Proposed behavior | Keep the custom viewer while moving heavy work to one cancellable worker with UI-owned polling; reject invalid/oversized sources and grids before work; use frontier-only component traversal; retain at most eight checkerboards; preserve current state on failed load; validate unique staged GIF/JSON before atomic replacement |
| Architecture and language | Existing Python, Pillow, and bespoke CustomTkinter panel remain. `prepare_source` centralizes non-UI coordination in the headless engine; the panel only reads controls, starts/cancels one worker, polls results, and renders. The shared batch queue is deliberately not used because viewing one source is interactive, not an unattended batch |
| Functionality and quality | File/folder/animated source loading, grid/cell/auto slicing, overlay, checkerboard, play/step/scrub/FPS, GIF preview, and slice JSON remain. GIF pixels are still Pillow encoded; output quality is unchanged and every published frame now decodes successfully |
| Code and dependency impact | Sprite engine/panel/smoke/README grew from 735 to 1,291 nonblank lines (+556); 160 nonblank focused test lines were added for bounds, cancellation, preservation, staged validation, and real custom-panel worker delivery. No package, model, binary, service, or framework dependency was added |
| Package-size impact | Source-only change; no release artifact was rebuilt, so no package-size change is claimed |
| Startup and runtime impact | Five-run ready-to-mainloop median measured 114 → 113 ms and discovery 29 → 27 ms; no Pillow tool engine loads during discovery. A 1,024²/100-component detector measured 124.37 → 149.61 ms (+25.24 ms, +20.3%) for frontier-only memory and cancellation checks. A 48 × 64² GIF measured 17.48 → 25.91 ms (+8.43 ms, +48.2%) because all staged frames are decoded before commit |
| RAM, VRAM, CPU, disk, and GPU transfer | Resident work caps at 10,000 frames and 128M decoded RGBA pixels (512 MiB frame data before Pillow overhead); sheet source plus crops share that pixel budget; checker cache caps at eight display images. Detector Python traced peak fell 10.16 → 2.13 MiB (−79.0%) on the measured 1,024² workload. Already-RGBA export frames are no longer copied wholesale. No GPU, VRAM, model, network, or transfer is used |
| Batch, cache, and incremental impact | This tool intentionally has no batch job or persistent artifact cache. One source is the work unit; failed/cancelled replacement loads keep the prior prepared state, repeated renders reuse a bounded checker cache, and exports are explicit. All sixteen actual batch tools remain on the durable queue |
| AI and model-loading impact | Zero AI calls and model loads. Exact grid math, alpha connected components, Pillow decoding, and schema/codec validation are sufficient and reproducible |
| Reliability, security, tests, and benchmarks | Six focused tests cover grid/cell limits, animation/folder budgets, cancellation, typed source preparation, corrupt staged GIF rejection with destination preservation, strict JSON, and a real Tk worker-to-UI delivery. Existing real-image smoke and 17-panel construction pass. Worker errors are visible envelopes; exports reject nonstandard JSON numbers and require explicit overwrite confirmation |
| Risks and rollback | Pillow GIF encoding itself is not interruptible mid-call, so cancel is observed before/after encoding and during validation; the 512 MiB decoded budget still permits a large interactive workload; source animation timing is still replaced by chosen FPS; detector trades measured 20.3% runtime for 79.0% lower traced Python peak; no persistent reload cache exists. Revert the Sprite Viewer engine/panel/tests/docs together; no shared core or data migration is involved |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 4, startup 5, memory 5, storage 5, batch efficiency 5, cache
effectiveness 4, incremental execution 4, AI efficiency 5, reliability 5,
maintainability 4, portability 5, and security 5. Batch efficiency is scored
against the intentional one-source interactive contract; the remaining 4s
record measured validation/runtime cost, no persistent reload cache, and the
necessary custom worker/poller code.

### Slice 3r — release inventory and licence gate

The Windows build previously installed an unpinned PyInstaller at build time,
depended on whatever `bin/` happened to remain in an ignored release folder,
and shipped only incidental dependency notices. Its 488.4 MiB artifact was
stale: it contained 462.3 MiB of FFmpeg binaries but omitted the lazily imported
Pillow and NumPy runtimes needed by several discovered tools. There was no
machine-readable dependency manifest, SPDX SBOM, exact FFmpeg configuration,
binary hash evidence, or fail-closed unknown-licence gate.

The build now verifies a separately pinned PyInstaller, starts from a clean
folder, copies the required media workers deterministically, inventories the
actual PyInstaller analysis, derives FFmpeg's licence from its configure flags,
rejects nonfree or changed FFmpeg builds, rejects unknown Python distributions,
copies exact notices, and atomically emits a human table, JSON manifest, and
SPDX 2.3 document. The generated SPDX graph and JSON are read back and
reference-validated before the release passes.

| Review item | Evidence |
|---|---|
| Current behavior | Runtime package ranges were open-ended; the uv-managed environment had no `pip`, so the old build-time install path was fragile; ignored binaries could be absent or stale; release notices were hand-maintained and did not prove what the frozen artifact contained |
| Proposed behavior | Exact core/build pins, deterministic clean bundle, required FFmpeg/ffprobe copy, actual-TOC package discovery, strict audited licence policy, exact notice copy, FFmpeg flag/source evidence, SHA-256 release evidence, atomic manifest/table/SPDX output, and fail-closed validation |
| Architecture and language | A stdlib-only Python build gate reads PyInstaller's existing TOC instead of adding a runtime service or package scanner. PowerShell remains the Windows packaging entry point. No application framework, UI, tool, or custom panel was replaced |
| Functionality and quality | The clean artifact now includes all 17 discovered tools and the Pillow/NumPy runtime they use. A hidden frozen-app launch remained alive after four seconds. Processing algorithms and output bytes were not changed |
| Code and dependency impact | The gate is 541 nonblank build-only Python lines with 127 nonblank focused test lines; PyInstaller 6.21.0 is pinned in a separate build-only requirement; no new runtime dependency, model, worker, service, or startup import was added |
| Package-size impact | Stale artifact: 488.4 MiB/1,024 files. Fresh complete artifact: 534.7 MiB/1,103 files (+46.3 MiB, +9.5%) because NumPy/Pillow and all current tools are now actually frozen. FFmpeg/ffprobe remain 462.3 MiB (86.5%); compliance outputs and 32 notice files add 324,410 bytes. This is correctness evidence, not a size optimization claim |
| Startup and runtime impact | Build completed in 58.4 seconds. The gate is build-only, so it adds zero application calls or runtime imports. Frozen working set was 56.3 MiB after a four-second startup observation; there is no equivalent pre-change frozen baseline, so no improvement is claimed. Source ready-to-mainloop remains at the prior 113 ms median |
| RAM, VRAM, CPU, disk, and GPU transfer | Build analysis and hashing consume development-machine CPU/disk only. Release hashes stream in 1 MiB chunks; notice copies and metadata are bounded at 2 MiB per notice. No runtime RAM/VRAM/GPU transfer, model load, thread, process, or idle cost was added |
| Batch, cache, and incremental impact | Batch execution and cache keys are unchanged. Packaging is deliberately a clean deterministic rebuild rather than incremental; each public artifact gets fresh hashes and provenance |
| AI and model-loading impact | Zero AI calls and model loads. Exact metadata, installed distribution records, configure flags, hashes, and SPDX identifiers are sufficient |
| Reliability and security | Unknown bundled distributions, missing notices, a missing media worker, malformed/nonfree/unexpected FFmpeg, incomplete atomic writes, invalid JSON, duplicate SPDX IDs, or dangling SPDX references stop the build. The generator never executes package code; it reads metadata and the literal PyInstaller TOC |
| Tests and benchmarks | Six focused compliance tests cover FFmpeg classification/nonfree rejection, actual TOC ownership, scoped PyInstaller runtime-hook handling, unknown-package failure, atomic replacement, and SPDX reference rejection. Windows PowerShell 5.1 parsing, clean PyInstaller build, 12-component inventory, zero staging residue, and frozen startup pass |
| Risks and rollback | The bundled Gyan full build is intentionally GPL-3.0-or-later and dominates storage. Public distribution still requires corresponding FFmpeg/external-library source access beside the binary download; the build records and prints this obligation but cannot verify a future hosting page. The release gate is Windows-tested; macOS/Linux packaging still needs an equivalent entry point. Revert the build gate/script/notices/pins together to roll back; no user data or runtime schema is involved |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 5, startup 5, memory 5, storage 4, batch efficiency 5, cache
effectiveness 5, incremental execution 4, AI efficiency 5, reliability 5,
maintainability 4, portability 4, and security 5. Storage remains 4 because the
full static FFmpeg sidecar dominates the artifact; incremental execution is
intentionally traded for reproducible clean builds; maintainability and
portability remain 4 until another OS packaging path proves the policy registry
and platform-specific notice discovery.

### Slice 3s — grouped professional CustomTkinter shell

The former shell placed all 17 tools in a fixed 230-pixel sidebar. At the
1200×780 default the final tool was outside the available rail; at the 980×640
minimum five tools were unreachable and batch Results collapsed below a
non-scrollable page. It had no catalog search, work-area hierarchy, product
Home, recent tools, category context, or app-level keyboard routes. Navigation
also applied the icon font to mixed icon-and-text button strings, reducing text
quality.

The shell now starts on a focused Home dashboard and exposes six stable
destinations: Home, Queue, Images, Video & Audio, Game Assets, and Files & Data.
Metadata-driven category and search pages show all 17 tools in two-column cards.
Tools remain lazily built, retain their real panel objects and queue contracts,
and render inside a vertical scroll host. Category context stays highlighted;
Back, recent tools, live debounced search, and keyboard routes make navigation
predictable without creating an agent or workflow-builder UI.

| Review item | Evidence |
|---|---|
| Current behavior | Flat 17-tool rail; one clipped tool at 1200×780 and five at 980×640; no Home, grouping, search, recents, tool heading/back route, or overflow host; stale Queue empty-state named only two tools |
| Proposed behavior | Six-item workspace rail, purpose-led Home, four category pages, deterministic live catalog search, recent tools, category-preserving tool header/back route, scroll-safe panels, accurate Queue empty-state, and keyboard shortcuts |
| Architecture and language | Existing Python/CustomTkinter modular monolith remains. Pure `catalog.py` owns category/search rules; presentation-only `catalog_panel.py` owns cards/pages; `shell.py` composes them and continues passing `AppServices` to untouched lazy tool contracts |
| Functionality and quality | All 17 tools remain discoverable through category and search; all 17 real panels construct; all sixteen batch workflows submit through the shared queue; Sprite Viewer stays purpose-built. At 980×640 its lower controls remain reachable through the new host |
| Code and dependency impact | One focused catalog primitive, catalog views, shell navigation tests, and visual/runtime benchmarks were added. No runtime package, model, worker, service, engine, or processing algorithm was added or replaced |
| Package-size impact | Source-only UI change at this checkpoint; no package-size reduction is claimed. The final release build is measured separately |
| Startup and runtime impact | Pre-change five-run startup was 209 ms cold then 100–103 ms (102 ms warm median). Post-change was 165 ms cold then four 106 ms runs (106 ms warm median, +4 ms/+3.9%, within normal measured variance and the 250 ms budget). Real construction plus first paint measured 214.4–238.6 ms (219.5 ms median) |
| RAM, VRAM, CPU, disk, and GPU transfer | Post-change idle working set measured 41.6–41.9 MiB, one Python thread, zero tool/queue panels loaded, and 0–31.2 ms process CPU during a one-second Windows sample. No equivalent source-shell pre-measurement exists, so no memory/CPU improvement is claimed. No GPU, VRAM, disk scan, model, or transfer occurs on Home |
| Batch, cache, and incremental impact | Job definitions, execution, checkpoints, cache keys, and output validation are unchanged. Panels still build once on first use; catalog pages contain metadata only. Recent tools are intentionally session-local and add no state store |
| AI and model-loading impact | Zero AI calls/model loads added. Catalog grouping and search are exact metadata operations; optional tool models remain lazy |
| Reliability, security, tests, and benchmarks | 41 unit tests pass; isolated real-shell verification covers six nav destinations, every category, search, lazy build, context, and scroll host; 17/17 panels construct; sixteen production queue paths and history render; real screenshots cover Home/category/search/tool/minimum/Queue |
| Risks and rollback | Two-column cards assume the supported 980-pixel minimum; CustomTkinter remains limited compared with a native accessibility tree; recent tools do not persist across sessions; search is metadata rather than semantic. Revert the shell/catalog presentation commit while retaining tool engines, queue, registry, and category metadata |

Checkpoint cartridge score: functional completeness 5, output quality 5,
runtime 5, startup 5, memory 4, storage 5, batch efficiency 5, cache
effectiveness 5, incremental execution 5, AI efficiency 5, reliability 5,
maintainability 4, portability 5, and security 5. Memory is 4 because there is
no equivalent pre-change source-shell working-set baseline. Maintainability is
4 until a fifth work area proves the fixed catalog vocabulary remains sufficient.

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

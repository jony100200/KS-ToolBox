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

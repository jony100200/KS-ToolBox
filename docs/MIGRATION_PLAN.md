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

### Slice 3 — next: shared queue application service

After two contrasting tools pass, move worker ownership from individual panels
to one shell-owned queue service. Add queue history and pause/resume/cancel UI.
Do not create a second scheduler beside `BaseBatchPanel`; migrate adapters one
tool at a time.

### Slice 4 — priorities, dependencies, and resources

Add only for demonstrated workflows. Start with bounded CPU/disk/external-process
slots. GPU/model residency belongs later, when an integrated model tool needs it.

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

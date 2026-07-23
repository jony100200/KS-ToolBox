# Batch Core

`toolbox.batch_core` is a headless durable item executor. `toolbox.job_queue`
owns shell-wide submission, priority ordering, pause/resume/cancel controls,
immutable progress snapshots, and history. Together they remove recovery,
retry, failure-isolation, and worker ownership from migrated panels without
changing tool algorithms. `image_rescale`, `video_compressor`,
`audio_tool`, `icon_normalizer`, `pixel_art`, `material_converter`, mode-aware
`showcase`, and analytical `tileset_checker` are the first production
integrations.

## Job lifecycle

```text
Created → Queued → Preparing → Running ───────────────→ Completed
                              ├─ item exhausted ──────→ CompletedWithWarnings
                              ├─ every item failed ───→ Failed
                              ├─ pause request ───────→ Paused → Running
                              └─ cancel request → Cancelling → Cancelled

Interrupted Running item → Pending; job → Recovered → Running
```

Pause and cancellation are cooperative at item boundaries. A codec or native
process already executing must provide its own cancellable adapter before its
tool can promise mid-item interruption. Video Compressor uses the implemented
owned-process adapter and can stop FFmpeg, ffprobe, VMAF, or HandBrake mid-item.

## Item lifecycle

```text
Pending → Running → Completed
                  → CompletedWithWarnings
                  → Skipped
                  → retry same item only
                  → Quarantined after retry exhaustion
```

Quarantine is currently logical: the source is not moved or deleted. The report
and database identify the unresolved item. Physical quarantine requires an
explicit, user-approved file policy and is not implied by the runner.

`CompletedWithWarnings` preserves a useful degraded artifact while making its
uncertainty visible at both item and job level. It is distinct from quarantine:
the output may be used, but it is revalidated before reuse like a clean
completion.

## SQLite schema

`jobs` stores tool/workflow versions, normalized settings, retry policy, state,
and timestamps. `job_items` stores ordered input paths, state, attempts,
diagnostics, and JSON result data. Each item transition is transactional. WAL
mode and `synchronous=NORMAL` balance crash safety and unattended throughput.

The store is opened lazily on batch submission. Default locations are:

- Windows: `%LOCALAPPDATA%/KS Toolbox/jobs.sqlite3`
- macOS: `~/Library/Application Support/KS Toolbox/jobs.sqlite3`
- Linux: `$XDG_STATE_HOME/ks-toolbox/jobs.sqlite3`, or `~/.local/state/ks-toolbox`

`KS_TOOLBOX_STATE_DIR` provides an explicit portable/test override.

## Current executor protocol

```python
result = execute(input_path)
outcome = classify(result)
```

`execute` owns tool work. `classify` maps the existing tool result to a typed
`ItemOutcome`. Unhandled per-item exceptions become visible, non-retryable
quarantine records; they do not stop later items. Progress-subscriber failures
are isolated and returned in report diagnostics.

`JobDefinition.inputs` are executable item anchors. For grouped work such as a
PBR texture set, `identity_dependencies` adds every member file to deterministic
job identity without turning each member into an independently executable item.
Anchors are always included, dependency order is normalized, and changing any
member produces a new job ID. This prevents stale set-level reuse while keeping
one checkpoint and failure boundary per coherent set.

Completed artifact records are reusable only through a tool-supplied
`validate_stored` function. Invalid or missing artifacts are reset to Pending
and recomputed individually. Without a validator, completed artifact items are
rerun rather than trusted blindly.

For external engines, `run_cancellable_cmd()` starts one exact command without a
shell, captures output, enforces timeout, and terminates its owned process tree
on cancellation. The interrupted item returns to Pending; it is not recorded as
a malformed-file quarantine.

## Completion report

`write_completion_report()` atomically writes schema-versioned JSON containing:

- job and tool identifiers;
- final state and recovery/reuse flags;
- counts by item state;
- attempts and details for every input;
- tool result data;
- subscriber diagnostics.

`prepare_batch_completion()` decodes terminal item records once, writes a
tool-owned manifest plus the atomic JSON report, and returns typed
`BatchCompletionArtifacts`. Non-fatal I/O problems become `QueueFinalization`
warnings. The queue then records `CompletedWithWarnings`, exposes the warning
text in history, and keeps successful item results available to the panel.
Durable `BaseBatchPanel` tools call `_prepare_queue_completion()` so tool ID,
output-root/dry-run routing, result decoding, manifest writing, and report
creation cannot drift across panels.

## Scope still required

The current slice does not yet provide job dependencies, scheduled start,
shutdown-on-finish, resource reservations, physical quarantine, or manual
item reset/retry controls. Those remain migration work, not claimed
capabilities. Priority affects waiting jobs; the intentionally single execution
lane avoids unmeasured CPU, disk, and codec oversubscription.

## Cartridge-grade score for this slice

| Category | Score | Evidence or reason |
|---|---:|---|
| Functional completeness | 4 | Durable runner, priority queue, controls, and history work; dependencies and scheduling remain |
| Output quality | 5 | Image algorithm unchanged; video candidate commits only after VMAF policy |
| Runtime performance | 4 | 8,651–10,508 durable no-op item transitions/s |
| Startup efficiency | 5 | Queue worker and store remain absent until submission; no optional-heavy startup import |
| Memory efficiency | 4 | Item metadata is linear and small for the supported batch scale |
| Storage efficiency | 4 | About 270 KB for 1,000 checkpointed items; no new package |
| Batch efficiency | 4 | Per-item retry and completed/valid artifact reuse |
| Cache effectiveness | 3 | Checkpoint reuse exists, but content-addressed artifact caching is deliberately deferred |
| Incremental execution | 4 | Only pending or invalid completed items rerun |
| AI efficiency | 5 | Exact deterministic orchestration; no model involved |
| Reliability | 4 | Transactional state, crash recovery, quarantine, atomic report, owned-process cancellation |
| Maintainability | 4 | Two focused core/adapter modules with behavioral tests |
| Portability | 4 | Python standard library and OS-correct state paths |
| Security | 4 | Strict JSON data, parameterized SQL, no shell execution, local-only state |

The cache score remains below four because hashing and validated artifact-cache
eviction need representative workloads and are explicitly scheduled after the
second contrasting tool. Calling checkpoint identity a content cache would be
incorrect.

# KS ToolBox Architecture

Verified against the repository on 2026-07-22. KS ToolBox is an existing
CustomTkinter modular monolith. This document describes the current system and
the allowed direction of incremental migration; it is not a rewrite brief.

## System shape

```text
CustomTkinter shell, tool panels, and Queue/History view
        │ requests / immutable snapshot polling
        ▼
AppServices + shell-owned JobQueue
        │ QueueSubmission + JobDefinition + CancellationToken
        ▼
Tool registry + lazy single-lane BatchRunner
        │ direct per-item execution
        ├──────────────► headless tools/<name>/engine.py
        │
        └──────────────► infrastructure adapters
                         SQLiteJobStore, filesystem, FFmpeg, Pillow, native tools
```

The first durable vertical slices are `image_rescale` and `video_compressor`.
Other tools still use the existing `BaseBatchPanel` loop until migrated and
verified individually.

## Allowed dependency directions

```text
presentation ──► application/batch contracts ──► tool engine
                              │
                              └───────────────► infrastructure contract
infrastructure adapter ──► batch contracts
tool engine ──► toolbox.engine_common
```

- `toolbox/shell.py` owns `AppServices`, the queue view, the tool registry, and
  shared UI elements; it does not implement tool processing.
- A tool may import public names from `toolbox`; it must not import another tool.
- Engine modules are headless and must not import CustomTkinter.
- `batch_core.py` has no UI, database, codec, model, or tool dependency.
- `sqlite_job_store.py` implements the storage boundary using the standard library.
- `engine_common.run_cancellable_cmd` owns cancellable external process trees.
- UI widgets must not call FFmpeg, models, or processing algorithms directly.

## Implemented contracts

| Contract | Owner | Purpose |
|---|---|---|
| `Tool` / `ToolMeta` | `toolbox/tool.py` | Lazy first-party tool registration and UI construction |
| `AppServices` | `toolbox/application.py` | Explicit shell-owned service context passed to panels |
| `JobQueue` | `toolbox/job_queue.py` | Lazy priority queue, pause/resume/cancel, snapshots, and history |
| `QueueSubmission` | `toolbox/job_queue.py` | Tool execution, classification, validation, and finalization contract |
| `JobDefinition` | `toolbox/batch_core.py` | Stable tool, workflow, inputs, settings, and retry identity |
| `ItemOutcome` | `toolbox/batch_core.py` | Typed completed, skipped, failed, or quarantined result |
| `CancellationToken` | `toolbox/batch_core.py` | Cooperative pause/cancel at safe item boundaries |
| `JobStore` | `toolbox/batch_core.py` | Small persistence boundary used by the runner |
| `SQLiteJobStore` | `toolbox/sqlite_job_store.py` | Transactional jobs and per-item checkpoints |
| error envelope | `toolbox/engine_common.py` | Headless engine error-as-value convention |

## Deliberately not implemented yet

- Job dependency graphs, scheduled start, and shutdown-on-finish.
- Resource reservations and hardware-aware multi-lane scheduling. The current
  single execution lane is deliberately bounded and predictable.
- A general event bus.
- Public/untrusted plugins.
- A workflow VM or JSON programming language.
- Rust/native scheduling code without profiling evidence.
- A PySide6 migration; CustomTkinter remains the presentation framework.

These are introduced only when at least two contrasting migrated tools prove
the boundary and a current user-facing requirement needs them.

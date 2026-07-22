# KS ToolBox Architecture

Verified against the repository on 2026-07-22. KS ToolBox is an existing
CustomTkinter modular monolith. This document describes the current system and
the allowed direction of incremental migration; it is not a rewrite brief.

## System shape

```text
CustomTkinter shell and tool panels
        │ requests / progress callbacks
        ▼
Application coordination
        │ JobDefinition + CancellationToken
        ▼
Tool registry + BatchRunner
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

- `toolbox/shell.py` depends only on the tool registry and shared UI elements.
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
| `JobDefinition` | `toolbox/batch_core.py` | Stable tool, workflow, inputs, settings, and retry identity |
| `ItemOutcome` | `toolbox/batch_core.py` | Typed completed, skipped, failed, or quarantined result |
| `CancellationToken` | `toolbox/batch_core.py` | Cooperative pause/cancel at safe item boundaries |
| `JobStore` | `toolbox/batch_core.py` | Small persistence boundary used by the runner |
| `SQLiteJobStore` | `toolbox/sqlite_job_store.py` | Transactional jobs and per-item checkpoints |
| error envelope | `toolbox/engine_common.py` | Headless engine error-as-value convention |

## Deliberately not implemented yet

- A global queue controller or queue screen; two contrasting tools now prove
  the executor boundary needed to build it.
- Priorities and dependency graphs.
- A general event bus.
- Public/untrusted plugins.
- A workflow VM or JSON programming language.
- Rust/native scheduling code without profiling evidence.
- A PySide6 migration; CustomTkinter remains the presentation framework.

These are introduced only when at least two contrasting migrated tools prove
the boundary and a current user-facing requirement needs them.

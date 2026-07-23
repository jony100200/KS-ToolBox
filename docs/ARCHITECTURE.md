# KS ToolBox Architecture

Verified against the repository on 2026-07-23. KS ToolBox is a CustomTkinter
modular monolith with 18 first-party tools. This document describes the current
system and its allowed incremental direction; it is not a rewrite brief.

## System shape

```text
CustomTkinter grouped shell, catalog pages, tool panels, and Queue/History
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

Seventeen tools submit work through the shell-owned queue: the media, image,
asset, archive, audit, dataset, and external-render tools. `image_enhancer`
uses the same durable queue with local optional models. `Sprite Viewer` is the
one purpose-built interactive panel: its bounded worker owns loading and export,
not an unattended batch workflow.

## Allowed dependency directions

```text
presentation ──► application/batch contracts ──► tool engine
                              │
                              └───────────────► infrastructure contract
infrastructure adapter ──► batch contracts
tool engine ──► toolbox.engine_common
```

- `toolbox/shell.py` owns `AppServices`, top-level navigation, lazy page
  composition, the queue view, and the tool registry; it does not implement
  tool processing.
- `toolbox/catalog.py` owns deterministic categories, validation, and search
  ranking without importing widgets or engines.
- `toolbox/catalog_panel.py` renders metadata-driven Home, category, search,
  and tool cards. It receives navigation callbacks and cannot process files.
- Tool pages keep the actual verified panel in a scroll-safe lazy host. Category
  selection remains highlighted while its tool is open.
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
| `ToolCategory` / catalog search | `toolbox/catalog.py` | Four stable work areas, fail-closed category validation, and deterministic search |
| `AppServices` | `toolbox/application.py` | Explicit shell-owned service context passed to panels |
| `JobQueue` | `toolbox/job_queue.py` | Lazy priority queue, pause/resume/cancel, snapshots, and history |
| `QueueSubmission` | `toolbox/job_queue.py` | Tool execution, classification, validation, and finalization contract |
| `BatchCompletionArtifacts` | `toolbox/batch_reporting.py` | Shared typed results, manifest, and atomic morning-report finalization |
| `JobDefinition` | `toolbox/batch_core.py` | Stable executable-item anchors plus complete file-dependency identity |
| `ItemOutcome` | `toolbox/batch_core.py` | Typed completed, warning, skipped, failed, or quarantined result |
| `CancellationToken` | `toolbox/batch_core.py` | Cooperative pause/cancel at safe item boundaries |
| `JobStore` | `toolbox/batch_core.py` | Small persistence boundary used by the runner |
| `SQLiteJobStore` | `toolbox/sqlite_job_store.py` | Transactional jobs and per-item checkpoints |
| `VolatileJobStore` | `toolbox/volatile_job_store.py` | Explicit session-only fallback when SQLite cannot load in the current process |
| error envelope | `toolbox/engine_common.py` | Headless engine error-as-value convention |
| file integrity/collision helpers | `toolbox/engine_common.py` | Shared cancellable streaming hashes and normalized output-path safety checks |
| media-duration probe | `toolbox/engine_common.py` | Shared cancellable ffprobe execution and finite-positive duration validation |

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

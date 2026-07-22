# Event Policy

KS ToolBox does not currently need a general event bus. Direct calls remain the
default within a tool pipeline. `JobQueue` stores immutable snapshots and offers
isolated subscribers for headless consumers; CustomTkinter reads snapshots by
main-thread polling. This keeps execution traceable and prevents worker-to-Tk
calls.

## Commands and facts

- Commands request work: `QueueJob`, `PauseJob`, `ResumeJob`, `CancelJob`,
  `RetryItem`, `OpenReport`.
- Events describe completed facts: `JobQueued`, `JobStarted`, `JobProgressed`,
  `JobPaused`, `JobCompleted`, `JobFailed`, `ItemQuarantined`, `CacheHit`,
  `ResourcePressureDetected`.

The command methods and queue lifecycle states are implemented; named event
objects are not. Events should be introduced only when a real second subscriber
or cross-system lifecycle boundary exists.

## Rules for an implemented event

- Use a typed immutable payload with `schema_version`, `event_id`, `job_id`, and
  timestamp where relevant.
- Name the publisher and all subscribers in this document.
- Preserve deterministic order for events from one job.
- Isolate and report subscriber errors.
- Provide unsubscribe/cleanup for long-lived UI subscribers.
- Do not use wildcard subscribers.
- Do not hide business logic entirely in callbacks.
- Persist lifecycle facts only when recovery or audit requires them.

## Current publisher catalogue

| Publisher | Signal | Subscriber | Delivery |
|---|---|---|---|
| `BatchRunner` | `BatchProgress` | `JobQueue` | direct typed callback on queue worker |
| `JobQueue` | `QueueSnapshot` | optional headless subscriber | synchronous, exception-isolated |
| `JobQueue` | stored snapshot/completion | migrated panel and Queue/History view | read-only polling on Tk main thread |

No invisible multi-hop event chains exist. A future structured logger may
subscribe after its audit and retention requirements are defined.

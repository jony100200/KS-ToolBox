# Event Policy

KS ToolBox does not currently need a general event bus. Direct calls remain the
default within a tool pipeline; typed progress callbacks cross the current
runner/presentation boundary. This keeps execution traceable.

## Commands and facts

- Commands request work: `QueueJob`, `PauseJob`, `ResumeJob`, `CancelJob`,
  `RetryItem`, `OpenReport`.
- Events describe completed facts: `JobQueued`, `JobStarted`, `JobProgressed`,
  `JobPaused`, `JobCompleted`, `JobFailed`, `ItemQuarantined`, `CacheHit`,
  `ResourcePressureDetected`.

This catalogue reserves names; it does not claim those events are implemented.
Events should be introduced only when a real second subscriber or cross-system
lifecycle boundary exists.

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
| `BatchRunner` | `BatchProgress` | migrated panel | direct typed callback |
| panel worker | UI update | CustomTkinter loop | `after(0, ...)` |

No invisible multi-hop event chains exist. A future queue controller may publish
job lifecycle events to the queue view and structured logger after its contract
is proven by two tools.

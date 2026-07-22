# Recovery Model

Recovery protects completed work without hiding failure. The first implemented
path covers item batches submitted through `BatchRunner`.

```text
submit deterministic JobDefinition
→ create/validate SQLite job and ordered items
→ mark item Running transactionally
→ tool writes temporary output and atomically commits
→ persist typed result
→ continue after isolated failure
→ write atomic completion report
```

## Restart flow

```text
open matching job
→ validate tool/workflow/settings/input identity
→ Running items become Pending
→ completed/skipped/quarantined records remain intact
→ job records Recovered
→ execute pending items only
```

A completed matching job revalidates stored completed artifacts. When every
artifact remains valid, it returns `reused=true` with zero executor calls. A
missing or invalid artifact is reset and only that item is recomputed.

## Failure boundaries

| Failure | Current behavior |
|---|---|
| malformed input | item becomes logical quarantine; later items continue |
| retryable item result | only that item retries, up to declared maximum |
| unexpected item exception | visible non-retryable quarantine record |
| progress subscriber exception | work continues; report diagnostic records it |
| cancellation | job becomes Cancelled; pending items remain resumable |
| external process cancellation | owned process tree stops; candidate is removed; item returns to Pending |
| process/power loss | prior Running item resets to Pending on next prepare |
| report/manifest write failure | queue and panel show CompletedWithWarnings; database state and outputs remain |
| SQLite open/write failure | panel reports batch-core failure; tool output is not assumed complete |

Tool engines remain responsible for temporary output cleanup and atomic
replacement. Existing `.part` sweeping remains scoped to declared output roots.
Video Compressor writes a `.verify` candidate, runs VMAF against it, and exposes
the final destination only after acceptance. A crash cannot make an unverified
candidate look like a completed output.

The Queue/History view loads the most recent SQLite reports only when opened.
Terminal records retain their stored state. A nonterminal record from a prior
process is displayed as `Recovered` with an explicit instruction to resubmit it
from the owning tool; submission then reuses valid item checkpoints.

## Trust and privacy

The local database contains source paths, settings, and result metadata. It is
not uploaded. Structured results are treated as data and JSON-validated. A
future diagnostic exporter must redact paths or content only under an explicit
user-selected policy.

## Not implemented

Manual retry/reset UI, physical file quarantine, free-space reservation,
cross-process worker leases, and rollback of external-tool side effects remain
future work. They must not be advertised as current behavior.

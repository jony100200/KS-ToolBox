# Workflow Steps

The current tools expose direct headless operations such as
`image_rescale.engine.process(path, options)`. The durable runner treats that
operation as one item step. This is sufficient for the first vertical slice and
avoids inventing a workflow VM.

## Current step boundary

```text
validated JobDefinition
→ begin item checkpoint
→ tool engine process(path, options)
→ classify existing result
→ persist result or quarantine
→ publish progress
```

Tool engines remain responsible for safe output planning, temporary files,
atomic commit, and format validation.

Classification distinguishes clean completion, usable completion with warnings,
skip, and failure. A warning preserves the degraded artifact, makes the job
state visibly warning-bearing, and requires the same stored validation as a
clean completion before future reuse. Retry exhaustion remains quarantine.

## Typed multi-step contract gate

A reusable `WorkflowStep` contract will be added when a migrated tool needs
checkpointable stages rather than a single per-file operation. Each real step
must then declare:

- stable ID and version;
- input/output artifact types;
- deterministic or nondeterministic behavior;
- side effects and atomic-commit boundary;
- cache and validation policy;
- CPU/GPU/disk/memory profile;
- retry and cancellation behavior.

Steps use direct calls inside one pipeline. No JSON bytecode or event chain is
planned.

## Cache-key specification

Implemented job/checkpoint identity:

```text
tool ID + tool version + workflow version
+ normalized settings + retry policy
+ normalized executable input paths + size + mtime_ns
+ optional normalized identity-dependency paths + size + mtime_ns
```

This identity safely locates a matching checkpoint. A tool validator must still
verify completed artifacts before reuse; missing or invalid outputs are
recomputed individually. It is not yet a content-addressed artifact cache.
Identity dependencies support a grouped item whose result depends on several
files; they affect the job key but do not create extra executable items.

The future artifact cache key must be:

```text
tool version + step version + normalized settings
+ cryptographic input content hashes
+ model/runtime version when output-affecting
+ relevant environment factors
```

Hashing and cache storage will be added only with incremental hashing and output
validation; path/mtime identity must not be mislabeled as content hashing.

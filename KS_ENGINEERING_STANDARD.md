# KS Cartridge-Grade Engineering Standard

Build this software with the discipline used to fit ambitious games into tiny cartridges.

The project may grow in features and total size, but the optimization standard never changes:

> Every line of code, dependency, allocation, process, model, file, abstraction, and computation must earn its place.

The goal is not the smallest possible application. The goal is the **smallest, fastest, most reliable implementation that delivers the complete professional-grade functionality**.

## Core requirements

- Preserve or improve all useful functionality and output quality.
- Optimize at the architectural, algorithmic, runtime, operating-system, and hardware levels.
- Prefer simple, direct solutions over layers of unnecessary abstraction.
- Reuse shared primitives instead of duplicating implementations.
- Do not add code for hypothetical future requirements.
- Do not leave unused code, dependencies, assets, models, workers, or compatibility layers in production.
- Do not sacrifice reliability, maintainability, security, or usability merely to reduce binary size.
- Do not accept avoidable bloat merely because modern computers have more storage.

## Method selection

Use the fastest reliable method that satisfies the required quality:

1. Reuse a validated cached result.
2. Use an exact deterministic algorithm.
3. Use metadata, rules, heuristics, retrieval, indexing, or constraint solving.
4. Use a small specialist or utility model.
5. Use a small generative model.
6. Use a larger AI model when the task genuinely requires it.

AI is fully allowed when it provides meaningful capability, flexibility, interpretation, classification, generation, or quality that deterministic methods cannot provide.

Do not use AI for work that an exact algorithm can perform faster and more reliably.

Do not force an inadequate deterministic approach when an appropriate model produces substantially better results.

Validate AI outputs using deterministic schemas, constraints, measurements, and post-processing wherever possible.

## Technical freedom

Choose technology based on measured requirements, not familiarity or fashion.

Use:

- Rust for safe, compact systems components, concurrency, scheduling, resource management, and portable native cores.
- C++ for established native libraries, codecs, inference runtimes, graphics, media processing, and performance-critical kernels.
- C for minimal operating-system interfaces, embedded components, and extremely constrained low-level code where justified.
- Python for rapid development, AI integration, build tooling, experimentation, and workflows where its runtime cost is acceptable.
- GPU kernels, SIMD, native operating-system APIs, memory mapping, shared memory, process isolation, or custom binary formats when measurements justify them.

Do not rewrite mature, optimized libraries without strong evidence that a custom implementation will be smaller, faster, safer, or easier to maintain.

Do not add a large framework when a focused component or direct platform API can perform the required job.

## Performance discipline

Design every feature for:

- fast startup;
- responsive interaction;
- efficient unattended batch processing;
- bounded RAM and VRAM;
- low idle CPU use;
- minimal disk access;
- minimal temporary storage;
- minimal process and thread count;
- efficient CPU-GPU transfers;
- predictable cancellation;
- safe crash recovery.

Apply where appropriate:

- lazy loading;
- streaming;
- memory mapping;
- zero-copy or low-copy data paths;
- object and buffer reuse;
- content-addressed caching;
- incremental recomputation;
- operation fusion;
- batched inference;
- vectorization and SIMD;
- bounded concurrency;
- hardware-aware scheduling;
- model residency and eviction policies;
- atomic writes;
- resumable checkpoints;
- deterministic validation.

Never initialize GPU runtimes, AI models, media engines, databases, or workers until a requested feature needs them.

## Batch-processing standard

For every batch workflow:

```text
Discover inputs
→ identify unchanged or completed work
→ validate formats and settings
→ group compatible operations
→ process deterministic stages
→ route only uncertain items to AI
→ validate outputs
→ retry failed items only
→ quarantine unresolved items
→ commit results atomically
→ save provenance and completion report
```

One malformed file must not stop an entire batch.

Long-running jobs must support:

- pause;
- resume;
- cancellation;
- checkpoints;
- per-item retry;
- failure quarantine;
- crash recovery;
- clear progress;
- reproducible manifests.

## Dependency standard

Every dependency must have a documented purpose.

Before adding one, determine:

- exact functionality provided;
- package-size cost;
- installed-size cost;
- startup cost;
- RAM and runtime cost;
- transitive dependencies;
- security surface;
- licensing obligations;
- smaller alternatives;
- whether an existing component already provides the function.

Reject dependencies that:

- duplicate existing capabilities;
- are used for a trivial operation;
- load large unused subsystems;
- create fragile installation requirements;
- introduce unnecessary background services;
- provide less value than their operational cost.

Development-only, testing, debugging, and build dependencies must not ship in production unless required at runtime.

## Code standard

Every module, class, function, and abstraction must answer:

1. What exact job does it perform?
2. Which current feature requires it?
3. Why can an existing primitive not perform the same job?
4. What disk, memory, runtime, and maintenance cost does it add?
5. How is it tested?
6. What happens if it fails?
7. Can it be removed without changing observable behavior?

Remove:

- unreachable code;
- unused configuration;
- speculative abstractions;
- unnecessary wrappers;
- duplicate helpers;
- repeated conversions;
- redundant serialization;
- repeated file decoding;
- repeated model inference;
- unnecessary allocations and copies;
- verbose data structures where compact representations are appropriate.

Do not compress code into unreadable tricks merely to reduce source-line count. Optimize generated machine behavior while keeping the implementation understandable and maintainable.

## Architecture standard

Keep universal mechanisms in the core and specialized capabilities outside it.

The permanent core should contain only broadly required facilities such as:

- job execution;
- resource management;
- caching;
- validation;
- recovery;
- permissions;
- logging;
- configuration;
- shared UI foundations.

Specialized functionality should remain modular and load on demand:

- OCR;
- transcription;
- AI models;
- video processing;
- 3D processing;
- game-asset tools;
- document engines;
- uncommon codecs;
- format-specific extractors.

Avoid forcing every user to pay the storage, memory, startup, and dependency cost of features they are not using.

## Reliability and security

Optimization is invalid if it makes the software fragile.

Require:

- strict input validation;
- path traversal protection;
- safe archive extraction;
- atomic file replacement;
- rollback or quarantine for destructive operations;
- resource limits;
- bounded queues;
- model-output validation;
- explicit network permissions;
- safe subprocess handling;
- integrity checks;
- useful audit logs.

Treat AI-generated plans, filenames, paths, commands, and structured outputs as untrusted input.

## Measurement requirement

Do not claim that code is optimized without evidence.

Record before-and-after measurements for significant changes:

- release package size;
- installed size;
- dependency count;
- startup time;
- idle RAM;
- peak RAM;
- peak VRAM;
- CPU use;
- disk reads and writes;
- temporary storage;
- processing throughput;
- model-loading time;
- number of AI calls;
- cache hit rate;
- failure-recovery behavior;
- test results;
- output-quality comparison.

Measure representative real workloads, not only artificial microbenchmarks.

## Optimization score

Score every major feature from 0 to 5:

| Category | Requirement |
|---|---|
| Functional completeness | No useful capability lost |
| Output quality | Meets professional standards |
| Runtime performance | Fast on realistic workloads |
| Startup efficiency | Loads only essential components |
| Memory efficiency | Controls RAM and VRAM |
| Storage efficiency | No unjustified files or dependencies |
| Batch efficiency | Avoids repeated work |
| Cache effectiveness | Reuses valid results |
| Incremental execution | Recomputes only affected stages |
| AI efficiency | Uses the smallest sufficient method |
| Reliability | Recovers safely from failure |
| Maintainability | Remains clear and testable |
| Portability | Works without fragile setup |
| Security | Enforces strict trust boundaries |

Any category below 4 requires either improvement or a documented technical reason.

## Change-report requirement

For every significant implementation or optimization, report:

1. Current behavior.
2. Proposed behavior.
3. Why the chosen architecture and language are appropriate.
4. Functionality and quality impact.
5. Code and dependency impact.
6. Package-size impact.
7. Startup and runtime impact.
8. RAM, VRAM, CPU, disk, and GPU-transfer impact.
9. AI usage and model-loading impact.
10. Reliability and security impact.
11. Tests and benchmarks.
12. Risks and rollback procedure.

## Merge gate

Do not merge a change when it:

- adds complexity without measurable value;
- duplicates an existing capability;
- reduces output quality;
- weakens reliability or security;
- adds unjustified dependencies;
- increases startup or idle resource use unnecessarily;
- performs repeated work that can be cached;
- invokes AI where an exact method is sufficient;
- introduces speculative code without a current feature;
- moves complexity elsewhere instead of removing it.

## Final engineering principle

> Use modern hardware, storage, AI, and native performance technologies when they deliver real value—but spend every byte and every computation with the discipline of an 8 MB cartridge.

Build large functionality from a compact set of carefully designed mechanisms. Make the software feel powerful because its architecture is intelligent, not because it carries unnecessary weight.

# KS ToolBox documentation

Start with the repository [README](../README.md) for installation, the tool
catalogue, and the safe batch workflow. Each tool has a focused guide in its own
`tools/<name>/README.md`; those guides are the operator reference for formats,
options, output, optional dependencies, and the matching smoke test.

## Current engineering reference

| Need | Read |
|---|---|
| Add or change a tool | [Adding a First-Party Tool](ADDING_A_TOOL.md) and [Contributing](../CONTRIBUTING.md) |
| Understand the shell, queue, and dependency boundaries | [Architecture](ARCHITECTURE.md) |
| Implement durable batch execution | [Batch Core](BATCH_CORE.md), [Recovery Model](RECOVERY_MODEL.md), and [Workflow Steps](WORKFLOW_STEPS.md) |
| Follow UI or module rules | [UI Guidelines](UI_GUIDELINES.md) and [First-Party Module System](MODULE_SYSTEM.md) |
| Measure a change | [Performance Budgets](PERFORMANCE_BUDGETS.md) and [`benchmarks/`](../benchmarks/README.md) |
| Build a portable release | [README build instructions](../README.md#build-a-portable-release) and [Third-Party Notices](../THIRD_PARTY_NOTICES.md) |
| Decide whether an event abstraction is justified | [Event Policy](EVENT_POLICY.md) |

## Historical and planning records

These documents preserve evidence and decisions from earlier work. They are
useful context, but they do not override current source, tool guides, or the
engineering references above.

- [Incremental Batch-Core Migration](MIGRATION_PLAN.md)
- [Performance and Intelligence Audit](KS_TOOLBOX_OPTIMIZATION_AUDIT.md)
- [Optimization Plan](KS_TOOLBOX_OPTIMIZATION_PLAN.md)
- [Extraction Map](EXTRACTION_MAP.md)

## Documentation rules

- Update a tool README when its formats, controls, output, dependency, safety
  behavior, or verification command changes.
- Keep current documents operational: state what is available today and link to
  the test that proves it.
- Keep measurements dated and identify their machine/context. Do not turn an
  historical benchmark or migration note into a present-tense product claim.
- Keep release and third-party records factual and generated-artifact-aware;
  they are not substitutes for the per-release build gate.

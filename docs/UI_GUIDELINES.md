# CustomTkinter UI Guidelines

CustomTkinter is the official KS ToolBox presentation framework. Replacing it
would rewrite 17 verified panels without improving batch reliability or output
quality. A framework change requires measured failure against a current need.

## Standard flow

```text
Tool Browser → Inputs → Preset/Settings → Preview → Output
             → Run/Add to Queue → Progress → Results/Report
```

The shell implements the full route for Image Rescale and Video Compressor,
including a Queue/History view. Other panels keep their verified UI and migrate
to the shared queue one at a time.

## Separation

- View: CustomTkinter widgets, layout, input display, and result formatting.
- View coordination: collect validated options and translate typed progress.
- Application/core: job lifecycle, retry, persistence, recovery, and reports.
- Engine: processing algorithms and output validation.

Widgets must not call FFmpeg, models, or native engines directly. Queue workers
never call Tk, including `after()`. Migrated panels and the queue view poll
immutable snapshots from Tk's main-thread timer. Compatibility workers may
schedule immutable results with `after()`, but never mutate widgets directly.

## Shared interaction rules

- Use `toolbox.theme`, `components`, and `icons`; do not hardcode colors.
- Make preview/dry-run the safe default where output can be destructive or large.
- Keep basic settings visible and group specialist controls separately.
- Show input count, output location, current state, progress, and per-item result.
- Reuse `BaseBatchPanel` terminal status/report handling; a tool supplies only
  its domain-specific summary text.
- Expose Stop for long work. Pause appears only when the executor genuinely
  supports safe cooperative pause.
- Explain failures with file, stage, reason, retry status, and next action.
- Preserve keyboard traversal and readable focus states as controls evolve.
- Keep panels scalable at the existing 980×640 minimum shell size.

## Framework reconsideration gate

Re-evaluate only with evidence of an essential unmet requirement such as queue
table performance, accessibility, complex docking, or GPU-backed preview. The
headless core must remain reusable if a future presentation is added.

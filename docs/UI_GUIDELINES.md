# CustomTkinter UI Guidelines

CustomTkinter is the official KS ToolBox presentation framework. Replacing it
would rewrite 17 verified panels without improving batch reliability or output
quality. A framework change requires measured failure against a current need.

## Standard flow

```text
Home or Search → Work Area → Tool → Inputs → Preset/Settings
               → Preview → Output → Run/Add to Queue → Results/Report
```

The shell exposes six stable top-level destinations: Home, Queue, Images,
Video & Audio, Game Assets, and Files & Data. Never restore one navigation item
per tool. Tools are discovered and grouped by `ToolMeta.category`; a new tool
appears in search and its category without shell edits.

Home gives the product purpose, one primary search, four work-area cards,
recently used tools, and a direct queue route. Category and search pages use the
same cards and explicit Open action. Opening a tool preserves category context
and provides a visible Back action.

All sixteen batch workflows use the shared queue. Sprite Viewer intentionally
keeps its verified interactive worker because it is not an unattended batch.

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
- Keep top-level navigation between five and ten stable workspaces. Tools belong
  in the searchable catalog, not in a permanently expanded sidebar.
- Put an icon, title, plain-language subtitle, and one clear Open action on tool
  cards. Do not expose tool settings before the user chooses a tool.
- Keep the currently selected workspace visible while a tool is open.
- Make preview/dry-run the safe default where output can be destructive or large.
- Keep basic settings visible and group specialist controls separately.
- Show input count, output location, current state, progress, and per-item result.
- Reuse `BaseBatchPanel` terminal status/report handling; a tool supplies only
  its domain-specific summary text.
- Expose Stop for long work. Pause appears only when the executor genuinely
  supports safe cooperative pause.
- Explain failures with file, stage, reason, retry status, and next action.
- Preserve keyboard traversal and readable focus states as controls evolve.
- Support `Ctrl/Cmd+K` for tool search, `Ctrl/Cmd+1` for Home,
  `Ctrl/Cmd+2` for Queue, `Alt+Left` for category return, Enter to search,
  and Escape to clear search.
- Every tool page uses a vertical scroll host. At the 980×640 minimum, controls
  may move below the fold but must remain reachable; they must never be clipped
  by a non-scrollable shell.
- Disabled destructive actions use a neutral surface. Danger color appears only
  when Stop/Cancel is actionable.
- Verify real screenshots at 1200×780 and 980×640 with
  `python -m benchmarks.capture_ui`; do not infer layout quality from widget tests.

## Framework reconsideration gate

Re-evaluate only with evidence of an essential unmet requirement such as queue
table performance, accessibility, complex docking, or GPU-backed preview. The
headless core must remain reusable if a future presentation is added.

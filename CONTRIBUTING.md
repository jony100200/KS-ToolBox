# Contributing to KS ToolBox

KS ToolBox is **one UI shell, many single-purpose batch tools**. Each tool is a
self-contained, auto-discovered plugin folder. Adding a tool means **dropping a
folder in `tools/`** — no edits to the shell, `main.py`, or any other tool.


## The architecture in one breath

```
toolbox/           the framework — shell, discovery, theme, shared widgets & helpers
  batch_panel.py     BaseBatchPanel: shared file-list UI (extend this for file-batch tools)
  engine_common.py   error-envelope, binary resolution, subprocess runner, extension sets
tools/<name>/      one tool, end to end
  engine.py          PURE logic — headless, no customtkinter, returns the error envelope
  panel.py           thin CustomTkinter UI — work never blocks or mutates the Tk loop
  tool.py            TOOL metadata + build_panel (LAZILY imports the panel)
  __init__.py        TOOL = <Name>Tool()
  test_smoke.py      boots + produces valid output on a real sample
  README.md          what it does, deps, options
```

## The rules (non-negotiable)

1. **Engine ≠ UI.** All logic lives in `engine.py`, pure and headless — no
   CustomTkinter import, no global state. It returns the standard envelope
   `{error, error_type, retryable, degraded, details, data}`. The panel only
   renders and submits durable work to the shell queue. Existing unmigrated
   tools temporarily use the shared panel's compatibility worker.
2. **Errors are values.** No `eval()`, no `except: pass`, no silent fallback. A
   fallback must announce itself (a status, a flag, a `degraded` envelope).
3. **Batch/destructive = Preview + Confirm + Logging.** Every batch tool defaults
   to a dry-run preview, confirms before deleting originals, and writes a per-run
   manifest CSV.
4. **Heavy deps import lazily.** `tool.py` must import its panel/engine *inside*
   `build_panel`, and the engine imports numpy/onnxruntime/vtracer/etc. inside the
   function that uses them — so the app discovers and lists the tool even on a
   machine that hasn't installed that tool's deps. A missing dep is announced
   in-panel, never a startup crash. (customtkinter is core and always present.)
5. **Cross-platform.** Guard OS-specific calls with `os.name`; resolve external
   binaries via the bundled `bin/` first then PATH (`engine_common.resolve_tool`);
   never pop a console (`engine_common.run_cmd` handles this); write atomically
   (`.part` temp → `os.replace`).
6. **No tool ships unverified.** `test_smoke.py` must boot the tool with default
   config and produce a valid output on a real sample. If it needs an uninstalled
   dep, it **skips cleanly** (prints SKIP, returns 0) — it never fails for a
    missing optional dependency. Update the tool's README.md with supported
    formats, controls, and options.

## Writing a file-batch tool the easy way

Extend `toolbox.batch_panel.BaseBatchPanel` — it gives you the files card, the
run/pause/stop row, the output-folder picker, queue polling, the results log,
and the stale-`.part` sweep for free. New tools implement `_build_submission`
plus their tool-specific result formatting, and reuse
`batch_reporting.prepare_batch_completion` for reports. The `_work` hook remains only for
incremental migration of existing verified tools. Use `self._build_output_row`
and `self._build_run_row` at the end of your options card, and
`self._resolve_input_root()` for mirror mode. See `tools/image_rescale/` as the
canonical example.

Tools with a different shape (e.g. directory + engine-exe, like
`tools/texture_renderer/`) can be a plain `ctk.CTkFrame` panel — only the
module-level `TOOL` is required.

## Verifying your tool

From the repo root, using an isolated uv overlay so you don't touch the app venv
(include `customtkinter` plus your tool's own deps):

```
uv run --no-project --with customtkinter --with <your-deps> python -m tools.<name>.test_smoke
```

And confirm discovery still lists everything (runs in the app `.venv`):

```
python -c "from toolbox.discovery import discover; print(sorted(t.meta.id for t in discover().all()))"
```

Before a release-facing change, also run:

```
python -m benchmarks.check_docs
python benchmarks/run_all_smoke.py
```

## Style

Match the surrounding code: the same comment density, naming, and idioms. Every
line should earn its place. Keep the engine small and the panel thin.

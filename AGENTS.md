# AGENTS.md — KS ToolBox

Child of `../AGENTS.md` (workspace DOX rail) + `M:\KS Apps\CodingPrinciples.md`. Parent rules apply; this doc owns KS-ToolBox local specifics.

`KS_ENGINEERING_STANDARD.md` is the canonical workspace-wide cartridge-grade engineering, measurement, and merge standard. It applies to every KS ToolBox feature, refactor, optimization, and review.

## Purpose

Cross-platform (Windows/Linux/macOS) CustomTkinter desktop app — **one UI shell, many single-purpose tools** — built for **free public release**. Each tool is a self-contained, auto-discovered plugin folder. Tool #1: Video Compressor.

## Ownership

- **This doc** owns: the plugin architecture, the tool template, the release/verification bar, and the app-local env decision.
- **`toolbox/`** owns the shared framework — `tool.py` (contract), `application.py`/`job_queue.py` (shell-owned services and queue), `batch_core.py`/`sqlite_job_store.py` (durable execution and recovery), `batch_reporting.py` (typed completion/report finalization), `queue_panel.py`, `theme.py`, `components.py`, `icons.py`, `shell.py`, `discovery.py`, `engine_common.py`, and `batch_panel.py`. Tools depend only on its public names.
- **`tools/<name>/`** owns one tool end-to-end.

## Local Contracts

- **One tool = one folder** under `tools/`, exposing a module-level `TOOL` (`ToolMeta` + `build_panel(parent, services)`). Discovery finds it; the shell never changes. The explicit service context contains the shell-owned queue; do not replace it with a singleton or widget-tree lookup. (LEGO principle.)
- **Engine ≠ UI** (three-layer Domain/Presentation, per the Unity rules' spirit): pure logic in `engine.py` — headless, no CustomTkinter import, no global state, returns the error-envelope `{error, error_type, retryable, degraded, details, data}`. Thin `panel.py` — CustomTkinter only; work never runs on the UI thread. Durable tools submit to the shell queue, while unmigrated panels temporarily use the compatibility worker.
- Tools import **`toolbox`** (theme/components/contract) only — never another tool, never framework internals.
- **Batch / destructive actions MUST have Preview + Confirm + Logging** (Unity rules §9): a dry-run preview, an explicit confirm before deleting originals, and a per-run log/manifest.
- **No** `eval()`, **no** swallowed exceptions (`except: pass`), **no** silent fallback — a fallback must announce itself (status/flag). (CodingPrinciples #3, #12.)
- **Cross-platform:** guard OS-specific calls with `os.name`; resolve external binaries via bundled `bin/` first then PATH; never pop a console (subprocess uses `CREATE_NO_WINDOW` on Windows; ship windowed via PyInstaller `--windowed`).
- **Heavy/optional deps import lazily.** A tool's `tool.py` (which discovery imports to read `meta`) must NOT pull numpy/rembg/etc. at module load — import the panel/engine inside `build_panel`. This keeps discovery and the sidebar working on a machine that hasn't installed a tool's deps yet; the missing dep is announced when the tool is opened (`shell._safe_build` shows an in-panel "install its dependencies" card), never as a startup crash. `discovery.discover()` also isolates a single broken plugin (skips it with a stderr warning) so one bad tool can't take down the app.
- **Env deviation (intentional, overrides the shared-venv workspace rule):** KS-ToolBox uses a **dedicated minimal `.venv`** (customtkinter, send2trash), NOT the shared `D:\KSAppDev\.venv`. Rationale: this is a distributable public app whose PyInstaller bundle must contain only its own deps, not the whole workspace's torch/opencv/etc. stack. Deps pinned in `requirements.txt`.

## Work Guidance

Tool template — every tool folder:
```
tools/<name>/
  engine.py      pure logic (Domain+Application), error-envelope, headless
  panel.py       Presentation only (CustomTkinter, thin, worker-thread)
  __init__.py    TOOL = <Name>Tool()
  test_smoke.py  standalone-start + valid-output on a bundled sample
  README.md      what it does, deps, options
```
Add a tool = drop the folder; **no edits** to `toolbox/` or `main.py`.
- Engine files import shared helpers from `toolbox.engine_common` (`ok`/`err` envelope, binary resolution and subprocess runners, cancellable ffprobe duration, streaming/cancellable `sha256_file`, output-collision detection, `VIDEO_EXTS`/`IMAGE_EXTS` constants) — never duplicate these.
- Batch-tool panels extend `toolbox.batch_panel.BaseBatchPanel` (shared files card, run/stop/pause row, output-folder picker, results log, queue polling, output-collision reporting, terminal-item display, completion preparation, and terminal status/report UI). New durable tools receive `services.queue`, implement `_build_submission`, finalize through `self._prepare_queue_completion(report, opts, tool_id=...)`, and never start their own worker thread. The old `_work` hook remains only as a compatibility path while existing tools migrate individually. Use `self._build_output_row(b, hint)` + `self._build_run_row(b)` at the end of the options card; use `self._resolve_input_root()` for mirror mode.

Extracting from RupayanFlow/ChobiEngine: keep the engine logic, drop the `runtime.py` job wrapper, write a thin panel, and **fix anti-patterns on the way in** (this session's verification found: VideoRescaler's bytes/str ffmpeg-detect bug, `eval()` on ffprobe output, `random` instead of `secrets` for passwords).

## Verification

- **No tool ships unverified.** Each must pass `test_smoke.py`: boots with default config and produces a valid output on a real sample. This is the release bar — "can't ship non-working stuff." A smoke test that needs an uninstalled dep should **skip cleanly** (print SKIP, return 0), not fail — see `video_chopper` (skips without ffmpeg) and `alpha_doctor` (always tests the pure deterministic keying with numpy+Pillow; runs the optional u2net leg only when onnxruntime + a cached model are present). To verify a heavy-dep tool without touching the app's `.venv`, use an ephemeral uv overlay — include `customtkinter` (core framework; the package `__init__`→`tool.py`→`icons.py` chain imports it) plus the tool's own deps: `uv run --no-project --with customtkinter --with <pkg> python -m tools.<name>.test_smoke`. Run the whole suite with `benchmarks/run_all_smoke.py` (discovers all tools; skips are not failures).
- **17 tools built + smoke-tested (each has `test_smoke.py`; `tools/video_chopper/` is the reference):**
  - *Video/Audio:* Video Compressor (VMAF-gated, delete-confirm), Video Chopper (ffmpeg blackdetect), Audio Tool (ffmpeg convert/trim/fade/normalize).
  - *Images:* Image Rescale, Format Converter (durable staged images/A-V/docs dispatch with typed artifact validation), Pixel Art Converter, To SVG (durable validated vtracer with collision/atomic-output guards), Icon Normalizer, Showcase (contact-sheet/hero/before-after).
  - *Game/asset:* Alpha Doctor (durable validated deterministic keying + explicit-consent/checksummed optional u2net), Material Converter (ORM/MOS, DX↔GL, presets), Sprite Viewer (custom viewer panel), Tileset Checker (seam score/preview), Texture Renderer (custom tabbed durable queue + bounded/cancellable validated Substance/Material Maker publishing), Package Extractor (durable unitypackage/zip/tar, cancellation rollback, zip-slip/symlink/bomb-safe).
  - *Library/dataset:* Asset Auditor (durable grouped dup/corrupt audit → exact HTML/JSON/CSV report set), Dataset Manager (durable grouped pair/replace/split/bucket, copy-only, JSON-last provenance, exact reuse/repair).
- **Direction:** deterministic-first / AI-optional public line; free tools funnel to the paid KS Production Engine. See the memory `kstoolbox-public-direction` and `docs/EXTRACTION_MAP.md`. **Fix-first if revisited:** Video Rescale (bytes/str ffmpeg bug + `eval()`).

## Child DOX Index

(none yet — tools share the uniform template above. Add a child AGENTS.md only if a specific tool grows its own durable contract.)

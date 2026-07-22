# Texture Renderer

Batch-export textures from **Substance 3D Designer** (`.sbsar`) and **Material
Maker** (`.ptex`) projects by driving their external command-line tools. A KS
ToolBox plugin.

Ported from the standalone *Universal Texture Batch Renderer*
(`M:\KS Apps\UniversalBatchRenderer`) — behavior preserved, engine made pure and
cross-platform, anti-patterns fixed (see below).

## What it does

Two sub-modes, one per engine, in a tabbed panel over a shared console:

- **Substance (`.sbsar`)** — scans an input directory for `.sbsar` archives and
  renders each via `sbsrender` at the chosen resolution.
- **Material Maker (`.ptex`)** — scans for `.ptex` projects and exports each via
  `material_maker` for a target engine (Unreal / Godot / Unity / Blender), then
  cleans the non-PNG sidecars the exporter drops and, optionally, resizes the
  exported PNGs to a target size.

Each mode runs on a worker thread (the UI never blocks), streams per-project
progress to the console, and can be stopped between projects.

## The two engines are USER-PROVIDED

`sbsrender` and `material_maker` are **not bundled** — they ship with the user's
own Substance Designer / Material Maker install. Point the tool at the
executable in each tab. Any name form is accepted cross-platform: `sbsrender`,
`sbsrender.exe`, `sbsrender.sh` (matched on the file stem, not a hardcoded
`.exe`).

- Substance 3D Designer CLI: https://substance3d.adobe.com/
- Material Maker: https://github.com/RodZill4/material-maker

## Dependencies

- **Pillow** — only for Material Maker's optional PNG resize (imported lazily;
  the tool loads without it, and resizing announces if it's missing).
- **customtkinter** — the ToolBox UI framework.
- The two **external engines** above (user-provided, not Python packages).

## Options

| Option | Mode | Meaning |
|---|---|---|
| Resolution | Substance | Render size (mapped to Substance's log2 `$outputsize`). |
| Target | Material Maker | Export target engine (Unreal/Godot/Unity/Blender). |
| Resize | Material Maker | Resample exported PNGs to N² (`Original` = keep). |
| Group into subfolders | both | Each project's maps go into its own named subfolder. |
| Recursive | both | Scan the input directory recursively. |
| Dry run | both | Preview the exact command per project; write nothing. |

**Destructive-action safety (Preview + Confirm + Logging):** a real Material
Maker export **deletes** every non-PNG file in the output folder during cleanup,
so a confirmation dialog gates any non-dry-run MM export. Dry run previews the
commands without touching disk.

## Verify

```powershell
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; H:\Apps\scoop\shims\uv.exe run --no-project --python 3.12 --with customtkinter --with pillow python -m tools.texture_renderer.test_smoke
```

The smoke test covers the pure logic and post-processing (discovery, resolution
mapping, argv building, engine validation, cleanup+resize, dry-run). The
external-engine invocation itself is a thin `run_cmd` wrapper and is **not**
exercised on CI (neither engine is installed there); the smoke test skips
cleanly if Pillow is absent.

## Anti-patterns fixed from the source

- `except: pass` swallowing every cleanup/resize/settings error → failures are
  now **counted and reported** (`cleanup_and_resize` returns `{cleaned, resized,
  errors}`).
- Hardcoded Windows-only `sbsrender.exe` / `material_maker.exe` name check →
  cross-platform **stem** match, any extension.
- Windows-only `subprocess.CREATE_NO_WINDOW` flag hardcoded → uses shared
  `engine_common.run_cmd` (sets the flag only on Windows).
- `*.exe`-only file-picker filter → no forced filter (Linux/macOS engines have
  no extension).
- Render logic tangled into the `CTk` window → pure, headless `engine.py`;
  thin, worker-threaded `panel.py`.

## Credit

Source app: *Universal Texture Batch Renderer* by KS.

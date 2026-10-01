# Unity Packager

Build a `.unitypackage` straight from a Unity project folder — **Unity does not need to be open**. No AI, no network, no subprocess — **Python standard library only**.

## How to use

1. **Unity project** — browse to the folder that contains `Assets/`.
2. **Folders to include** — tick the product folders to ship (use *Add another folder…* for anything else).
3. **Package** — choose where to save, then **Preview** (dry run) or **Build package**. Build always previews first and asks before writing.

## What goes in the package

A `.unitypackage` is a gzipped tar of `<guid>/pathname`, `<guid>/asset.meta` and, for files, `<guid>/asset`.

- Folders and files inside the ticked paths, **plus their ancestor folders** under `Assets/` (so Unity recreates the same folder GUIDs).
- Left out, exactly as Unity does: hidden items (`.x`), items ending in `~`, and `*.tmp`.
- A file with **no `.meta`** is skipped and listed in the report — Unity would give it a new GUID and break references.

## Safety and guarantees

- Inputs must resolve **inside `Assets/`** (not `Assets` itself); `..` traversal is refused.
- The output file cannot sit inside an included folder.
- **Duplicate GUIDs** are refused with both paths named, never silently dropped.
- Output is staged to `<name>.part` and replaced **atomically**; **Cancel** leaves no package and no `.part`.
- **Collision policy:** create a copy (`name (2).unitypackage`), overwrite, or stop.
- **Deterministic:** the same input and compression level produce byte-identical packages (fixed timestamps, sorted entries); the report shows the SHA-256.
- Files stream into the archive, so memory stays bounded for large packs.

## Layout

| File | Role |
|---|---|
| `engine.py` | validate → plan → build; headless, returns the shared error envelope |
| `panel.py` | thin CustomTkinter UI; build runs on a cancellable worker thread |
| `test_smoke.py` | fake project: layout, determinism, collisions, unsafe input, cancellation |

Run the test: `uv run --no-project --with customtkinter python -m tools.unity_packager.test_smoke`

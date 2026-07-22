# Package Extractor

Deterministic batch extraction of Unity `.unitypackage` files and `zip` / `tar`
archives, with original-folder reconstruction and SAFE handling of untrusted
input. No AI, no network, no subprocess — **Python standard library only**.

## Formats

| Format | How |
|---|---|
| `.unitypackage` | It's a **gzipped tar**. Each asset is a `<GUID>/` entry holding `asset` (the file bytes), `asset.meta`, and `pathname` (a text file with the original Unity project path, e.g. `Assets/Art/hero.png`). Extraction reads each GUID's `pathname` and writes its `asset` bytes to that reconstructed relative path — rebuilding the original `Assets/…` tree. |
| `.zip` | `zipfile` |
| `.tar`, `.tar.gz` / `.tgz`, `.tar.bz2`, `.tar.xz` | `tarfile` (transparent compression) |

Each archive extracts into its **own `<stem>/` subfolder** under the output
folder, so a batch never intermixes.

## Security guarantees

This tool treats every archive as untrusted input.

- **Zip-slip / tar-slip.** Every entry name — and every reconstructed Unity
  `pathname` — is sanitised (`safe_name` / `_safe_relpath`) and then asserted to
  resolve **within** the output root (`is_within`, via `realpath`). Any entry that
  tries to escape (`../…`, absolute, drive/UNC) is **rejected and recorded** in the
  report — announced, never silently dropped or written outside.
- **Symlinks / hardlinks.** Symlink and hardlink tar members are **skipped**
  (never followed, never created).
- **Decompression bombs.** A per-file cap and a per-archive total cap are enforced
  on the **actual bytes written** (a lying header can't beat the running total).
  Exceeding either **stops that archive** with a clear error; the partial report is
  preserved.
- **No execution.** Archive contents are only ever read and written as bytes.

## Options

- **Output folder** (required) — root for every archive's `<stem>/` subfolder.
- **Extension filter** — only extract entries whose extension is in the list
  (blank = all), e.g. `.png, .fbx`.
- **On name clash** — `rename` (write `name_001.ext`) or `skip`. Never overwrites
  blindly.
- **Nested archives** — optionally unpack archives found inside an archive,
  bounded to a depth of 2.
- **Preview only** — list contents (names, sizes, reconstructed paths) and write
  nothing.

## Reports

Non-preview runs write, per archive, `_extract_report.json` + `_extract_report.csv`
(files written with sha256, skipped, rejected, errors) into that archive's folder,
plus a top-level `extract_manifest.csv` summarising the whole run.

## Verify

```powershell
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; H:\Apps\scoop\shims\uv.exe run --no-project --python 3.12 --with customtkinter python -m tools.package_extractor.test_smoke
```

The smoke test builds a real zip with a `../evil.txt` traversal entry (asserts it
is rejected, not written outside) and a minimal fake `.unitypackage` (asserts
`Assets/foo/bar.txt` is reconstructed with the exact bytes).

## Credits

Unity `.unitypackage` parsing distilled from KS UnityExtractor; safe-path /
collision helpers modelled on RupayanFlow's `pack_core`. Reimplemented cleanly on
the Python standard library for KS-ToolBox.

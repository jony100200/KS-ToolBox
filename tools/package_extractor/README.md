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
folder. A real batch is rejected before it starts if selected same-stem
archives would share that folder, so their contents cannot silently intermix.

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
  against declared sizes during preview and on the **actual bytes written**
  during extraction (a lying header cannot beat the running total). Exceeding
  either stops that archive with a clear error; the partial report is preserved.
- **Metadata floods.** Each archive has a 100,000-member default cap and a hard
  configuration ceiling, bounding generated report and per-entry bookkeeping.
- **No execution.** Archive contents are only ever read and written as bytes.
- **Cancellation rollback.** Cancellation removes staged data and only the
  members recorded as created by that interrupted attempt. Pre-existing
  collision targets are never part of rollback.

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

The per-archive reports are staged, flushed, and atomically published with JSON
as the completion marker. Durable queue reuse verifies both report files and
every recorded top-level or nested artifact by path, size, and SHA-256. Missing,
changed, or corrupt outputs are never accepted as a completed extraction.

## Unattended queue behavior

- One archive is one durable failure and recovery boundary.
- Pause takes effect between archives; cancellation is also checked while
  streaming members and recursing into nested archives.
- Unsafe entries and recoverable per-member errors complete with visible
  warnings instead of being labelled clean.
- Interrupted attempts roll back their own committed members; malformed or
  bomb-guarded archives are isolated without stopping the rest of the queue.
- Top-level manifest and morning-report failures are visible finalization
  warnings rather than silently disappearing.

## Verify

```powershell
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; H:\Apps\scoop\shims\uv.exe run --no-project --python 3.12 --with customtkinter python -m tools.package_extractor.test_smoke
```

The smoke test builds a real zip with a `../evil.txt` traversal entry, a minimal
fake `.unitypackage`, and a nested archive. It verifies safe reconstruction,
atomic reports, exact corruption detection, recursive validation, preview bomb
and entry-count budgets, same-destination detection, invalid-option rejection, and cancellation
rollback without touching a pre-existing collision target.

## Credits

Unity `.unitypackage` parsing distilled from KS UnityExtractor; safe-path /
collision helpers modelled on RupayanFlow's `pack_core`. Reimplemented cleanly on
the Python standard library for KS-ToolBox.

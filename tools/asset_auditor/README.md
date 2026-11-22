# Asset Auditor

A deterministic, read-only batch inspector for a folder of images/assets. Point
it at a folder, it scans recursively and writes a report describing everything
worth knowing before you ship, pack, or train on those assets — **no AI, no
network, no GPU**. numpy + Pillow + stdlib only.

It **never modifies or deletes a source file.** The tool's output is three
report files written to your chosen folder.

## What it finds

| Check | Meaning |
|---|---|
| Exact duplicates | byte-identical files, grouped by sha256 |
| Near duplicates | perceptually similar — dHash Hamming distance ≤ threshold (union-find grouping) |
| Corrupt / unreadable | Pillow `verify()`/decode failure **plus** a magic-byte header check (PNG/JPEG/GIF/WEBP/BMP/TIFF) |
| Unsafe filenames | Windows reserved names (`CON`, `NUL`, `COM1`…), illegal chars `<>:"/\|?*`, control chars, trailing dot/space |
| Empty files | 0 bytes |
| Oversized files | larger than a configurable MB threshold |
| Empty folders | directories with no entries |
| Tiny images | a side below a minimum dimension |
| Resolution histogram | counts per `W×H` |
| Image health | very-dark / very-bright / low-contrast flags (mean & std of luminance) |

Near-duplicate grouping uses an exact BK-tree Hamming-distance index followed
by union-find. It produces the same transitive groups as comparing every pair,
while avoiding most unrelated comparisons on normal sparse dHash collections.
Its worst case remains quadratic for adversarial dense hashes; a threshold that
covers the whole hash width is handled directly in linear time.

## Output

Written atomically (`.part` → replace) to the report folder:

- **`audit.html`** — a self-contained page (inline CSS, inline base64 thumbnails
  for duplicate groups); opens offline, anywhere.
- **`audit.json`** — the full machine-readable report, including a per-file record.
- **`audit_issues.csv`** — one flat, greppable row per issue (`issue_type, path, detail`).

The three reports are one durable artifact set. HTML and CSV are staged first;
`audit.json` records their sizes and SHA-256 values and publishes last as the
completion marker. Queue recovery reuses a prior audit only while all three
files still match that provenance. A corrupt or incomplete set is rebuilt.

One selected collection is one grouped queue item, while every selected source
participates in job identity. Active hashing, decoding boundaries, grouping,
folder scans, thumbnail rendering, report writes, and report validation observe
cancellation. Retriable I/O failures are isolated and the morning completion
report remains available through the shared queue history.

## Options

| Option | Meaning | Default |
|---|---|---|
| Near-dup Hamming | max dHash bit-distance (of 64) to call two images near-duplicates | `8` |
| Oversized over (MB) | flag files larger than this | `25` |
| Min dimension (px) | images with a side below this are "tiny" | `32` |
| Image health flags | compute dark/bright/low-contrast flags | on |
| Report folder | where the reports go (blank = `./asset_audit` beside the scanned folder) | — |

The Hamming threshold is validated against the dHash width (0–64 with the
current 8×8 hash), the oversized threshold must be finite and positive, and the
minimum dimension must be 1–1,000,000 pixels. Invalid settings stop before any
source is scanned or report is written. A single grouped audit is capped at
100,000 files so detailed records, thumbnails, and checkpoint metadata have a
defined ceiling; larger collections should be split into production units.

## Dependencies

- **Pillow** and **numpy** (`pip install pillow numpy`).

## Verify

```
python -m tools.asset_auditor.test_smoke
```

## HDD-Gentle Exact Duplicate CLI

For large drives across all file types (videos, archives, models, images):

```
python -m tools.asset_auditor.cli dupes -i <root> -o <report_dir>
```

Uses a single-threaded `os.scandir` metadata walk, discards unique sizes without
opening files, skips hardlink aliases (`st_dev`, `st_ino`), runs a 64 KB head/mid/tail
partial fingerprint on size collisions, and only streams full SHA-256 on remaining matches.

To re-verify a `dupes.json` report and safely move duplicate copies to the Recycle Bin
(always keeping 1 canonical copy):

```
python -m tools.asset_auditor.cli dupes-clean -r <report_dir>/dupes.json --execute
```

To build a fast offline search page and Markdown folder map (`00_FIND_EVERYTHING.html` + `00_DRIVE_MAP.md`) at a drive or folder root with bounded sequential depth:

```
python -m tools.asset_auditor.cli index -i <root> -o <out_dir> --max-depth 3
```

Checks the filename-safety rules (no deps), then — with numpy + Pillow — the
dHash/Hamming behaviour, exact indexed/all-pairs grouping parity across five
thresholds, the corrupt/dimension check, and a full audit over a temp folder
built to contain every issue class. It then validates exact three-report reuse,
same-size corruption rejection, strict options, announced thumbnail fallback,
and cancellation cleanup. Skips cleanly if numpy/Pillow are absent.


# Dataset Manager

Deterministic file management for image + caption datasets — the boring, safe
plumbing that AI dataset prep needs, **without shipping any AI model, captioner,
or network call.** It pairs, audits, buckets, splits, and batch-edits captions,
and it does all of it **copy-only**: your source folder is only ever *read*.

> Helps you prepare training datasets (LoRA/fine-tune style image+caption sets)
> without any AI on board — no model weights, no VLM, no internet. Just correct,
> reproducible file handling.

## Operations

Point it at a folder of images. Sidecar captions are matched by **stem** —
`img_007.png` ↔ `img_007.txt` (or `.caption`).

1. **Pair report** — lists images *with* captions, images *missing* captions, and
   *orphan* captions (a `.txt` with no image). Written as `pair_report.csv` +
   `pair_report.json`.
2. **Caption find & replace** — literal or regex find/replace across caption text,
   written to **copies** in the output folder (originals are never edited). Reports
   how many replacements were made.
3. **Resolution bucketing** — copies each image (and its caption) into a subfolder
   named by size (`768x512`) or by aspect ratio (`portrait` / `landscape` /
   `square`).
4. **Train / val / test split** — copies each image+caption pair into `train/`,
   `val/`, `test/` by ratio (e.g. `0.8 / 0.1 / 0.1`). **Deterministic:** files are
   sorted by name, then assigned by a fixed index threshold — **no randomness**, so
   the same folder always splits the same way. An image and its caption always land
   in the same split.
5. **Dataset manifest** — every run writes `dataset_manifest.csv` +
   `.json`: `filename, has_caption, width, height, split, bucket, sha256`.

## Why it's safe

- **Non-destructive.** Nothing is moved or deleted; sources are never modified.
  All output goes under one output root via `shutil.copy2` / freshly written text.
- **Preview-first.** *Preview only* (the default) lists exactly what *would* be
  copied or changed, writing nothing.
- **Deterministic.** Pairing, bucketing, and the split are pure functions of the
  sorted inputs — reproducible, no RNG.
- **Atomic writes.** Every file is written to a `.part` temp then swapped into
  place, so a crash can't leave a half-written caption or manifest.

## Options

| Option | Applies to | Notes |
|---|---|---|
| Caption extensions | all | Comma-separated, e.g. `.txt, .caption`. |
| Find / Replace / Regex | replace | Regex uses Python `re`; an invalid pattern fails loudly before any write. |
| Bucket by | bucket | `dimensions` (WxH folders) or `aspect` (portrait/landscape/square). |
| Split ratios | split | `train / val / test`; normalised, so they need not sum to 1. |
| Output folder | all | Results are copied here. Required unless *Preview only*. |
| Preview only | all | Default on — lists actions, writes nothing. |

## Dependencies

- **stdlib only** for pairing, splitting, bucketing, replace, sha256, manifests.
- **Pillow** — used lazily, *only* to read `width`/`height`. Without it every
  operation still works; the manifest's dimension columns come back blank and the
  run is flagged *degraded* (announced, never silent).

## Verify

```
$env:PYTHONPATH="D:\KSAppDev\KS-ToolBox"; H:\Apps\scoop\shims\uv.exe run --no-project --python 3.12 --with customtkinter --with pillow python -m tools.dataset_manager.test_smoke
```

The smoke test proves pairing/bucketing/split/replace math, then runs a real
temp dataset through pair_report + split + bucket and **asserts the sources are
byte-for-byte unchanged** afterward.

## Credits

Part of **KS ToolBox**. Deterministic primitives (folder scan, natural sort,
sha256, the `train/val/test` split idea) distilled from KS-RupayanFlow's batch
manifest / dataset-library modules; no AI/VLM code was carried over.

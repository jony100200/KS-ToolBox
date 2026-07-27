# Metadata Scrubber

Batch-copies images into a delivery folder with every trace of provenance removed.

## Why

Generated images are self-documenting, which is excellent internally and bad on publish:

- **ComfyUI** writes its entire API graph into the PNG `prompt` and `workflow` chunks — every
  node, model filename, LoRA, and prompt.
- **stable-diffusion.cpp** writes prompt, seed, sampler, steps, LoRA and model names into
  `parameters`.
- **Cameras and phones** add EXIF, which can include GPS coordinates.

All of it travels with the file. Anyone who receives an image can read it in seconds.

## What it does

Copies each selected image to the output folder, stripping:

`prompt`, `workflow`, `parameters`, EXIF, XMP, IPTC, and the usual text chunks
(`Comment`, `Software`, `Description`, `Author`, `Copyright`, `Title`).

## Guarantees

- **Copy-only.** Sources are opened read-only and never written to. If the output path would
  land on the source, the item fails rather than overwriting it. Your masters keep their
  recipes; only the copies are stripped.
- **Verified, not assumed.** Every written file is re-opened and re-inspected. If anything
  survived, the item is reported as failed — it is never silently shipped.
- **Pixels preserved.** PNG/WebP/TIFF re-encode losslessly. JPEG reuses the original
  quantization tables and subsampling (`quality="keep"`), so there is no second generation
  of loss.
- **Colour preserved.** The ICC profile describes the pixels, not their origin, so it is kept
  by default. Uncheck *Keep colour profile* to drop it too.

## Options

| option | default | meaning |
| --- | --- | --- |
| Preview only | **on** | report what would be stripped, write nothing |
| Mirror input structure | on | recreate the source folder tree under the output root |
| Keep colour profile (ICC) | on | keep colour fidelity; not provenance |
| Copy non-image files through | on | keeps the delivered set complete |
| Output folder | blank | blank = `./clean` beside each source |

Preview is on by default deliberately: run it once to see what is actually in your files
before writing anything.

## Output

A `scrub_manifest.csv` in the output root — one row per file with the source, action, the
keys that were removed, and the destination. That is the audit trail for what left the
building.

## Deps

Pillow. Imported lazily, so discovery and the sidebar work without it.

## Verify

```
python -m tools.metadata_scrubber.test_smoke
```

Covers detection, delivered-copy cleanliness, master-untouched, pixel identity, the
in-place-write refusal, dry-run, and durable batch recovery.

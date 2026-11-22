# Image Compressor

Size-gated, quality-verified batch image compression. Every compressed image is
re-opened and verified before it replaces or commits, and originals are removed
only after proof verification.

## Modes

- **Lossless WebP** (`lossless_webp`, default): converts PNG/BMP/TIFF/WebP to
  lossless WebP and verifies 100% pixel-identical output (`lossless_exact=True`).
- **Lossless Same Format** (`lossless_same`): re-optimizes PNG (`compress_level=9`)
  and TIFF, converts uncompressed BMP to PNG, and skips already-lossy JPEG.
- **Visually Lossless** (`visually_lossless`): encodes WebP/JPEG at configurable
  quality and gates acceptance on PSNR (`>= 42 dB` default).

In all modes, outputs that are not at least `min_saving` (5% default) smaller than
the source are discarded and the original is kept.

## CLI

```
python -m tools.image_compressor.cli compress -i <dir> --in-place --work-dir <ssd_temp> --execute --delete-original
python -m tools.image_compressor.cli cleanup -o <out_dir> --execute
```

## Verify

```
python -m tools.image_compressor.test_smoke
```

"""Icon/Sprite Normalizer engine — make a batch of icons one uniform square size.

Pure logic, no UI, no global state. Pillow only (imported lazily inside the
functions so discovery/the sidebar work before Pillow is installed). Each image
is trimmed to its opaque bounds, scaled to fit — aspect preserved — into a
`size` x `size` transparent canvas, and centred. Output is always RGBA PNG.

Distilled from RupayanFlow's `asset2d.icons.core.fit_icon`, dropping the numpy
dependency (Pillow's own alpha `getbbox` + `resize` do the same work), the job
wrapper, and the unrelated background-removal helpers. The absolute pixel margin
is generalised to a `padding_pct` so one setting works at any target size, and
the deprecated `Image.LANCZOS` alias is replaced with `Image.Resampling.LANCZOS`.

Public interface:
    normalize(img, ...)  -> PIL.Image          (pure transform, RGBA in/out)
    process(path, opts)  -> Result             (load -> normalize -> save)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS

_MAX_PADDING_PCT = 90.0     # never shrink the content area below 10% of the canvas


def normalize(img, *, size: int = 256, padding_pct: float = 0.0, trim: bool = True):
    """Centre a subject on a square `size` x `size` transparent canvas. RGBA out.

    trim        : crop to the opaque (alpha > 0) bounding box first.
    padding_pct : empty margin as a % of the canvas — the content fits inside a
                  `size * (1 - padding_pct/100)` box, centred (0 = fill the edge).

    A fully-transparent input (when trimming) yields an empty canvas — the caller
    decides whether that is worth writing.
    """
    from PIL import Image

    src = img.convert("RGBA")
    size = max(1, int(size))
    frac = max(0.0, min(float(padding_pct), _MAX_PADDING_PCT)) / 100.0
    target = max(1, int(round(size * (1.0 - frac))))

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    subject = src
    if trim:
        box = src.getchannel("A").getbbox()      # bounds of alpha > 0, or None if empty
        if box is None:
            return canvas
        subject = src.crop(box)

    w, h = subject.size
    if w == 0 or h == 0:
        return canvas
    scale = min(target / w, target / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    scaled = subject.resize((new_w, new_h), Image.Resampling.LANCZOS)

    px = (size - new_w) // 2
    py = (size - new_h) // 2
    canvas.alpha_composite(scaled, (px, py))
    return canvas


@dataclass
class NormalizeOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    size: int = 256
    padding_pct: float = 0.0
    trim: bool = True
    dry_run: bool = True


def plan_output(src: Path, opts: NormalizeOptions) -> Path:
    """Where this icon's normalized PNG goes. Mirror preserves the input subtree
    under out_root; else flat under out_root; else a `normalized` folder beside
    src. Always a `.png` (output is RGBA)."""
    src = Path(src)
    name = src.stem + "_icon.png"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "normalized" / name


@dataclass
class Result:
    src: str
    action: str                       # converted | skipped | failed | dry-run
    reason: str
    before: str = ""                  # "WxH"
    after: str = ""                   # "WxH"
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: NormalizeOptions) -> Result:
    """load -> normalize -> save, for one image."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    try:
        from PIL import Image
    except ImportError:
        return Result(str(src), "failed", "Pillow not installed — pip install pillow", detail="dep.missing")

    dst = plan_output(src, opts)
    after = f"{opts.size}x{opts.size}"
    # A `with` block releases the source handle on every path (skip, dry-run, and
    # the write path alike) — matters for 100s-of-files batches on Windows, where
    # a held handle blocks the file.
    try:
        with Image.open(src) as im:
            rgba = im.convert("RGBA")
            before = f"{rgba.width}x{rgba.height}"

            # Fully-transparent + trimming = nothing to centre. Announce the skip
            # rather than silently writing an empty canvas (no swallowed no-op).
            if opts.trim and rgba.getchannel("A").getbbox() is None:
                return Result(str(src), "skipped", "fully transparent — nothing to normalize",
                              before=before)
            if opts.dry_run:
                return Result(str(src), "dry-run", f"would normalize {before} -> {after}",
                              before=before, after=after, out_path=str(dst))

            out = normalize(rgba, size=opts.size, padding_pct=opts.padding_pct, trim=opts.trim)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")  # atomic write
            out.save(tmp, "PNG")
            tmp.replace(dst)
    except Exception as ex:                       # PIL raises many types on bad images / saves
        return Result(str(src), "failed", f"could not normalize {src.name}: {ex}", detail="normalize.failed")

    return Result(str(src), "converted", f"{before} -> {after}", before=before, after=after,
                  out_path=str(dst), detail=f"trim={opts.trim} pad={opts.padding_pct}%")


def validate_result(result: Result) -> bool:
    """Reopen a stored artifact before the durable runner trusts it."""
    if result.action in {"skipped", "dry-run"}:
        return True
    if result.action != "converted" or not result.out_path:
        return False
    try:
        from PIL import Image

        with Image.open(result.out_path) as image:
            image.load()
            width, height = image.size
            return (
                image.format == "PNG"
                and image.mode == "RGBA"
                and width > 0
                and width == height
                and result.after == f"{width}x{height}"
            )
    except (ImportError, OSError, ValueError):
        return False

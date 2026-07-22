"""Pixel Art Converter engine — turn an image into clean pixel art.

Pure logic, no UI, no global state. Two quantizations: spatial (downscale with
NEAREST so blocks are crisp) and colour (median-cut palette, optional dither),
with hard-binary alpha so edges stay sharp. Optionally upscales back to the
original size so the pixels are big and visible. Cross-platform; Pillow only.

Lifted from ChobiEngine's true_pixel_converter, with its per-image
`except: log-and-continue` replaced by the standard error envelope so failures
are reported, never silently dropped.

Public interface:
    pixelize(img, ...)   -> PIL.Image          (pure transform, RGBA in/out)
    process(path, opts)  -> Result             (load -> pixelize -> save)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS


def pixelize(img, *, pixel_size: int = 4, num_colors: int = 16,
             dither: bool = True, upscale: bool = True):
    """Pixel-art transform of a PIL image. RGBA out (alpha preserved, hard-keyed).

    pixel_size : block size — bigger = chunkier pixels.
    num_colors : palette size (median-cut).
    dither     : Floyd-Steinberg when reducing colours.
    upscale    : scale the tiny result back up to the original size (visible pixels).
    """
    from PIL import Image

    src_rgba = img.convert("RGBA")
    orig_w, orig_h = src_rgba.size
    r, g, b, a = src_rgba.split()
    rgb = Image.merge("RGB", (r, g, b))

    new_w = max(1, orig_w // max(1, pixel_size))
    new_h = max(1, orig_h // max(1, pixel_size))

    tiny_rgb = rgb.resize((new_w, new_h), Image.Resampling.NEAREST)
    tiny_alpha = a.resize((new_w, new_h), Image.Resampling.NEAREST).point(lambda p: 255 if p > 128 else 0)

    dmethod = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    quant = tiny_rgb.quantize(colors=max(2, num_colors), method=Image.Quantize.MEDIANCUT, dither=dmethod)
    tiny = Image.merge("RGBA", (*quant.convert("RGB").split(), tiny_alpha))

    if upscale:
        return tiny.resize((orig_w, orig_h), Image.Resampling.NEAREST)
    return tiny


@dataclass
class PixelOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    pixel_size: int = 4
    num_colors: int = 16
    dither: bool = True
    upscale: bool = True
    dry_run: bool = True


def plan_output(src: Path, opts: PixelOptions) -> Path:
    """Where this image's pixel-art PNG goes. Mirror preserves the input subtree
    under out_root; else flat under out_root; else a `pixel_art` folder beside src."""
    src = Path(src)
    name = src.stem + "_pixel.png"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "pixel_art" / name


@dataclass
class Result:
    src: str
    action: str                       # converted | failed | dry-run
    reason: str
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: PixelOptions) -> Result:
    """load -> pixelize -> save, for one image."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    dst = plan_output(src, opts)

    if opts.dry_run:
        return Result(str(src), "dry-run", "would convert to pixel art", out_path=str(dst))

    try:
        from PIL import Image
        with Image.open(src) as im:
            out = pixelize(im, pixel_size=opts.pixel_size, num_colors=opts.num_colors,
                           dither=opts.dither, upscale=opts.upscale)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")  # atomic write
        out.save(tmp, "PNG")
        tmp.replace(dst)
    except ImportError:
        return _fail_result(src, "Pillow not installed — pip install pillow", "dep.missing")
    except Exception as ex:                       # PIL raises many types on bad images
        return _fail_result(src, f"could not convert {src.name}: {ex}", "convert.failed")

    return Result(str(src), "converted", f"{opts.num_colors} colors, {opts.pixel_size}px blocks",
                  out_path=str(dst), detail="upscaled" if opts.upscale else "native size")


def _fail_result(src: Path, details: str, etype: str) -> Result:
    return Result(str(src), "failed", details, detail=etype)

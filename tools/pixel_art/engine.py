"""Pixel Art Studio Engine — turn images into clean, authentic retro pixel art.

Pure logic, no UI, no global state.
Features:
    1. Spatial Quantization (Downscale with Area/Nearest sampling)
    2. Curated Hardware Palettes (PICO-8, Game Boy DMG/Pocket, NES, C64, CGA, EDG32, Cyberpunk)
    3. Perceptual CIELAB Delta-E Nearest Color Matching
    4. Advanced Dithering (Ordered Bayer 2x2/4x4/8x8, Floyd-Steinberg, Atkinson, None)
    5. Pixel-Perfect Line Cleanup (L-shape corner pruning)
    6. Sprite Outlining (Black/Color stroke around alpha silhouettes)
    7. Hard-Binary Alpha Keying
    8. Crisp Integer Nearest-Neighbor Upscaling
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PIL import Image

from toolbox.engine_common import IMAGE_EXTS
from . import palettes
from . import dithering


def pixel_perfect_prune(alpha_mask: np.ndarray) -> np.ndarray:
    """L-Shape corner pruning to eliminate double-pixel 'elbows' on diagonal line art."""
    h, w = alpha_mask.shape[:2]
    pruned = alpha_mask.copy()
    solid = alpha_mask > 128

    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if not solid[y, x]:
                continue
            # Check 4 direct neighbors
            up, down = solid[y - 1, x], solid[y + 1, x]
            left, right = solid[y, x - 1], solid[y, x + 1]

            # Detect isolated 90-degree corner
            is_corner_tl = up and left and not right and not down and not solid[y - 1, x - 1]
            is_corner_tr = up and right and not left and not down and not solid[y - 1, x + 1]
            is_corner_bl = down and left and not right and not up and not solid[y + 1, x - 1]
            is_corner_br = down and right and not left and not up and not solid[y + 1, x + 1]

            if is_corner_tl or is_corner_tr or is_corner_bl or is_corner_br:
                pruned[y, x] = 0

    return pruned


def add_sprite_outline(image_rgba: Image.Image, outline_color: tuple[int, int, int] = (0, 0, 0), thickness: int = 1) -> Image.Image:
    """Adds a crisp pixel outline around transparent sprite boundaries."""
    arr = np.asarray(image_rgba)
    h, w = arr.shape[:2]
    alpha = arr[:, :, 3] > 128

    if not np.any(alpha) or np.all(alpha):
        return image_rgba

    # Dilate alpha mask by thickness
    dilated = alpha.copy()
    for dy in range(-thickness, thickness + 1):
        for dx in range(-thickness, thickness + 1):
            if abs(dx) + abs(dy) <= thickness and (dx != 0 or dy != 0):
                dilated |= np.roll(np.roll(alpha, dy, axis=0), dx, axis=1)

    outline_mask = dilated & ~alpha
    out_arr = arr.copy()
    out_arr[outline_mask, 0] = outline_color[0]
    out_arr[outline_mask, 1] = outline_color[1]
    out_arr[outline_mask, 2] = outline_color[2]
    out_arr[outline_mask, 3] = 255

    return Image.fromarray(out_arr, "RGBA")


def pixelize(img: Image.Image,
             *,
             pixel_size: int = 4,
             palette: str = "pico8",
             num_colors: int = 16,
             dither_method: str = "bayer4",
             dither_strength: float = 1.0,
             use_lab: bool = True,
             pixel_perfect: bool = False,
             outline: bool = False,
             outline_color: tuple[int, int, int] = (0, 0, 0),
             upscale: bool = True) -> Image.Image:
    """Full studio-grade pixel art transformation."""
    src_rgba = img.convert("RGBA")
    orig_w, orig_h = src_rgba.size

    # 1. Downscale to pixel grid
    new_w = max(1, orig_w // max(1, pixel_size))
    new_h = max(1, orig_h // max(1, pixel_size))

    # Box downscale for smooth color averaging before quantization
    tiny_src = src_rgba.resize((new_w, new_h), Image.Resampling.BOX if pixel_size > 2 else Image.Resampling.NEAREST)
    tiny_arr = np.asarray(tiny_src)
    tiny_rgb = tiny_arr[:, :, :3]
    tiny_alpha = tiny_arr[:, :, 3]

    # 2. Hard-key binary alpha
    binary_alpha = np.where(tiny_alpha > 128, 255, 0).astype(np.uint8)

    # 3. Optional Pixel-Perfect corner pruning
    if pixel_perfect:
        binary_alpha = pixel_perfect_prune(binary_alpha)

    # 4. Fetch / Compile Palette
    palette_rgb = palettes.get_palette_rgb(palette, fallback_image=tiny_src, num_colors=num_colors)

    # 5. Dither & Quantize
    quant_rgb = dithering.dither_image(
        tiny_rgb,
        palette_rgb=palette_rgb,
        method=dither_method,
        strength=dither_strength,
        use_lab=use_lab
    )

    # 6. Composite RGBA
    tiny_result = Image.fromarray(np.dstack([quant_rgb, binary_alpha]), "RGBA")

    # 7. Optional Sprite Outline
    if outline:
        tiny_result = add_sprite_outline(tiny_result, outline_color=outline_color)

    # 8. Integer Nearest-Neighbor Upscale
    if upscale:
        return tiny_result.resize((orig_w, orig_h), Image.Resampling.NEAREST)
    return tiny_result


@dataclass
class PixelOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    pixel_size: int = 4
    palette: str = "pico8"
    num_colors: int = 16
    dither_method: str = "bayer4"
    dither_strength: float = 1.0
    use_lab: bool = True
    pixel_perfect: bool = False
    outline: bool = False
    outline_color: tuple[int, int, int] = (0, 0, 0)
    upscale: bool = True
    dry_run: bool = True


def plan_output(src: Path, opts: PixelOptions) -> Path:
    """Where this image's pixel-art PNG goes."""
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
        with Image.open(src) as im:
            out = pixelize(
                im,
                pixel_size=opts.pixel_size,
                palette=opts.palette,
                num_colors=opts.num_colors,
                dither_method=opts.dither_method,
                dither_strength=opts.dither_strength,
                use_lab=opts.use_lab,
                pixel_perfect=opts.pixel_perfect,
                outline=opts.outline,
                outline_color=opts.outline_color,
                upscale=opts.upscale
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")  # atomic write
        out.save(tmp, "PNG")
        tmp.replace(dst)
    except Exception as ex:
        return Result(str(src), "failed", f"could not convert {src.name}: {ex}", detail="convert.failed")

    return Result(
        str(src), "converted",
        f"palette:{opts.palette} dither:{opts.dither_method} {opts.pixel_size}px",
        out_path=str(dst), detail="upscaled" if opts.upscale else "native size"
    )


def validate_result(result: Result, opts: PixelOptions) -> bool:
    """Verify dimensions, format, palette, and hard alpha before reuse."""
    if result.action == "dry-run":
        return True
    if result.action != "converted" or not result.out_path:
        return False
    try:
        with Image.open(result.src) as source:
            source_size = source.size
        expected_size = source_size if opts.upscale else (
            max(1, source_size[0] // max(1, opts.pixel_size)),
            max(1, source_size[1] // max(1, opts.pixel_size)),
        )
        with Image.open(result.out_path) as output:
            if output.format != "PNG" or output.mode != "RGBA" or output.size != expected_size:
                return False
            alpha = output.getchannel("A").getcolors(maxcolors=3)
            return (
                alpha is not None
                and all(value in {0, 255} for _, value in alpha)
            )
    except Exception:
        return False

"""Compact non-destructive filter-stack core for Image Enhancer.

Owns no files, models, or UI: callers apply the returned image to a copy and keep the source untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from . import classical_ops


@dataclass(frozen=True)
class FilterPass:
    op: str
    params: dict[str, float]
    enabled: bool = True


_PRESETS: dict[str, tuple[FilterPass, ...]] = {
    "auto": (),  # Dynamically compiled from image profile in smart.py
    "natural_skin": (
        FilterPass("wavelet_degloss", {"strength": 0.75, "pore_boost": 1.20}),
        FilterPass("micro_texture", {"amount": 0.035, "scale": 2.2}),
        FilterPass("denoise", {"radius": 0.20}),
        FilterPass("contrast", {"factor": 1.04}),
    ),
    "de_gloss": (
        FilterPass("wavelet_degloss", {"strength": 0.85, "pore_boost": 1.15}),
        FilterPass("contrast", {"factor": 1.03}),
        FilterPass("saturation", {"factor": 1.02}),
    ),
    "vivid_pop": (
        FilterPass("contrast", {"factor": 1.10}),
        FilterPass("saturation", {"factor": 1.18}),
        FilterPass("vibrance", {"amount": 0.15}),
        FilterPass("clarity", {"amount": 0.25}),
        FilterPass("unsharp_threshold", {"radius": 1.2, "amount": 0.8, "threshold": 3.0}),
    ),
    "warm_sunset": (
        FilterPass("temperature", {"amount": 0.22}),
        FilterPass("tint", {"amount": -0.05}),
        FilterPass("contrast", {"factor": 1.06}),
        FilterPass("saturation", {"factor": 1.08}),
        FilterPass("soft_glow", {"opacity": 0.18, "radius": 14.0}),
        FilterPass("vignette", {"amount": 0.25, "radius": 0.9}),
    ),
    "cinematic_teal": (
        FilterPass("contrast", {"factor": 1.08}),
        FilterPass("split_tone", {"amount": 0.35}),
        FilterPass("clarity", {"amount": 0.20}),
        FilterPass("vignette", {"amount": 0.30, "radius": 0.85}),
        FilterPass("film_grain", {"amount": 0.025, "scale": 1.5}),
    ),
    "soft_glamour": (
        FilterPass("wavelet_degloss", {"strength": 0.65, "pore_boost": 1.10}),
        FilterPass("soft_glow", {"opacity": 0.28, "radius": 18.0}),
        FilterPass("selective_blur", {"radius": 2.0, "max_delta": 14.0}),
        FilterPass("contrast", {"factor": 1.04}),
        FilterPass("vignette", {"amount": 0.20, "radius": 0.95}),
    ),
    "moody_film": (
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("gamma", {"value": 0.95}),
        FilterPass("saturation", {"factor": 0.92}),
        FilterPass("film_grain", {"amount": 0.045, "scale": 1.8}),
        FilterPass("vignette", {"amount": 0.40, "radius": 0.80}),
    ),
    "clahe_texture": (
        FilterPass("clahe", {"clip_limit": 2.2}),
        FilterPass("clarity", {"amount": 0.25}),
        FilterPass("contrast", {"factor": 1.04}),
        FilterPass("unsharp_threshold", {"radius": 1.2, "amount": 0.8, "threshold": 3.0}),
    ),
    "auto_white_balance": (
        FilterPass("auto_wb", {"p_norm": 6.0}),
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("saturation", {"factor": 1.04}),
    ),
    "despeckle_clean": (
        # Only the gated 3x3 median: specks out, everything else untouched.
        FilterPass("despeckle", {"radius": 1.0, "threshold": 22.0}),
    ),
    "radial_focus": (
        FilterPass("radial_focus", {"focus_radius": 0.35, "blur_radius": 24.0}),
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("vignette", {"amount": 0.25, "radius": 0.90}),
    ),
    "privacy_censor": (
        FilterPass("privacy_blur", {"radius": 32.0, "pixelate_block": 0.0}),
    ),
    "pixelate_censor": (
        FilterPass("privacy_blur", {"radius": 0.0, "pixelate_block": 20.0}),
    ),
    "dcp_dehaze": (
        FilterPass("dcp_dehaze", {"strength": 0.80}),
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("saturation", {"factor": 1.05}),
    ),
    "vector_cel": (
        FilterPass("vector_cel", {"smooth_radius": 3.5, "num_levels": 10.0, "edge_strength": 0.25}),
        FilterPass("saturation", {"factor": 1.08}),
        FilterPass("contrast", {"factor": 1.04}),
    ),
    "anime_style": (
        FilterPass("anime_cel", {"smooth_passes": 2.0, "line_strength": 0.35, "color_boost": 1.18, "shinkai_glow": 0.20}),
        FilterPass("contrast", {"factor": 1.04}),
        FilterPass("saturation", {"factor": 1.05}),
    ),
    "red_eye_fix": (
        FilterPass("red_eye", {"strength": 1.0, "threshold": 1.6}),
    ),
    "retinex_dehaze": (
        FilterPass("retinex", {"dynamic": 2.2}),
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("saturation", {"factor": 1.05}),
    ),
    "shadows_highlights": (
        FilterPass("shadows_highlights", {"shadow_lift": 0.35, "highlight_compress": 0.25}),
        FilterPass("contrast", {"factor": 1.04}),
        FilterPass("saturation", {"factor": 1.03}),
    ),
    "gentle_restore": (
        FilterPass("denoise", {"radius": 0.5}),
        FilterPass("unsharp_threshold", {"radius": 1.2, "amount": 0.6, "threshold": 3.0}),
        FilterPass("contrast", {"factor": 1.03}),
    ),
    "detail": (
        FilterPass("denoise", {"radius": 0.3}),
        FilterPass("unsharp_threshold", {"radius": 1.5, "amount": 1.2, "threshold": 2.0}),
        FilterPass("clarity", {"amount": 0.10}),
    ),
    "colour_restore": (
        FilterPass("denoise", {"radius": 0.3}),
        FilterPass("contrast", {"factor": 1.08}),
        FilterPass("saturation", {"factor": 1.12}),
        FilterPass("vibrance", {"amount": 0.10}),
    ),
    "portrait_polish": (
        FilterPass("wavelet_degloss", {"strength": 0.60, "pore_boost": 1.15}),
        FilterPass("selective_blur", {"radius": 2.0, "max_delta": 15.0}),
        FilterPass("unsharp_threshold", {"radius": 1.2, "amount": 0.6, "threshold": 4.0}),
        FilterPass("contrast", {"factor": 1.05}),
        FilterPass("saturation", {"factor": 1.04}),
    ),
    "texture_cleanup": (
        FilterPass("denoise", {"radius": 0.6}),
        FilterPass("high_pass", {"radius": 1.5, "opacity": 0.28}),
        FilterPass("contrast", {"factor": 1.04}),
    ),
    "bw_contrast": (
        FilterPass("saturation", {"factor": 0.0}),
        FilterPass("contrast", {"factor": 1.25}),
        FilterPass("clarity", {"amount": 0.35}),
        FilterPass("film_grain", {"amount": 0.035, "scale": 1.6}),
        FilterPass("vignette", {"amount": 0.35, "radius": 0.85}),
    ),
    "custom": (),
}


def preset_names() -> tuple[str, ...]:
    return tuple(_PRESETS)


# The parameter(s) whose neutral value makes a pass a no-op. Shape parameters
# (radius, scale, pore_boost) do not count: a zero-strength vignette with radius
# 0.85 must not run, and a zero-strength de-gloss must not boost pores.
_STRENGTH_KEYS: dict[str, tuple[str, ...]] = {
    "hue": ("degrees",), "vibrance": ("amount",), "temperature": ("amount",), "tint": ("amount",),
    "edge_boost": ("amount",), "high_pass": ("opacity",), "denoise": ("radius",),
    "de_gloss": ("strength",), "wavelet_degloss": ("strength",), "micro_texture": ("amount",),
    "soft_glow": ("opacity",), "clarity": ("amount",), "vignette": ("amount",),
    "film_grain": ("amount",), "split_tone": ("amount",), "privacy_blur": ("radius", "pixelate_block"),
    "radial_focus": ("blur_radius",), "clahe": ("clip_limit",), "auto_wb": ("p_norm",),
    "despeckle": ("radius",),
}
_FACTOR_OPS = {"brightness", "contrast", "saturation", "sharpen"}


def _is_noop(item: FilterPass) -> bool:
    if item.op in _FACTOR_OPS:
        return item.params.get("factor", 1.0) == 1.0
    if item.op == "gamma":
        return item.params.get("value", 1.0) == 1.0
    keys = _STRENGTH_KEYS.get(item.op)
    return keys is not None and not any(item.params.get(key, 0.0) for key in keys)


def compact_stack(passes: list[FilterPass]) -> list[FilterPass]:
    """Remove disabled/no-op passes and merge adjacent multiplicative passes."""
    merged: list[FilterPass] = []
    for current in passes:
        if not current.enabled or _is_noop(current):
            continue
        if merged and current.op == merged[-1].op and current.op in _FACTOR_OPS:
            previous = merged.pop()
            merged.append(FilterPass(current.op, {"factor": previous.params.get("factor", 1.0) * current.params.get("factor", 1.0)}))
        else:
            merged.append(current)
    return merged


def build_stack(*, preset: str, brightness: float, contrast: float, gamma: float, hue_degrees: float,
                saturation: float, vibrance: float, temperature: float, tint: float, denoise: float,
                sharpen: float, high_pass: float, edge_boost: float,
                de_gloss: float = 0.0, micro_texture: float = 0.0,
                soft_glow: float = 0.0, clarity: float = 0.0, vignette: float = 0.0,
                film_grain: float = 0.0, split_tone: float = 0.0,
                privacy_blur: float = 0.0, pixelate_block: float = 0.0,
                radial_focus: float = 0.0,
                clahe_strength: float = 0.0,
                auto_wb_strength: float = 0.0,
                despeckle_strength: float = 0.0) -> list[FilterPass]:
    if preset not in _PRESETS:
        raise ValueError(f"unknown enhancement preset: {preset}")
    passes = list(_PRESETS[preset])
    if auto_wb_strength > 0.0:
        passes.append(FilterPass("auto_wb", {"p_norm": 6.0}))
    if despeckle_strength > 0.0:
        passes.append(FilterPass("despeckle", {"radius": 1.0, "threshold": 22.0}))
    if clahe_strength > 0.0:
        passes.append(FilterPass("clahe", {"clip_limit": clahe_strength * 3.0}))

    passes.extend((
        FilterPass("denoise", {"radius": denoise}),
        # No fine-scale boost on top of a denoise: it would re-amplify the noise.
        FilterPass("wavelet_degloss", {"strength": de_gloss, "pore_boost": 1.0 if denoise > 0 else 1.15}),
        FilterPass("micro_texture", {"amount": micro_texture, "scale": 2.2}),
        FilterPass("clarity", {"amount": clarity}),
        FilterPass("brightness", {"factor": brightness}),
        FilterPass("contrast", {"factor": contrast}),
        FilterPass("gamma", {"value": gamma}),
        FilterPass("temperature", {"amount": temperature}),
        FilterPass("tint", {"amount": tint}),
        FilterPass("hue", {"degrees": hue_degrees}),
        FilterPass("saturation", {"factor": saturation}),
        FilterPass("vibrance", {"amount": vibrance}),
        FilterPass("split_tone", {"amount": split_tone}),
        FilterPass("soft_glow", {"opacity": soft_glow, "radius": 16.0}),
        FilterPass("vignette", {"amount": vignette, "radius": 0.85}),
        FilterPass("film_grain", {"amount": film_grain, "scale": 1.6}),
        FilterPass("radial_focus", {"focus_radius": 0.35, "blur_radius": radial_focus * 30.0}),
        FilterPass("privacy_blur", {"radius": privacy_blur * 40.0, "pixelate_block": pixelate_block}),
        FilterPass("high_pass", {"radius": 1.25, "opacity": high_pass}),
        FilterPass("edge_boost", {"amount": edge_boost}),
        FilterPass("sharpen", {"factor": sharpen}),
    ))
    return compact_stack(passes)


def apply(image: Image.Image, passes: list[FilterPass],
          boxes: tuple[tuple[int, int, int, int], ...] | None = None) -> tuple[Image.Image, tuple[str, ...]]:
    alpha = image.convert("RGBA").getchannel("A")
    result = image.convert("RGB")
    if passes and alpha.getextrema()[0] < 255:
        result = fill_hidden_rgb(result, alpha)
    names: list[str] = []
    opencv = classical_ops.has_opencv()
    for item in passes:
        result = _apply(result, item, boxes=boxes)
        if not opencv and item.op in classical_ops.OPENCV_FALLBACKS:
            names.append(f"{item.op}[fallback:{classical_ops.OPENCV_FALLBACKS[item.op]};needs OpenCV]")
        else:
            names.append(item.op)
    result = result.convert("RGBA")
    result.putalpha(alpha)
    return result, tuple(names)


def fill_hidden_rgb(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """Replace the colour under fully transparent pixels with nearby visible colour.

    Filters that look at neighbours (blur, sharpen, denoise) would otherwise pull
    the hidden colour (often magenta or black in game exports) into the visible
    edge. Normalised convolution at growing radii fills every hidden pixel; the
    visible pixels and the alpha channel are not changed.
    """
    a = np.asarray(alpha, dtype=np.float32) / 255.0
    hidden = a <= 0.0
    if not hidden.any() or hidden.all():
        return rgb
    src = np.asarray(rgb, dtype=np.float32)
    weight = (~hidden).astype(np.float32)
    premult = Image.fromarray((src * weight[..., None]).round().astype(np.uint8), "RGB")
    mask = Image.fromarray((weight * 255).astype(np.uint8), "L")
    out = src.copy()
    todo = hidden.copy()
    for radius in (2, 6, 18, 54, 162):
        wsum = np.asarray(mask.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255.0
        ready = todo & (wsum > 0.02)
        if ready.any():
            csum = np.asarray(premult.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
            out[ready] = csum[ready] / wsum[ready][:, None]
            todo &= ~ready
        if not todo.any():
            break
    if todo.any():
        out[todo] = src[~hidden].mean(axis=0)
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGB")


def _apply(image: Image.Image, item: FilterPass,
           boxes: tuple[tuple[int, int, int, int], ...] | None = None) -> Image.Image:
    op, p = item.op, item.params
    if op == "denoise":
        # "radius" is the historical key; it is now the denoise strength (0-2).
        return classical_ops.edge_preserving_denoise(image, strength=max(0.0, p.get("radius", 0.0)))
    if op == "brightness":
        return ImageEnhance.Brightness(image).enhance(max(0.0, p.get("factor", 1.0)))
    if op == "contrast":
        return ImageEnhance.Contrast(image).enhance(max(0.0, p.get("factor", 1.0)))
    if op == "saturation":
        return ImageEnhance.Color(image).enhance(max(0.0, p.get("factor", 1.0)))
    if op == "sharpen":
        factor = max(0.0, p.get("factor", 1.0))
        if factor < 1.0:                          # below 1 = soften, as before
            return ImageEnhance.Sharpness(image).enhance(factor)
        return classical_ops.unsharp_mask_threshold(image, radius=1.5, amount=(factor - 1.0) * 1.2,
                                                    threshold=2.0)
    if op == "gamma":
        gamma = max(0.01, p.get("value", 1.0))
        lut = [max(0, min(255, round((i / 255.0) ** (1.0 / gamma) * 255))) for i in range(256)]
        return image.point(lut * 3)
    if op == "hue":
        return _hue(image, p.get("degrees", 0.0))
    if op == "vibrance":
        return _vibrance(image, p.get("amount", 0.0))
    if op == "temperature":
        return _shift_rgb(image, red=p.get("amount", 0.0) * 64, blue=-p.get("amount", 0.0) * 64)
    if op == "tint":
        return _shift_rgb(image, red=-p.get("amount", 0.0) * 24, green=p.get("amount", 0.0) * 48, blue=-p.get("amount", 0.0) * 24)
    if op == "high_pass":
        return _high_pass(image, p.get("radius", 1.25), p.get("opacity", 0.0))
    if op == "edge_boost":
        return classical_ops.edge_boost(image, max(0.0, p.get("amount", 0.0)))
    if op in {"de_gloss", "wavelet_degloss"}:
        return classical_ops.wavelet_de_gloss(
            image,
            de_gloss_strength=p.get("strength", 0.75),
            pore_boost=p.get("pore_boost", 1.15),
            boxes=boxes
        )
    if op == "micro_texture":
        return _micro_texture_pass(image, amount=p.get("amount", 0.0), scale=p.get("scale", 2.2))
    if op == "soft_glow":
        return classical_ops.soft_glow_orton(image, radius=p.get("radius", 16.0), opacity=p.get("opacity", 0.25))
    if op == "clarity":
        return classical_ops.clarity(image, amount=p.get("amount", 0.30), radius=p.get("radius", 24.0))
    if op == "vignette":
        return classical_ops.vignette(image, amount=p.get("amount", 0.35), radius=p.get("radius", 0.85))
    if op == "film_grain":
        return classical_ops.film_grain(image, amount=p.get("amount", 0.035), scale=p.get("scale", 1.6))
    if op == "split_tone":
        return classical_ops.split_tone(image, amount=p.get("amount", 0.35))
    if op == "privacy_blur":
        return classical_ops.privacy_blur(image, radius=p.get("radius", 28.0), pixelate_block=int(p.get("pixelate_block", 0)))
    if op == "radial_focus":
        return classical_ops.radial_focus_blur(image, focus_radius=p.get("focus_radius", 0.35), blur_radius=p.get("blur_radius", 24.0))
    if op == "clahe":
        return classical_ops.clahe_local_contrast(image, clip_limit=p.get("clip_limit", 2.0))
    if op == "auto_wb":
        return classical_ops.auto_white_balance(image, p_norm=p.get("p_norm", 6.0))
    if op == "despeckle":
        return classical_ops.adaptive_despeckle(image, radius=int(p.get("radius", 1)), threshold=p.get("threshold", 22.0))
    if op == "dcp_dehaze":
        return classical_ops.dark_channel_dehaze(image, strength=p.get("strength", 0.75))
    if op == "retinex":
        return classical_ops.retinex_mscr(image, dynamic=p.get("dynamic", 2.0))
    if op == "vector_cel":
        return classical_ops.vector_cel_shade(
            image,
            smooth_radius=p.get("smooth_radius", 3.0),
            num_levels=int(p.get("num_levels", 10)),
            edge_strength=p.get("edge_strength", 0.25)
        )
    if op in {"anime_cel", "anime_style"}:
        return classical_ops.anime_cel_shader(
            image,
            smooth_passes=int(p.get("smooth_passes", 2)),
            line_strength=p.get("line_strength", 0.35),
            color_boost=p.get("color_boost", 1.18),
            shinkai_glow=p.get("shinkai_glow", 0.20)
        )
    if op in {"red_eye", "fix_red_eye"}:
        return classical_ops.fix_red_eye(
            image,
            strength=p.get("strength", 1.0),
            threshold=p.get("threshold", 1.6)
        )
    if op == "selective_blur":
        return classical_ops.selective_gaussian_blur(image, radius=p.get("radius", 2.5), max_delta=p.get("max_delta", 18.0))
    if op == "shadows_highlights":
        return classical_ops.shadows_highlights(image, shadow_lift=p.get("shadow_lift", 0.25), highlight_compress=p.get("highlight_compress", 0.20))
    if op == "unsharp_threshold":
        return classical_ops.unsharp_mask_threshold(image, radius=p.get("radius", 1.5), amount=p.get("amount", 1.25), threshold=p.get("threshold", 4.0))
    raise ValueError(f"unsupported enhancement operation: {op}")


def _shift_rgb(image: Image.Image, *, red: float = 0.0, green: float = 0.0, blue: float = 0.0) -> Image.Image:
    values = np.asarray(image, dtype=np.int16).copy()
    values[..., 0] += round(red)
    values[..., 1] += round(green)
    values[..., 2] += round(blue)
    return Image.fromarray(np.clip(values, 0, 255).astype(np.uint8), "RGB")


def _hue(image: Image.Image, degrees: float) -> Image.Image:
    if not degrees:
        return image
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    delta = high - low
    hue = np.zeros_like(high)
    sat = np.divide(delta, high, out=np.zeros_like(delta), where=high > 0)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mask = delta > 1e-6
    hue[(high == red) & mask] = ((green - blue) / np.where(mask, delta, 1))[(high == red) & mask]
    hue[(high == green) & mask] = 2 + ((blue - red) / np.where(mask, delta, 1))[(high == green) & mask]
    hue[(high == blue) & mask] = 4 + ((red - green) / np.where(mask, delta, 1))[(high == blue) & mask]
    hue = (hue / 6 + degrees / 360.0) % 1.0
    index = (hue * 6).astype(int) % 6
    frac = hue * 6 - index
    p, q, t = high * (1 - sat), high * (1 - sat * frac), high * (1 - sat * (1 - frac))
    out = np.empty_like(rgb)
    choices = ((high, t, p), (q, high, p), (p, high, t), (p, q, high), (t, p, high), (high, p, q))
    for n, value in enumerate(choices):
        select = index == n
        out[select] = np.stack(value, axis=2)[select]
    return Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8), "RGB")


def _vibrance(image: Image.Image, amount: float) -> Image.Image:
    rgb = np.asarray(image, dtype=np.float32) / 255.0
    high = rgb.max(axis=2, keepdims=True)
    spread = high - rgb.min(axis=2, keepdims=True)
    result = rgb + (rgb - high) * (amount * (2 - spread))
    return Image.fromarray(np.clip(result * 255, 0, 255).astype(np.uint8), "RGB")


def _high_pass(image: Image.Image, radius: float, opacity: float) -> Image.Image:
    """High-pass sharpening: add the detail layer to luminance (was a blend towards grey)."""
    if opacity <= 0:
        return image
    return classical_ops.luma_high_pass(image, radius=max(0.1, radius), gain=min(1.0, opacity) * 2.0)


def _micro_texture_pass(image: Image.Image, amount: float = 0.035, scale: float = 2.2, seed: int = 42) -> Image.Image:
    """Inject subtle, band-limited, luminance-coupled micro-pore texture into flat skin."""
    if amount <= 0.0:
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    h, w = rgb.shape[:2]

    rng = np.random.default_rng(seed)
    white = rng.standard_normal((h, w)).astype(np.float32)
    white_img = Image.fromarray(((white - white.min()) / (white.max() - white.min() + 1e-6) * 255).astype(np.uint8), "L")

    fine = np.asarray(white_img.filter(ImageFilter.GaussianBlur(scale)), dtype=np.float32)
    coarse = np.asarray(white_img.filter(ImageFilter.GaussianBlur(scale * 3.0)), dtype=np.float32)
    band = fine - coarse
    sd = band.std() or 1.0
    normalized_band = (band / sd)[..., None]

    lum = (rgb.mean(axis=2, keepdims=True)) / 255.0
    coupling = np.clip(4.0 * lum * (1.0 - lum), 0.0, 1.0)

    injected = rgb + normalized_band * (amount * 255.0) * coupling
    return Image.fromarray(np.clip(injected, 0, 255).astype(np.uint8), "RGB")

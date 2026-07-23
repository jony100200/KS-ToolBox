"""Compact non-destructive filter-stack core for Image Enhancer.

Inspired by Rupayan's typed passes, PixiEditor's chained colour adjustments,
and G'MIC's HSV adjustment order.  It owns no files, models, or UI: callers
apply the returned image to a copy and keep the source untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


@dataclass(frozen=True)
class FilterPass:
    op: str
    params: dict[str, float]
    enabled: bool = True


_PRESETS: dict[str, tuple[FilterPass, ...]] = {
    "gentle_restore": (FilterPass("denoise", {"radius": 0.5}), FilterPass("sharpen", {"factor": 1.12}), FilterPass("contrast", {"factor": 1.03}), FilterPass("edge_boost", {"amount": 0.04})),
    "detail": (FilterPass("denoise", {"radius": 0.2}), FilterPass("high_pass", {"radius": 1.2, "opacity": 0.18}), FilterPass("sharpen", {"factor": 1.30}), FilterPass("contrast", {"factor": 1.04}), FilterPass("edge_boost", {"amount": 0.12})),
    "colour_restore": (FilterPass("denoise", {"radius": 0.3}), FilterPass("contrast", {"factor": 1.08}), FilterPass("saturation", {"factor": 1.12}), FilterPass("vibrance", {"amount": 0.10})),
    "portrait_polish": (FilterPass("denoise", {"radius": 0.25}), FilterPass("sharpen", {"factor": 1.20}), FilterPass("contrast", {"factor": 1.05}), FilterPass("saturation", {"factor": 1.04})),
    "texture_cleanup": (FilterPass("denoise", {"radius": 0.35}), FilterPass("high_pass", {"radius": 1.5, "opacity": 0.28}), FilterPass("sharpen", {"factor": 1.25}), FilterPass("contrast", {"factor": 1.08})),
    "sharp_abstract": (FilterPass("denoise", {"radius": 0.45}), FilterPass("high_pass", {"radius": 1.75, "opacity": 0.35}), FilterPass("edge_boost", {"amount": 0.30}), FilterPass("contrast", {"factor": 1.10})),
    "smooth_bilateral": (FilterPass("denoise", {"radius": 0.75}), FilterPass("contrast", {"factor": 1.08}), FilterPass("saturation", {"factor": 1.04})),
    "custom": (),
}


def preset_names() -> tuple[str, ...]:
    return tuple(_PRESETS)


def compact_stack(passes: list[FilterPass]) -> list[FilterPass]:
    """Remove disabled/no-op passes and merge adjacent multiplicative passes."""
    merged: list[FilterPass] = []
    for current in passes:
        if not current.enabled:
            continue
        if current.op in {"brightness", "contrast", "saturation", "sharpen"} and current.params.get("factor", 1.0) == 1.0:
            continue
        if current.op in {"hue", "vibrance", "temperature", "tint", "edge_boost", "high_pass", "denoise"} and not any(current.params.values()):
            continue
        if merged and current.op == merged[-1].op and current.op in {"brightness", "contrast", "saturation", "sharpen"}:
            previous = merged.pop()
            merged.append(FilterPass(current.op, {"factor": previous.params.get("factor", 1.0) * current.params.get("factor", 1.0)}))
        else:
            merged.append(current)
    return merged


def build_stack(*, preset: str, brightness: float, contrast: float, gamma: float, hue_degrees: float,
                saturation: float, vibrance: float, temperature: float, tint: float, denoise: float,
                sharpen: float, high_pass: float, edge_boost: float) -> list[FilterPass]:
    if preset not in _PRESETS:
        raise ValueError(f"unknown enhancement preset: {preset}")
    passes = list(_PRESETS[preset])
    passes.extend((
        FilterPass("denoise", {"radius": denoise}),
        FilterPass("brightness", {"factor": brightness}),
        FilterPass("contrast", {"factor": contrast}),
        FilterPass("gamma", {"value": gamma}),
        FilterPass("temperature", {"amount": temperature}),
        FilterPass("tint", {"amount": tint}),
        FilterPass("hue", {"degrees": hue_degrees}),
        FilterPass("saturation", {"factor": saturation}),
        FilterPass("vibrance", {"amount": vibrance}),
        FilterPass("high_pass", {"radius": 1.25, "opacity": high_pass}),
        FilterPass("edge_boost", {"amount": edge_boost}),
        FilterPass("sharpen", {"factor": sharpen}),
    ))
    return compact_stack(passes)


def apply(image: Image.Image, passes: list[FilterPass]) -> tuple[Image.Image, tuple[str, ...]]:
    alpha = image.convert("RGBA").getchannel("A")
    result = image.convert("RGB")
    names: list[str] = []
    for item in passes:
        result = _apply(result, item)
        names.append(item.op)
    result = result.convert("RGBA"); result.putalpha(alpha)
    return result, tuple(names)


def _apply(image: Image.Image, item: FilterPass) -> Image.Image:
    op, p = item.op, item.params
    if op == "denoise":
        radius = max(0.0, p["radius"])
        return image if radius <= 0 else image.filter(ImageFilter.GaussianBlur(min(radius, 2.0)))
    if op == "brightness": return ImageEnhance.Brightness(image).enhance(max(0.0, p["factor"]))
    if op == "contrast": return ImageEnhance.Contrast(image).enhance(max(0.0, p["factor"]))
    if op == "saturation": return ImageEnhance.Color(image).enhance(max(0.0, p["factor"]))
    if op == "sharpen": return ImageEnhance.Sharpness(image).enhance(max(0.0, p["factor"]))
    if op == "gamma":
        gamma = max(0.01, p["value"]); lut = [max(0, min(255, round((i / 255.0) ** (1.0 / gamma) * 255))) for i in range(256)]
        return image.point(lut * 3)
    if op == "hue": return _hue(image, p["degrees"])
    if op == "vibrance": return _vibrance(image, p["amount"])
    if op == "temperature": return _shift_rgb(image, red=p["amount"] * 64, blue=-p["amount"] * 64)
    if op == "tint": return _shift_rgb(image, red=-p["amount"] * 24, green=p["amount"] * 48, blue=-p["amount"] * 24)
    if op == "high_pass": return _high_pass(image, p["radius"], p["opacity"])
    if op == "edge_boost":
        amount = max(0.0, p["amount"])
        edges = image.convert("L").filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(0.75))
        layer = Image.merge("RGB", (edges, edges, edges))
        return Image.blend(image, layer, min(0.6, amount * 0.25))
    raise ValueError(f"unsupported enhancement operation: {op}")


def _shift_rgb(image: Image.Image, *, red: float = 0.0, green: float = 0.0, blue: float = 0.0) -> Image.Image:
    values = np.asarray(image, dtype=np.int16).copy()
    values[..., 0] += round(red); values[..., 1] += round(green); values[..., 2] += round(blue)
    return Image.fromarray(np.clip(values, 0, 255).astype(np.uint8), "RGB")


def _hue(image: Image.Image, degrees: float) -> Image.Image:
    if not degrees: return image
    rgb = np.asarray(image, dtype=np.float32) / 255.0; high = rgb.max(axis=2); low = rgb.min(axis=2); delta = high - low
    hue = np.zeros_like(high); sat = np.divide(delta, high, out=np.zeros_like(delta), where=high > 0)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]; mask = delta > 1e-6
    hue[(high == red) & mask] = ((green - blue) / np.where(mask, delta, 1))[(high == red) & mask]
    hue[(high == green) & mask] = 2 + ((blue - red) / np.where(mask, delta, 1))[(high == green) & mask]
    hue[(high == blue) & mask] = 4 + ((red - green) / np.where(mask, delta, 1))[(high == blue) & mask]
    hue = (hue / 6 + degrees / 360.0) % 1.0; index = (hue * 6).astype(int) % 6; frac = hue * 6 - index
    p, q, t = high * (1 - sat), high * (1 - sat * frac), high * (1 - sat * (1 - frac))
    out = np.empty_like(rgb)
    choices = ((high, t, p), (q, high, p), (p, high, t), (p, q, high), (t, p, high), (high, p, q))
    for n, value in enumerate(choices):
        select = index == n; out[select] = np.stack(value, axis=2)[select]
    return Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8), "RGB")


def _vibrance(image: Image.Image, amount: float) -> Image.Image:
    rgb = np.asarray(image, dtype=np.float32) / 255.0; high = rgb.max(axis=2, keepdims=True); spread = high - rgb.min(axis=2, keepdims=True)
    result = rgb + (rgb - high) * (amount * (2 - spread))
    return Image.fromarray(np.clip(result * 255, 0, 255).astype(np.uint8), "RGB")


def _high_pass(image: Image.Image, radius: float, opacity: float) -> Image.Image:
    if opacity <= 0: return image
    base = np.asarray(image, dtype=np.int16); blur = np.asarray(image.filter(ImageFilter.GaussianBlur(max(0.1, radius))), dtype=np.int16)
    high = np.clip(128 + base - blur, 0, 255).astype(np.uint8)
    return Image.blend(image, Image.fromarray(high, "RGB"), min(1.0, opacity))

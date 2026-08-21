"""Cheap, deterministic routing and auto-plan compilation for Image Enhancer.

This module decides *what and how much* restoration an image needs.  It intentionally
does not load neural models or alter pixels: that keeps batch-wide analysis fast (<5ms)
and makes every recommendation explainable in the completion manifest.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

MODES = ("deterministic", "hybrid", "ai")


@dataclass(frozen=True)
class Profile:
    width: int
    height: int
    mean_luma: float
    shadow_fraction: float
    highlight_fraction: float
    saturation: float
    colour_cast: str
    cast_strength: float
    sharpness: float
    noise: float
    compression: float
    specular_gap: float
    confidence: float
    recommendation: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Plan:
    requested_mode: str
    effective_mode: str
    brightness: float
    contrast: float
    gamma: float
    saturation: float
    temperature: float
    sharpen: float
    denoise: float
    de_gloss: float
    micro_texture: float
    use_model: bool
    model_scale: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def skin_tone_signature(image) -> dict[str, float | int | bool]:
    """Return a conservative warm-surface colour signature for continuity QA.

    Compares the source and result pixel distribution so an enhancement
    cannot quietly bleach a warm complexion into an unnatural pale one.
    """
    rgb = _rgb(image)
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    saturation = np.divide(high - low, high, out=np.zeros_like(high), where=high > 1e-5)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    red8, green8, blue8 = red * 255.0, green * 255.0, blue * 255.0
    cb = 128.0 - 0.168736 * red8 - 0.331264 * green8 + 0.5 * blue8
    cr = 128.0 + 0.5 * red8 - 0.418688 * green8 - 0.081312 * blue8
    channel_range = high - low
    mask = ((red8 > 95) & (green8 > 40) & (blue8 > 20) & (channel_range * 255.0 > 15)
            & (np.abs(red8 - green8) > 15) & (red > green) & (red > blue)
            & (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173))
    values = rgb[mask]
    if values.shape[0] < max(64, rgb.shape[0] * rgb.shape[1] // 500):
        return {"available": False, "pixels": int(values.shape[0])}
    median = np.median(values, axis=0)
    normalized = median / max(float(median.mean()), 1e-6)
    return {
        "available": True,
        "pixels": int(values.shape[0]),
        "red_ratio": round(float(normalized[0]), 4),
        "green_ratio": round(float(normalized[1]), 4),
        "blue_ratio": round(float(normalized[2]), 4),
        "luma": round(float(np.median(luma[mask])), 4),
        "saturation": round(float(np.median(saturation[mask])), 4),
    }


def skin_tone_guard(source, result) -> dict[str, float | int | bool | str]:
    """Compare source/result warm-surface signatures and state review need."""
    before = skin_tone_signature(source)
    after = skin_tone_signature(result)
    if before["available"] and not after["available"]:
        return {"available": True, "needs_review": True, "reason": "source warm-surface signature disappeared",
                "source_pixels": int(before["pixels"]), "result_pixels": int(after["pixels"])}
    if not before["available"] or not after["available"]:
        return {"available": False, "needs_review": False, "reason": "insufficient warm-surface evidence",
                "source_pixels": int(before["pixels"]), "result_pixels": int(after["pixels"])}
    chroma_delta = float(np.sqrt(sum((float(before[key]) - float(after[key])) ** 2
                                      for key in ("red_ratio", "green_ratio", "blue_ratio"))))
    luma_delta = float(after["luma"]) - float(before["luma"])
    saturation_ratio = float(after["saturation"]) / max(float(before["saturation"]), 1e-4)

    needs_review = ((chroma_delta > 0.22 and saturation_ratio < 0.70)
                    or (luma_delta > 0.22 and saturation_ratio < 0.72))
    return {
        "available": True,
        "needs_review": needs_review,
        "reason": "warm-surface colour shift exceeds continuity threshold" if needs_review else "within continuity threshold",
        "chroma_delta": round(chroma_delta, 4),
        "luma_delta": round(luma_delta, 4),
        "saturation_ratio": round(saturation_ratio, 4),
        "source_pixels": int(before["pixels"]),
        "result_pixels": int(after["pixels"]),
    }


def _rgb(image) -> np.ndarray:
    array = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("Image Enhancer needs an RGB/RGBA image")
    return array


def _to_linear(srgb: np.ndarray) -> np.ndarray:
    low = srgb / 12.92
    high = ((srgb + 0.055) / 1.055) ** 2.4
    return np.where(srgb <= 0.04045, low, high)


def analyse(image) -> Profile:
    """Profile an image using only a bounded, downsampled NumPy copy in milliseconds."""
    from PIL import Image

    source = image.convert("RGB")
    longest = max(source.size)
    if longest > 512:
        scale = 512 / longest
        source = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.Resampling.BILINEAR)
    rgb = _rgb(source)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    luma_linear = _to_linear(rgb)[..., 0] * 0.2126 + _to_linear(rgb)[..., 1] * 0.7152 + _to_linear(rgb)[..., 2] * 0.0722
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    saturation = float(np.mean(np.divide(high - low, high, out=np.zeros_like(high), where=high > 1e-5)))

    # Robust Laplacian MAD Noise estimation and edge sharpness
    gray = luma
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    contrast = float(gray.std()) + 1e-6
    sharpness = float(np.clip((lap.std() / contrast) * 1.2, 0.0, 1.0)) if lap.size else 0.0

    # Robust noise floor ignoring real edges via Median Absolute Deviation
    mad = float(np.median(np.abs(lap - np.median(lap)))) if lap.size else 0.0
    noise = float(np.clip((mad / 0.6745 / np.sqrt(20.0)) * 6.0, 0.0, 1.0))
    compression = float(np.clip(np.mean(np.abs(np.diff(gray, axis=1))) * 4.0, 0.0, 1.0))

    # Specular gap (P92 - P45)
    p45, p92 = (float(v) for v in np.percentile(luma, (45, 92)))
    specular_gap = float(np.clip(p92 - p45, 0.0, 1.0))

    # Color Cast & Neutral Pixel Gating
    means = rgb.reshape(-1, 3).mean(axis=0)
    spread = float(means.max() - means.min())
    dominant = int(np.argmax(means))
    cast = ("warm" if dominant == 0 else "cool" if dominant == 2 else "green") if spread > 0.035 else "neutral"
    shadow_fraction = float(np.mean(luma < 0.08))
    highlight_fraction = float(np.mean(luma > 0.96))
    mean_luma_linear = float(luma_linear.mean())
    confidence = 1.0
    notes: list[str] = []

    if shadow_fraction > 0.28:
        confidence -= 0.15
        notes.append("heavy shadow clipping limits recovery")
    if highlight_fraction > 0.10:
        confidence -= 0.15
        notes.append("blown highlights cannot be reconstructed deterministically")
    if max(image.size) < 384:
        confidence -= 0.10
        notes.append("small source benefits from optional model upscale")

    severe = mean_luma_linear < 0.05 or (sharpness < 0.06 and noise > 0.09) or max(image.size) < 256
    moderate = mean_luma_linear < 0.18 or sharpness < 0.13 or noise > 0.075 or saturation > 0.72
    recommendation = "ai" if severe else "hybrid" if moderate else "deterministic"
    notes.insert(0, f"recommended {recommendation}")

    return Profile(
        width=image.width, height=image.height, mean_luma=round(float(luma.mean()), 4),
        shadow_fraction=round(shadow_fraction, 4), highlight_fraction=round(highlight_fraction, 4),
        saturation=round(saturation, 4), colour_cast=cast, cast_strength=round(spread, 4),
        sharpness=round(sharpness, 4), noise=round(noise, 4), compression=round(compression, 4),
        specular_gap=round(specular_gap, 4),
        confidence=round(float(np.clip(confidence, 0.0, 1.0)), 4), recommendation=recommendation,
        reasons=tuple(notes),
    )


def plan(profile: Profile, *, requested_mode: str, auto_select: bool, requested_scale: int) -> Plan:
    """Turn a profile into bounded non-destructive settings and model demand."""
    if requested_mode not in MODES:
        raise ValueError(f"unknown Smart Enhance mode: {requested_mode}")
    if requested_scale not in (1, 2, 3, 4):
        raise ValueError("requested scale must be 1, 2, 3, or 4")

    effective = profile.recommendation if auto_select and profile.confidence >= 0.55 else requested_mode
    reasons = list(profile.reasons)
    if auto_select:
        reasons.append(f"auto-router selected {effective} (confidence {profile.confidence:.2f})")

    # Linear-light based gain and tone adjustments
    brightness = 1.0 + min(0.65, max(0.0, 0.36 - profile.mean_luma) * 1.8)
    gamma = 1.0 + min(0.40, max(0.0, 0.30 - profile.mean_luma) * 1.2)
    contrast = 1.0 + min(0.15, max(0.0, 0.45 - (1.0 - profile.shadow_fraction - profile.highlight_fraction)) * 0.25)
    saturation = 1.0 - min(0.30, max(0.0, profile.saturation - 0.56) * 0.7)

    temperature = 0.0
    if profile.colour_cast == "warm":
        temperature = -min(0.22, profile.cast_strength * 1.6)
    elif profile.colour_cast == "cool":
        temperature = min(0.22, profile.cast_strength * 1.6)

    sharpen = 1.0
    if profile.sharpness < 0.18 and profile.noise < 0.06:
        sharpen += min(0.35, (0.18 - profile.sharpness) * 1.9)
    denoise = min(0.45, max(0.0, profile.noise - 0.04) * 3.0)

    # De-gloss and micro-texture planning
    de_gloss = 0.0
    if profile.specular_gap > 0.25:
        de_gloss = round(float(min(0.85, profile.specular_gap * 1.4)), 3)
        reasons.append(f"specular gap {profile.specular_gap:.2f} -> de-gloss {de_gloss:.2f}")

    micro_texture = 0.0
    if profile.sharpness < 0.12 and profile.noise < 0.03:
        micro_texture = 0.035
        reasons.append("flat/waxy surface detected -> inject micro-pore texture")

    model_scale = requested_scale
    if effective == "hybrid" and auto_select and model_scale == 1 and max(profile.width, profile.height) < 768:
        model_scale = 2
        reasons.append("small source requested x2 utility upscale")

    use_model = effective == "ai" or (effective == "hybrid" and model_scale > 1)
    if requested_mode == "deterministic":
        effective = "deterministic"
        model_scale = 1
        use_model = False
        reasons.append("deterministic mode forbids model loading")
    elif effective == "deterministic":
        model_scale = 1
        use_model = False
        reasons.append("deterministic mode forbids model loading")
    elif effective == "ai" and model_scale == 1:
        model_scale = 4
        reasons.append("AI mode uses x4 restoration then returns to source dimensions")

    return Plan(
        requested_mode=requested_mode, effective_mode=effective,
        brightness=round(brightness, 4), contrast=round(contrast, 4), gamma=round(gamma, 4),
        saturation=round(saturation, 4), temperature=round(temperature, 4),
        sharpen=round(sharpen, 4), denoise=round(denoise, 4),
        de_gloss=round(de_gloss, 4), micro_texture=round(micro_texture, 4),
        use_model=use_model, model_scale=model_scale, reasons=tuple(reasons),
    )

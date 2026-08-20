"""Cheap, deterministic routing for the public Smart Enhance modes.

This module decides *how much* restoration an image needs.  It intentionally
does not load models, touch files, or alter pixels: that keeps batch-wide
analysis fast and makes the recommendation explainable in the completion
manifest.  The engine owns execution and only loads an optional worker after
the selected mode and plan require one.
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
    use_model: bool
    model_scale: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def skin_tone_signature(image) -> dict[str, float | int | bool]:
    """Return a conservative warm-surface colour signature for continuity QA.

    This intentionally does not label ethnicity or identify a person.  It
    compares the source and result's own pixel distribution so an enhancement
    cannot quietly bleach a warm/brown complexion into a pale, desaturated one.
    It is a guardrail, not a replacement for owner review.
    """
    rgb = _rgb(image)
    high, low = rgb.max(axis=2), rgb.min(axis=2)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    saturation = np.divide(high - low, high, out=np.zeros_like(high), where=high > 1e-5)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # Conservative YCbCr + RGB skin-colour envelope.  The earlier broad
    # warm-surface heuristic included warm walls, wood, and clothing; that
    # turned ordinary white-balance into a false complexion alarm.  This is
    # still only a colour proxy — no person/ethnicity classification involved.
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
    # A legitimate white-balance correction changes chroma slightly.  The
    # failure we must catch is the paired loss of warm chroma/saturation that
    # makes a complexion read as washed out or white, not ordinary grading.
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
        raise ValueError("Smart Enhance needs an RGB/RGBA image")
    return array


def analyse(image) -> Profile:
    """Profile an image using only a bounded, downsampled NumPy copy."""
    from PIL import Image

    source = image.convert("RGB")
    longest = max(source.size)
    if longest > 512:
        scale = 512 / longest
        source = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), Image.Resampling.BILINEAR)
    rgb = _rgb(source)
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    saturation = float(np.mean(np.divide(high - low, high, out=np.zeros_like(high), where=high > 1e-5)))

    # Laplacian energy is a compact blur proxy.  Noise measures the residual
    # after a one-pixel local average, so large edges are not mistaken for grain.
    gray = luma
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    sharpness = float(np.clip(np.var(lap) * 50.0, 0.0, 1.0)) if lap.size else 0.0
    smooth = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]) * 0.25
    residual = gray[1:-1, 1:-1] - smooth
    noise = float(np.clip(np.median(np.abs(residual)) * 8.0, 0.0, 1.0)) if residual.size else 0.0
    compression = float(np.clip(np.mean(np.abs(np.diff(gray, axis=1))) * 4.0, 0.0, 1.0))

    means = rgb.reshape(-1, 3).mean(axis=0)
    spread = float(means.max() - means.min())
    dominant = int(np.argmax(means))
    cast = ("warm" if dominant == 0 else "cool" if dominant == 2 else "green") if spread > 0.035 else "neutral"
    shadow_fraction = float(np.mean(luma < 0.08))
    highlight_fraction = float(np.mean(luma > 0.96))
    mean_luma = float(luma.mean())
    confidence = 1.0
    notes: list[str] = []
    if shadow_fraction > 0.28:
        confidence -= 0.15; notes.append("heavy shadow clipping limits recovery")
    if highlight_fraction > 0.10:
        confidence -= 0.15; notes.append("blown highlights cannot be reconstructed deterministically")
    if max(image.size) < 384:
        confidence -= 0.10; notes.append("small source benefits from an optional model pass")

    severe = mean_luma < 0.12 or (sharpness < 0.06 and noise > 0.09) or max(image.size) < 256
    moderate = mean_luma < 0.28 or sharpness < 0.13 or noise > 0.075 or saturation > 0.72
    recommendation = "ai" if severe else "hybrid" if moderate else "deterministic"
    notes.insert(0, f"recommended {recommendation}")
    return Profile(
        width=image.width, height=image.height, mean_luma=round(mean_luma, 4),
        shadow_fraction=round(shadow_fraction, 4), highlight_fraction=round(highlight_fraction, 4),
        saturation=round(saturation, 4), colour_cast=cast, cast_strength=round(spread, 4),
        sharpness=round(sharpness, 4), noise=round(noise, 4), compression=round(compression, 4),
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
        reasons.append(f"experimental auto-route selected {effective} (confidence {profile.confidence:.2f})")
    brightness = 1.0 + min(0.75, max(0.0, 0.34 - profile.mean_luma) * 2.1)
    gamma = 1.0 + min(0.45, max(0.0, 0.30 - profile.mean_luma) * 1.3)
    contrast = 1.0 + min(0.18, max(0.0, 0.45 - (1.0 - profile.shadow_fraction - profile.highlight_fraction)) * 0.3)
    saturation = 1.0 - min(0.30, max(0.0, profile.saturation - 0.56) * 0.7)
    temperature = 0.0
    if profile.colour_cast == "warm": temperature = -min(0.25, profile.cast_strength * 1.7)
    elif profile.colour_cast == "cool": temperature = min(0.25, profile.cast_strength * 1.7)
    sharpen = 1.0
    if profile.sharpness < 0.16 and profile.noise < 0.08:
        sharpen += min(0.30, (0.16 - profile.sharpness) * 1.8)
    denoise = min(0.45, max(0.0, profile.noise - 0.045) * 3.2)

    # Only Hybrid/AI may ask for an AI worker. Hybrid uses it only when the
    # caller requested resolution or the auto-router found a genuinely small
    # source. AI mode is intentionally explicit and always runs the model.
    model_scale = requested_scale
    if effective == "hybrid" and auto_select and model_scale == 1 and max(profile.width, profile.height) < 768:
        model_scale = 2
        reasons.append("small source requested x2 utility upscale")
    use_model = effective == "ai" or (effective == "hybrid" and model_scale > 1)
    if effective == "deterministic":
        model_scale = 1
        use_model = False
        reasons.append("deterministic mode forbids model loading")
    elif effective == "ai" and model_scale == 1:
        # x4 then downsample acts as the conservative model-only restoration
        # path while preserving the requested output dimensions.
        model_scale = 4
        reasons.append("AI mode uses x4 restoration then returns to source dimensions")
    return Plan(
        requested_mode=requested_mode, effective_mode=effective,
        brightness=round(brightness, 4), contrast=round(contrast, 4), gamma=round(gamma, 4),
        saturation=round(saturation, 4), temperature=round(temperature, 4),
        sharpen=round(sharpen, 4), denoise=round(denoise, 4),
        use_model=use_model, model_scale=model_scale, reasons=tuple(reasons),
    )

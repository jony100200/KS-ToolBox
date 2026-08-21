"""Deterministic spatial, wavelet, and multi-scale image enhancement operations.

Pure Python & NumPy mathematical implementations:
    1. Multi-Scale Retinex with Color Restoration (MSRCR) - Logarithmic surround ratio
    2. Dyadic Wavelet Decomposition & Multi-Scale Frequency Pyramid
    3. Wavelet-Based Surface De-Gloss & Specular Sheen Compression
    4. Edge-Preserving Selective Surface Smoothing
    5. Asymmetric Shadows & Highlights Tonemapping
    6. Thresholded High-Pass Unsharp Masking
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


# ==============================================================================
# 1. Multi-Scale Retinex with Color Restoration (MSRCR)
# ==============================================================================

def retinex_mscr(image: Image.Image,
                 scales: tuple[float, ...] = (15.0, 80.0, 250.0),
                 weights: tuple[float, ...] | None = None,
                 dynamic: float = 2.0,
                 alpha: float = 125.0,
                 beta: float = 46.0) -> Image.Image:
    """Multi-Scale Retinex with Color Restoration (MSRCR).

    Provides dynamic range expansion, shadow recovery, and atmospheric
    dehazing on muddy or low-light images using multi-scale logarithmic ratio math.
    """
    img_rgb = np.asarray(image.convert("RGB"), dtype=np.float32) + 1.0  # +1 to avoid log(0)
    if weights is None:
        weights = tuple(1.0 / len(scales) for _ in scales)

    # 1. Multi-scale log ratio
    log_src = np.log(img_rgb)
    msr = np.zeros_like(img_rgb)

    for scale, weight in zip(scales, weights):
        blurred = np.asarray(image.convert("RGB").filter(ImageFilter.GaussianBlur(scale)), dtype=np.float32) + 1.0
        msr += weight * (log_src - np.log(blurred))

    # 2. Color restoration factor
    sum_channels = np.sum(img_rgb, axis=2, keepdims=True)
    color_restoration = beta * (np.log(alpha * img_rgb) - np.log(sum_channels))

    # 3. Combined MSRCR signal
    mscr = msr * color_restoration

    # 4. Statistical Dynamic Range Compression (mean ± dynamic * std)
    mean = np.mean(mscr, axis=(0, 1), keepdims=True)
    std = np.std(mscr, axis=(0, 1), keepdims=True) + 1e-6
    min_val = mean - dynamic * std
    max_val = mean + dynamic * std

    normalized = np.clip((mscr - min_val) / (max_val - min_val), 0.0, 1.0) * 255.0
    return Image.fromarray(normalized.astype(np.uint8), "RGB")


# ==============================================================================
# 2. Wavelet Decomposition & Dyadic Frequency Pyramid
# ==============================================================================

def wavelet_decompose(image: Image.Image, scales: int = 5) -> tuple[list[np.ndarray], np.ndarray]:
    """Dyadic Wavelet Decomposition.

    Splits the image into N scale detail bands and one coarse residual:
        Scale 1 (1px): Fine pores, individual hair strands, micro-grain.
        Scale 2 (2px): Skin texture, micro-grain, fabric weave.
        Scale 3 (4px): Specular highlight transitions, small blemishes, acne.
        Scale 4 (8px): Specular sheen boundaries, oily patches, harsh shadows.
        Scale 5 (16px): Broad facial contours, cheekbone shading.
        Residual: Broad ambient illumination and complexion color.

    Reversible identity: image == residual + sum(scale_layers)
    """
    current = np.asarray(image.convert("RGB"), dtype=np.float32)
    bands: list[np.ndarray] = []

    for i in range(scales):
        radius = float(2 ** i)
        blurred = np.asarray(
            Image.fromarray(np.clip(current, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(radius)),
            dtype=np.float32
        )
        detail = current - blurred
        bands.append(detail)
        current = blurred

    residual = current
    return bands, residual


def wavelet_recombine(bands: list[np.ndarray], residual: np.ndarray,
                      scale_weights: list[float] | None = None) -> Image.Image:
    """Exact reconstruction of Wavelet Decomposed frequency bands with optional per-scale weighting."""
    out = residual.copy()
    if scale_weights is None:
        scale_weights = [1.0] * len(bands)

    for band, weight in zip(bands, scale_weights):
        out += band * weight

    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


def wavelet_de_gloss(image: Image.Image,
                     de_gloss_strength: float = 0.75,
                     pore_boost: float = 1.15,
                     boxes: tuple[tuple[int, int, int, int], ...] | None = None) -> Image.Image:
    """Studio-grade de-gloss using 5-scale Wavelet decomposition.

    Attenuates scales 3 & 4 (specular sheen boundaries) on skin areas while boosting
    scale 1 (fine pores), completely eliminating the oily look without plastic smoothing.
    """
    bands, residual = wavelet_decompose(image, scales=5)
    h, w = bands[0].shape[:2]

    # Skin/Face mask
    weight = np.ones((h, w, 1), dtype=np.float32)
    if boxes:
        weight.fill(0.0)
        for x1, y1, x2, y2 in boxes:
            pad_x, pad_y = (x2 - x1) * 0.4, (y2 - y1) * 0.7
            bx1, by1 = max(0, int(x1 - pad_x)), max(0, int(y1 - pad_y * 0.2))
            bx2, by2 = min(w, int(x2 + pad_x)), min(h, int(y2 + pad_y))
            weight[by1:by2, bx1:bx2, 0] = 1.0
        # Feather the mask
        weight = np.asarray(
            Image.fromarray((weight[:, :, 0] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(12.0)),
            dtype=np.float32
        )[:, :, None] / 255.0

    # Scale 1 (Pores): Boost slightly to restore crisp tactile realism
    bands[0] = bands[0] * (1.0 + (pore_boost - 1.0) * weight)

    # Scale 3 & 4 (Specular boundaries & oily sheen): Compress
    attenuation = 1.0 - (de_gloss_strength * 0.55 * weight)
    bands[2] = bands[2] * attenuation
    bands[3] = bands[3] * (1.0 - (de_gloss_strength * 0.40 * weight))

    return wavelet_recombine(bands, residual)


# ==============================================================================
# 3. Selective Gaussian Blur (Edge-Preserving Surface Smoothing)
# ==============================================================================

def selective_gaussian_blur(image: Image.Image, radius: float = 2.5, max_delta: float = 18.0) -> Image.Image:
    """Selective Gaussian Blur — smooths low-contrast variation without crossing sharp edges."""
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)

    diff = np.abs(src - blurred)
    # If difference is smaller than max_delta, blend toward blur; otherwise preserve original edge
    mask = np.clip(1.0 - (diff / max(max_delta, 1e-6)), 0.0, 1.0)

    out = src * (1.0 - mask) + blurred * mask
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


# ==============================================================================
# 4. Asymmetric Shadows & Highlights Tonemapping
# ==============================================================================

def shadows_highlights(image: Image.Image,
                       shadow_lift: float = 0.25,
                       highlight_compress: float = 0.20,
                       radius: float = 35.0) -> Image.Image:
    """Asymmetric Shadow/Highlight tonemapping."""
    src = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = src[..., 0] * 0.2126 + src[..., 1] * 0.7152 + src[..., 2] * 0.0722

    # Low-frequency illumination guide
    guide_img = Image.fromarray((luma * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(radius))
    guide = np.asarray(guide_img, dtype=np.float32) / 255.0

    # Shadow lift (smooth quadratic curve in dark regions)
    shadow_mask = np.clip(1.0 - guide, 0.0, 1.0)[..., None]
    lift = shadow_lift * 0.4 * shadow_mask * (1.0 - src) * src * 4.0

    # Highlight compress (soft shoulder in bright regions)
    highlight_mask = np.clip(guide, 0.0, 1.0)[..., None]
    compress = highlight_compress * 0.3 * highlight_mask * src * (1.0 - src) * 4.0

    out = np.clip((src + lift - compress) * 255.0, 0.0, 255.0)
    return Image.fromarray(out.astype(np.uint8), "RGB")


# ==============================================================================
# 5. Thresholded Unsharp Masking
# ==============================================================================

def unsharp_mask_threshold(image: Image.Image,
                           radius: float = 1.5,
                           amount: float = 1.25,
                           threshold: float = 4.0) -> Image.Image:
    """Unsharp Mask with Threshold — avoids amplifying noise in flat areas."""
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)

    diff = src - blurred
    abs_diff = np.abs(diff)

    # Threshold gate: ignore differences below threshold
    gate = np.where(abs_diff >= threshold, (abs_diff - threshold) / np.maximum(abs_diff, 1e-6), 0.0)
    sharpened = src + diff * (amount - 1.0) * gate

    return Image.fromarray(np.clip(sharpened, 0.0, 255.0).astype(np.uint8), "RGB")

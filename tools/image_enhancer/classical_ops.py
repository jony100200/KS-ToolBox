"""Deterministic spatial, wavelet, multi-scale, creative, and privacy image enhancement operations.

Pure Python & NumPy mathematical implementations:
    1. Multi-Scale Retinex with Color Restoration (MSRCR) - Logarithmic surround ratio
    2. Dyadic Wavelet Decomposition & Multi-Scale Frequency Pyramid
    3. Wavelet-Based Surface De-Gloss & Specular Sheen Compression
    4. Edge-Preserving Selective Surface Smoothing
    5. Asymmetric Shadows & Highlights Tonemapping
    6. Thresholded High-Pass Unsharp Masking
    7. Soft Glow / Orton Bloom Effect
    8. Midtone Clarity & Micro-Contrast
    9. Radial Vignette Lens Falloff
    10. Organic Analog Film Grain Emulsion
    11. Dual-Tone Split Color Grading
    12. Privacy & Censor Blur (Heavy Gaussian & Mosaic Pixelation)
    13. Background Optical Bokeh Blur (Depth-of-Field Subject Mask)
    14. Radial & Tilt-Shift Focus Blur
    15. Selective Box Region Censor Blur
    16. Contrast-Limited Adaptive Histogram Equalization (CLAHE)
    17. Auto White-Balance (Shades of Gray Minkowski Illuminant Estimation)
    18. Adaptive Median Despeckle (Single-Pixel AI Artifact Cleaner)
    19. 3-Way Quadratic Color Balance (Shadows / Midtones / Highlights)
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

    guide_img = Image.fromarray((luma * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(radius))
    guide = np.asarray(guide_img, dtype=np.float32) / 255.0

    shadow_mask = np.clip(1.0 - guide, 0.0, 1.0)[..., None]
    lift = shadow_lift * 0.4 * shadow_mask * (1.0 - src) * src * 4.0

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

    gate = np.where(abs_diff >= threshold, (abs_diff - threshold) / np.maximum(abs_diff, 1e-6), 0.0)
    sharpened = src + diff * (amount - 1.0) * gate

    return Image.fromarray(np.clip(sharpened, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 6. Soft Glow / Orton Bloom Effect
# ==============================================================================

def soft_glow_orton(image: Image.Image,
                    radius: float = 16.0,
                    opacity: float = 0.25,
                    threshold: float = 120.0) -> Image.Image:
    """Orton Effect / Soft Glow Diffusion for cinematic, romantic portrait lighting."""
    if opacity <= 0.0:
        return image
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    luma = src[..., 0] * 0.2126 + src[..., 1] * 0.7152 + src[..., 2] * 0.0722

    # High-pass threshold for bloom highlights
    bloom_src = np.where(luma[..., None] > threshold, src, threshold)
    bloom_img = Image.fromarray(np.clip(bloom_src, 0, 255).astype(np.uint8), "RGB").filter(ImageFilter.GaussianBlur(radius))
    bloom = np.asarray(bloom_img, dtype=np.float32)

    # Screen blend for luminous glow: 1 - (1 - a)*(1 - b)
    src_norm, bloom_norm = src / 255.0, bloom / 255.0
    screened = 1.0 - (1.0 - src_norm) * (1.0 - bloom_norm)

    out = src * (1.0 - opacity) + (screened * 255.0) * opacity
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 7. Midtone Clarity & Micro-Contrast
# ==============================================================================

def clarity(image: Image.Image,
            amount: float = 0.30,
            radius: float = 24.0) -> Image.Image:
    """Midtone Clarity / Micro-Contrast boost without clipping deep shadows or highlights."""
    if amount == 0.0:
        return image
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)

    diff = src - blurred
    luma = (src[..., 0] * 0.2126 + src[..., 1] * 0.7152 + src[..., 2] * 0.0722) / 255.0

    # Bell curve weighting for midtones: 4*x*(1-x)
    weight = np.clip(4.0 * luma * (1.0 - luma), 0.0, 1.0)[..., None]

    out = src + diff * (amount * 1.5) * weight
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 8. Radial Vignette Lens Falloff
# ==============================================================================

def vignette(image: Image.Image,
             amount: float = 0.35,
             radius: float = 0.85,
             softness: float = 0.5) -> Image.Image:
    """Radial lens falloff / vignette to draw focus to the center."""
    if amount <= 0.0:
        return image
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    h, w = src.shape[:2]

    y, x = np.ogrid[:h, :w]
    cx, cy = w / 2.0, h / 2.0
    dist = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)

    r_inner = max(0.0, radius * (1.0 - softness))
    r_outer = radius
    mask = np.clip((dist - r_inner) / max(1e-5, (r_outer - r_inner)), 0.0, 1.0)
    falloff = (1.0 - np.cos(mask * np.pi)) * 0.5

    darken = 1.0 - falloff[..., None] * amount
    out = src * darken
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 9. Organic Analog Film Grain Emulsion
# ==============================================================================

def film_grain(image: Image.Image,
               amount: float = 0.035,
               scale: float = 1.6,
               seed: int = 101) -> Image.Image:
    """Organic analog photographic film grain synthesis."""
    if amount <= 0.0:
        return image
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    h, w = src.shape[:2]

    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((h, w)).astype(np.float32)
    noise_img = Image.fromarray(((noise - noise.min()) / (noise.max() - noise.min() + 1e-6) * 255).astype(np.uint8), "L")

    fine = np.asarray(noise_img.filter(ImageFilter.GaussianBlur(scale)), dtype=np.float32)
    coarse = np.asarray(noise_img.filter(ImageFilter.GaussianBlur(scale * 2.5)), dtype=np.float32)
    grain = (fine - coarse)
    grain_sd = grain.std() or 1.0
    normalized_grain = (grain / grain_sd)[..., None]

    luma = (src.mean(axis=2, keepdims=True)) / 255.0
    film_response = np.clip(1.0 - (luma ** 1.8), 0.15, 1.0)

    out = src + normalized_grain * (amount * 255.0) * film_response
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 10. Dual-Tone Split Color Grading
# ==============================================================================

def split_tone(image: Image.Image,
               shadow_rgb: tuple[float, float, float] = (0.0, 0.15, 0.25),  # Deep Teal
               highlight_rgb: tuple[float, float, float] = (0.25, 0.18, 0.0), # Warm Amber
               amount: float = 0.35) -> Image.Image:
    """Cinematic Dual-Tone Split Color Grading (Shadows vs. Highlights)."""
    if amount <= 0.0:
        return image
    src = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = src[..., 0] * 0.2126 + src[..., 1] * 0.7152 + src[..., 2] * 0.0722

    shadow_weight = np.clip(1.0 - luma * 2.0, 0.0, 1.0)[..., None]
    highlight_weight = np.clip((luma - 0.5) * 2.0, 0.0, 1.0)[..., None]

    s_tint = np.array(shadow_rgb, dtype=np.float32) * amount
    h_tint = np.array(highlight_rgb, dtype=np.float32) * amount

    tinted = src + s_tint * shadow_weight + h_tint * highlight_weight
    return Image.fromarray(np.clip(tinted * 255.0, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 11. Privacy & Safe-for-Work Blur / Censor Modes
# ==============================================================================

def privacy_blur(image: Image.Image,
                 radius: float = 28.0,
                 pixelate_block: int = 0) -> Image.Image:
    """Privacy Shield / SFW Blur to obscure sensitive content from onlookers/kids.

    If pixelate_block > 0: applies mosaic pixelation censor.
    If pixelate_block == 0: applies heavy Gaussian frosted-glass blur.
    """
    if pixelate_block > 1:
        w, h = image.size
        small_w = max(1, w // pixelate_block)
        small_h = max(1, h // pixelate_block)
        return image.resize((small_w, small_h), Image.Resampling.NEAREST).resize((w, h), Image.Resampling.NEAREST)
    return image.filter(ImageFilter.GaussianBlur(max(1.0, radius)))


def background_bokeh_blur(image: Image.Image,
                          subject_mask: Image.Image,
                          blur_radius: float = 22.0) -> Image.Image:
    """Optical Bokeh Background Blur — keeps the subject sharp and blurs the background."""
    bg_blurred = image.filter(ImageFilter.GaussianBlur(blur_radius))
    mask_feathered = subject_mask.convert("L").filter(ImageFilter.GaussianBlur(4.0))
    return Image.composite(image, bg_blurred, mask_feathered)


def radial_focus_blur(image: Image.Image,
                      center: tuple[float, float] = (0.5, 0.5),
                      focus_radius: float = 0.35,
                      blur_radius: float = 24.0) -> Image.Image:
    """Radial / Tilt-Shift Focus Blur — keeps central subject in focus and blurs perimeter."""
    w, h = image.size
    blurred = image.filter(ImageFilter.GaussianBlur(blur_radius))

    y, x = np.ogrid[:h, :w]
    cx, cy = w * center[0], h * center[1]
    norm_dim = max(w, h)
    dist = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / norm_dim

    mask_arr = np.clip((dist - focus_radius) / 0.25, 0.0, 1.0)
    feather = ((1.0 - np.cos(mask_arr * np.pi)) * 0.5 * 255.0).astype(np.uint8)
    mask_img = Image.fromarray(feather, "L")

    return Image.composite(blurred, image, mask_img)


def box_censor_blur(image: Image.Image,
                    boxes: tuple[tuple[int, int, int, int], ...],
                    mode: str = "blur",
                    radius: float = 20.0,
                    pixelate_block: int = 16) -> Image.Image:
    """Censors specific bounding boxes (e.g. face regions or sensitive areas) with blur or pixelation."""
    if not boxes:
        return image
    result = image.copy()
    w, h = image.size

    for x1, y1, x2, y2 in boxes:
        bx1, by1 = max(0, x1), max(0, y1)
        bx2, by2 = min(w, x2), min(h, y2)
        if bx2 <= bx1 or by2 <= by1:
            continue
        crop = result.crop((bx1, by1, bx2, by2))
        if mode == "pixelate":
            cw, ch = crop.size
            small = crop.resize((max(1, cw // pixelate_block), max(1, ch // pixelate_block)), Image.Resampling.NEAREST)
            censored = small.resize((cw, ch), Image.Resampling.NEAREST)
        else:
            censored = crop.filter(ImageFilter.GaussianBlur(radius))
        result.paste(censored, (bx1, by1))

    return result


# ==============================================================================
# 12. Contrast-Limited Adaptive Histogram Equalization (CLAHE)
# ==============================================================================

def clahe_local_contrast(image: Image.Image,
                         clip_limit: float = 2.0,
                         grid_size: int = 8) -> Image.Image:
    """Adaptive localized histogram equalization (CLAHE) in perceptual LAB color space.

    Extracts rich texture in underexposed game art, fabric folds, and dark shadows
    without blowing out noise or global highlights.
    """
    try:
        import cv2
        arr = np.asarray(image.convert("RGB"))
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        clahe_obj = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(grid_size, grid_size))
        lab[:, :, 0] = clahe_obj.apply(lab[:, :, 0])
        rgb = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        return Image.fromarray(rgb)
    except Exception:
        # Graceful fallback: high-frequency enhancement
        return clarity(image, amount=clip_limit * 0.15)


# ==============================================================================
# 13. Auto White-Balance (Shades of Gray Minkowski Illuminant Estimation)
# ==============================================================================

def auto_white_balance(image: Image.Image, p_norm: float = 6.0) -> Image.Image:
    """Color constancy illuminant estimation (Shades of Gray / Minkowski p-norm).

    Automatically neutralizes aggressive green/yellow/blue tint casts without clipping whites.
    """
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    # Minkowski p-norm illuminant vector
    illuminant = np.power(np.mean(np.power(arr, p_norm), axis=(0, 1)), 1.0 / p_norm)
    mean_val = illuminant.mean()
    gains = mean_val / (illuminant + 1e-6)

    balanced = arr * gains
    return Image.fromarray(np.clip(balanced, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 14. Adaptive Despeckle (Single-Pixel AI Artifact Cleaner)
# ==============================================================================

def adaptive_despeckle(image: Image.Image,
                       radius: int = 2,
                       threshold: float = 25.0) -> Image.Image:
    """Selective median despeckling — cleans isolated pinhole noise and dead pixels.

    Only alters pixels that deviate from their neighborhood median by more than the threshold.
    """
    src = np.asarray(image.convert("RGB"), dtype=np.float32)
    med = np.asarray(image.filter(ImageFilter.MedianFilter(size=radius * 2 + 1)), dtype=np.float32)

    diff = np.abs(src - med)
    max_diff = np.max(diff, axis=2, keepdims=True)

    # Gate: replace only outlier pixels
    mask = np.clip((max_diff - threshold) / 10.0, 0.0, 1.0)
    out = src * (1.0 - mask) + med * mask
    return Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")


# ==============================================================================
# 15. Dark Channel Prior Atmospheric Dehazing (DCP)
# ==============================================================================

def dark_channel_dehaze(image: Image.Image,
                        strength: float = 0.75,
                        patch_size: int = 15,
                        omega: float = 0.95,
                        t0: float = 0.1) -> Image.Image:
    """Single-Image Atmospheric Dehazing via Dark Channel Prior (DCP).

    Physically models atmospheric light scattering to cut through deep fog,
    smoky washes, and atmospheric turbidity while preserving true foreground contrast.
    """
    img = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    h, w = img.shape[:2]

    # Downsampled proxy for rapid transmission estimation
    scale = 4
    sw, sh = max(1, w // scale), max(1, h // scale)
    small_img = image.resize((sw, sh), Image.Resampling.BILINEAR)
    small_arr = np.asarray(small_img.convert("RGB"), dtype=np.float32) / 255.0

    # 1. Dark Channel on proxy
    min_rgb = np.min(small_arr, axis=2)
    min_img = Image.fromarray((min_rgb * 255).astype(np.uint8), "L")
    dark_channel = np.asarray(min_img.filter(ImageFilter.MinFilter(max(3, patch_size // scale))), dtype=np.float32) / 255.0

    # 2. Estimate Atmospheric Light A (top 0.1% brightest in dark channel)
    num_top = max(1, int(sh * sw * 0.001))
    flat_dark = dark_channel.flatten()
    indices = np.argpartition(flat_dark, -num_top)[-num_top:]
    flat_img = small_arr.reshape(-1, 3)
    A = np.mean(flat_img[indices], axis=0)
    A = np.clip(A, 0.1, 1.0)

    # 3. Transmission Map on proxy
    norm_img = small_arr / A
    norm_min = np.min(norm_img, axis=2)
    norm_min_img = Image.fromarray((norm_min * 255).astype(np.uint8), "L")
    dark_norm = np.asarray(norm_min_img.filter(ImageFilter.MinFilter(max(3, patch_size // scale))), dtype=np.float32) / 255.0
    raw_t = 1.0 - (omega * strength) * dark_norm

    # Refine and upscale transmission map
    t_small = Image.fromarray(np.clip(raw_t * 255, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(4.0))
    t_full = t_small.resize((w, h), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(8.0))
    t_refined = np.maximum(np.asarray(t_full, dtype=np.float32) / 255.0, t0)[..., None]

    # 4. Radiance Recovery
    recovered = (img - A) / t_refined + A
    return Image.fromarray(np.clip(recovered * 255.0, 0.0, 255.0).astype(np.uint8), "RGB")

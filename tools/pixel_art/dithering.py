"""Ordered matrix and error-diffusion dithering algorithms for pixel art.

Pure Python & NumPy implementations:
    1. Bayer Matrix 2x2, 4x4, 8x8 (Classic 90s console ordered crosshatch)
    2. Floyd-Steinberg Error Diffusion (Smooth continuous diffusion)
    3. Atkinson Dithering (Apple Macintosh classic 1984 1/8th diffusion)
    4. None (Flat posterized cel-shading)
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import palettes


# ==============================================================================
# Normalized Bayer Threshold Matrices
# ==============================================================================

BAYER_2X2 = (np.array([
    [0, 2],
    [3, 1]
], dtype=np.float32) + 0.5) / 4.0 - 0.5

BAYER_4X4 = (np.array([
    [ 0,  8,  2, 10],
    [12,  4, 14,  6],
    [ 3, 11,  1,  9],
    [15,  7, 13,  5]
], dtype=np.float32) + 0.5) / 16.0 - 0.5

BAYER_8X8 = (np.array([
    [ 0, 32,  8, 40,  2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44,  4, 36, 14, 46,  6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [ 3, 35, 11, 43,  1, 33,  9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47,  7, 39, 13, 45,  5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21]
], dtype=np.float32) + 0.5) / 64.0 - 0.5


def bayer_dither(image_rgb: np.ndarray,
                 palette_rgb: np.ndarray,
                 matrix_size: int = 4,
                 strength: float = 1.0,
                 spread: float = 48.0,
                 use_lab: bool = True) -> np.ndarray:
    """Ordered Bayer Matrix Dithering.

    Fast, fully vectorized matrix crosshatching for authentic 8-bit/16-bit retro graphics.
    """
    if matrix_size == 2:
        bayer = BAYER_2X2
    elif matrix_size == 8:
        bayer = BAYER_8X8
    else:
        bayer = BAYER_4X4

    h, w = image_rgb.shape[:2]
    tile_y = int(np.ceil(h / bayer.shape[0]))
    tile_x = int(np.ceil(w / bayer.shape[1]))

    # Tile the Bayer matrix across the image shape
    tiled_bayer = np.tile(bayer, (tile_y, tile_x))[:h, :w, None]

    # Add threshold modulation
    modulated = image_rgb + (tiled_bayer * spread * strength)
    modulated = np.clip(modulated, 0.0, 255.0)

    # Nearest palette matching
    return palettes.apply_palette(modulated, palette_rgb, use_lab=use_lab)


def floyd_steinberg_dither(image_rgb: np.ndarray,
                           palette_rgb: np.ndarray,
                           strength: float = 1.0,
                           use_lab: bool = True) -> np.ndarray:
    """Floyd-Steinberg Error Diffusion Dithering (7/16, 3/16, 5/16, 1/16)."""
    h, w = image_rgb.shape[:2]
    work = image_rgb.astype(np.float32).copy()
    out = np.zeros((h, w, 3), dtype=np.uint8)

    for y in range(h):
        for x in range(w):
            old_pixel = work[y, x]
            # Find nearest palette color
            pal_idx = palettes.match_nearest_palette_index(old_pixel[None, None, :], palette_rgb, use_lab=use_lab)[0, 0]
            new_pixel = palette_rgb[pal_idx]
            out[y, x] = new_pixel

            error = (old_pixel - new_pixel) * strength
            if x + 1 < w:
                work[y, x + 1] += error * (7.0 / 16.0)
            if y + 1 < h:
                if x - 1 >= 0:
                    work[y + 1, x - 1] += error * (3.0 / 16.0)
                work[y + 1, x] += error * (5.0 / 16.0)
                if x + 1 < w:
                    work[y + 1, x + 1] += error * (1.0 / 16.0)

    return out


def atkinson_dither(image_rgb: np.ndarray,
                    palette_rgb: np.ndarray,
                    strength: float = 1.0,
                    use_lab: bool = True) -> np.ndarray:
    """Atkinson Error Diffusion Dithering (Classic 1984 Apple Macintosh 1/8th dispersion)."""
    h, w = image_rgb.shape[:2]
    work = image_rgb.astype(np.float32).copy()
    out = np.zeros((h, w, 3), dtype=np.uint8)

    for y in range(h):
        for x in range(w):
            old_pixel = work[y, x]
            pal_idx = palettes.match_nearest_palette_index(old_pixel[None, None, :], palette_rgb, use_lab=use_lab)[0, 0]
            new_pixel = palette_rgb[pal_idx]
            out[y, x] = new_pixel

            error = (old_pixel - new_pixel) * (strength / 8.0)
            if x + 1 < w:
                work[y, x + 1] += error
            if x + 2 < w:
                work[y, x + 2] += error
            if y + 1 < h:
                if x - 1 >= 0:
                    work[y + 1, x - 1] += error
                work[y + 1, x] += error
                if x + 1 < w:
                    work[y + 1, x + 1] += error
            if y + 2 < h:
                work[y + 2, x] += error

    return out


def dither_image(image_rgb: np.ndarray,
                 palette_rgb: np.ndarray,
                 method: str = "bayer4",
                 strength: float = 1.0,
                 use_lab: bool = True) -> np.ndarray:
    """Dispatches to the specified dithering method."""
    if method in {"bayer2", "bayer4", "bayer8"}:
        size = 2 if method == "bayer2" else 8 if method == "bayer8" else 4
        return bayer_dither(image_rgb, palette_rgb, matrix_size=size, strength=strength, use_lab=use_lab)
    if method == "floyd":
        return floyd_steinberg_dither(image_rgb, palette_rgb, strength=strength, use_lab=use_lab)
    if method == "atkinson":
        return atkinson_dither(image_rgb, palette_rgb, strength=strength, use_lab=use_lab)

    # Flat / None (Posterization)
    return palettes.apply_palette(image_rgb, palette_rgb, use_lab=use_lab)

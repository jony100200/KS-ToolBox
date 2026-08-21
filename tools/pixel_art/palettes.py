"""Curated retro hardware, classic console, and indie pixel-art palettes.

Pure Python & NumPy implementations of color quantization, CIELAB perceptual distance,
and authentic hardware color tables:
    - PICO-8 (16 colors)
    - Game Boy DMG-01 (4 olive shades)
    - Game Boy Pocket (4 LCD monochrome shades)
    - NES / Famicom (54 authentic 8-bit palette)
    - Commodore 64 (16 VIC-II shades)
    - CGA Mode 1 High (4 neon shades)
    - CGA Mode 1 Low (4 retro shades)
    - ENDESGA 32 (EDG32 indie standard)
    - Cyberpunk / Synthwave (8 neon shades)
    - 1-Bit Monochrome (2 shades)
    - Adaptive Auto (N colors via median-cut)
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    h = hex_code.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ==============================================================================
# Hardware Color Tables
# ==============================================================================

PALETTES: dict[str, list[str]] = {
    "pico8": [
        "#000000", "#1D2B53", "#7E2553", "#008751", "#AB5236", "#5F574F", "#C2C3C7", "#FFF1E8",
        "#FF004D", "#FFA300", "#FFEC27", "#00E436", "#29ADFF", "#83769C", "#FF77A8", "#FFCCAA"
    ],
    "gameboy_dmg": [
        "#0F380F", "#306230", "#8BAC0F", "#9BBC0F"
    ],
    "gameboy_pocket": [
        "#2B2B26", "#706B66", "#A89F91", "#C7BFAE"
    ],
    "c64": [
        "#000000", "#FFFFFF", "#880000", "#AAFFEE", "#CC44CC", "#00CC55", "#0000AA", "#EEEE77",
        "#DD8855", "#664400", "#FF7777", "#333333", "#777777", "#AAFF66", "#0088FF", "#BBBBBB"
    ],
    "cga_high": [
        "#000000", "#55FFFF", "#FF55FF", "#FFFFFF"
    ],
    "cga_low": [
        "#000000", "#55FF55", "#FF5555", "#FFFF55"
    ],
    "endesga32": [
        "#BE4A2F", "#D77643", "#EAD4AA", "#E4A672", "#B86F50", "#733E39", "#3E2731", "#A22633",
        "#E43B44", "#F77622", "#FEE761", "#63C74D", "#3E8948", "#265C42", "#193C3E", "#124E89",
        "#0099DB", "#2CE8F5", "#FFFFFF", "#C0CBDC", "#8B9BB4", "#5A6988", "#3A4466", "#262B44",
        "#181425", "#FF0044", "#68386C", "#B55088", "#F6757A", "#E8B796", "#C28569", "#794100"
    ],
    "cyberpunk8": [
        "#050510", "#08203E", "#2B0B3F", "#8B005D", "#FF0055", "#00FFCC", "#FFE600", "#FFFFFF"
    ],
    "monochrome": [
        "#000000", "#FFFFFF"
    ],
    "nes": [
        "#7C7C7C", "#0000FC", "#0000BC", "#4428BC", "#940084", "#A80020", "#A81000", "#881400",
        "#503000", "#007800", "#006800", "#005800", "#004058", "#000000", "#BCBCBC", "#0078F8",
        "#0058F8", "#6844FC", "#D800CC", "#E40058", "#F83800", "#E45C10", "#AC7C00", "#00B800",
        "#00A800", "#00A844", "#008888", "#F8F8F8", "#3CBCFC", "#6888FC", "#9878F8", "#F878F8",
        "#F85898", "#F87858", "#FCA044", "#F8B800", "#B8F818", "#58D854", "#58F898", "#00E8D8",
        "#787878", "#FCFCFC", "#A4E4FC", "#B8B8F8", "#D8B8F8", "#F8B8F8", "#F8A4C0", "#F0D0B0",
        "#FCE0A8", "#F8D878", "#D8F878", "#B8F8B8", "#B8F8D8", "#00FCFC", "#F8D8F8"
    ]
}

PALETTE_DISPLAY_NAMES = {
    "auto": "Auto (Adaptive Median-Cut)",
    "pico8": "PICO-8 (16 Colors)",
    "gameboy_dmg": "Game Boy DMG-01 (4 Greens)",
    "gameboy_pocket": "Game Boy Pocket (4 Grays)",
    "nes": "NES / Famicom (54 Colors)",
    "c64": "Commodore 64 (16 Colors)",
    "endesga32": "ENDESGA 32 (32 Colors)",
    "cga_high": "CGA Mode 1 High (4 Colors)",
    "cga_low": "CGA Mode 1 Low (4 Colors)",
    "cyberpunk8": "Cyberpunk Neon (8 Colors)",
    "monochrome": "1-Bit Monochrome (Black & White)",
}


def get_palette_rgb(palette_key: str, fallback_image: Image.Image | None = None, num_colors: int = 16) -> np.ndarray:
    """Returns a numpy array of shape (N, 3) representing the RGB palette."""
    if palette_key in PALETTES:
        hexes = PALETTES[palette_key]
        return np.array([_hex_to_rgb(h) for h in hexes], dtype=np.float32)

    # Adaptive palette from fallback image
    if fallback_image is not None:
        rgb_img = fallback_image.convert("RGB")
        quant = rgb_img.quantize(colors=max(2, num_colors), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        palette_bytes = quant.getpalette()[:num_colors * 3]
        return np.array(palette_bytes, dtype=np.float32).reshape(-1, 3)

    # Default PICO-8
    return np.array([_hex_to_rgb(h) for h in PALETTES["pico8"]], dtype=np.float32)


# ==============================================================================
# Perceptual CIELAB Color Matching
# ==============================================================================

def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB array (0..255) to CIELAB space for perceptual Delta-E distance."""
    try:
        import cv2
        u8 = np.clip(rgb, 0, 255).astype(np.uint8)
        if u8.ndim == 2:
            u8 = u8[None, :, :]
            lab = cv2.cvtColor(u8, cv2.COLOR_RGB2LAB).astype(np.float32)
            return lab[0]
        return cv2.cvtColor(u8, cv2.COLOR_RGB2LAB).astype(np.float32)
    except Exception:
        # Fallback luminance-weighted distance approximation
        weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
        return rgb * weights


def match_nearest_palette_index(image_rgb: np.ndarray, palette_rgb: np.ndarray, use_lab: bool = True) -> np.ndarray:
    """Finds index of nearest palette color for every pixel in image_rgb.

    image_rgb: (H, W, 3) float32
    palette_rgb: (K, 3) float32
    returns: (H, W) uint16 index array
    """
    if use_lab:
        img_space = rgb_to_lab(image_rgb)
        pal_space = rgb_to_lab(palette_rgb)
    else:
        img_space = image_rgb
        pal_space = palette_rgb

    # Vectorized Euclidean distance across K palette colors: (H, W, 1, 3) - (1, 1, K, 3)
    diff = img_space[:, :, None, :] - pal_space[None, None, :, :]
    dist_sq = np.sum(diff ** 2, axis=-1)  # (H, W, K)
    return np.argmin(dist_sq, axis=-1).astype(np.uint16)


def apply_palette(image_rgb: np.ndarray, palette_rgb: np.ndarray, use_lab: bool = True) -> np.ndarray:
    """Maps every pixel to the exact nearest palette color."""
    indices = match_nearest_palette_index(image_rgb, palette_rgb, use_lab=use_lab)
    return palette_rgb[indices].astype(np.uint8)

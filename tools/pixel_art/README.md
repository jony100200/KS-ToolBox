# Pixel Art Studio & Retro Palette Remapper

A standalone, batch-capable retro pixel-art converter and hardware palette remapping studio. Turn photos, illustrations, and 2D character renders into clean, authentic retro pixel-art sprites and scene art.

---

## Features

1. **Curated Retro Hardware & Indie Palettes:**
   * **PICO-8:** 16 iconic fantasy console colors.
   * **Game Boy (DMG-01):** 4 olive-green monochrome LCD shades.
   * **Game Boy Pocket:** 4 crisp grayscale LCD shades.
   * **NES / Famicom:** 54 authentic 8-bit console colors.
   * **Commodore 64:** 16 warm VIC-II CRT shades.
   * **CGA Mode 1 High / Low:** Classic 4-color PC gaming palettes.
   * **ENDESGA 32 (EDG32):** The indie game developer pixel-art standard.
   * **Cyberpunk Neon:** 8 electric synthwave neon shades.
   * **1-Bit Monochrome:** High-contrast pure black and white.
   * **Auto (Adaptive Median-Cut):** Dynamically quantized palette of $N$ colors.

2. **Advanced Dithering Algorithms:**
   * **Bayer Ordered Matrix ($2\times 2, 4\times 4, 8\times 8$):** Authentic 90s console crosshatching with controllable strength.
   * **Floyd-Steinberg:** Smooth error diffusion.
   * **Atkinson Dithering:** Classic 1984 Apple Macintosh 1/8th error dispersion.
   * **None (Cel-Shaded):** Flat, crisp, posterized pixel graphics.

3. **Perceptual CIELAB $\Delta E$ Color Distance Matching:**
   * Eliminates muddy greens, dull skintones, and wrong color assignments by matching palette colors in perceptual CIELAB color space.

4. **Pixel-Perfect Line Cleanup & Sprite Outlining:**
   * **L-Shape Corner Pruning:** Detects and erases duplicate 90-degree corner pixels on diagonal line art.
   * **Sprite Outlining:** Generates 1px black or custom-colored exterior outline around sprites with transparency.

5. **Interactive UI with Live Before/After Split Preview:**
   * Draggable split slider with sub-10ms CPU live preview.
   * `👁️ Hold to Compare` original image button.
   * Live interactive color swatch bar showing active palette colors.

---

## Headless CLI Batch Usage

```powershell
# Convert images with PICO-8 palette and Bayer 4x4 dithering
python -c "from pathlib import Path; from tools.pixel_art import engine as e; e.process('D:/path/sprite.png', e.PixelOptions(palette='pico8', pixel_size=4, dither_method='bayer4'))"
```

---

## Verification

```powershell
python -m tools.pixel_art.test_smoke
```

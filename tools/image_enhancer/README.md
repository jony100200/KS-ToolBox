# Image Enhancer

A standalone, local batch image enhancer, studio lighting editor, and surface restoration suite. It never starts or contacts ComfyUI or external servers. Every output is a new PNG, non-destructive, with the normal Toolbox queue, manifest reports, retry, and interactive Before/After preview controls.

---

## Interactive Studio UI & Live Split Preview

The desktop tool incorporates an Android-style photo editor workflow:
- **Interactive Before/After Split Canvas:** Drag the split divider to inspect adjustments side-by-side in real-time ($<10\text{ms}$ preview proxy on CPU).
- **👁️ Hold to Compare:** Momentarily flip the canvas to 100% original.
- **🛡️ Instant Privacy Shield:** One-click header toggle that overlays a heavy frosted-glass Gaussian blur across the canvas for privacy when family, kids, or colleagues are nearby.
- **Android-Style Studio Sliders:** Grouped into 4 clean tabbed categories with live formatted value badges and double-click to reset:
  1. *Light & Color:* Brightness, Contrast, Gamma, Saturation, Vibrance, CLAHE, Auto-WB, Color Temperature, Tint.
  2. *Skin & Texture:* 5-Scale Wavelet De-Gloss, Pore Micro-Texture, Despeckle, Denoise, Sharpen, High-Pass, Edge Boost.
  3. *Creative & Privacy Blur:* Soft Glow (Orton Bloom), Midtone Clarity, Vignette, Film Grain, Split Tone, Tilt-Shift Focus, Privacy Blur, Mosaic Pixelation.
  4. *Target Regions & Repair:* Salient Subject Mask (`u2netp`), Face Detail (`yunet`), Manual Box, Alpha Hole Inpainting (`telea`).

---

## One-Button Auto Enhance & Curated Presets

Select one image or an entire folder and press **Enhance Selected**.

Every item is automatically profiled in milliseconds on CPU:
- **Linear-Light Luma & Contrast:** Measures exposure, shadow clipping, and highlight blowout.
- **Color Balance & Cast:** Detects warm/cool/green casts on near-neutral pixels.
- **Noise vs Sharpness:** Computes robust Laplacian Median Absolute Deviation (MAD) to separate true edges from noise.
- **Surface Quality & De-Gloss:** Measures specular highlight distance ($P_{92} - P_{45}$) and waxy flat surfaces to compile a targeted, frequency-separated correction plan.

### Curated Presets:

1. **Auto (Recommended):** Analyzes each image independently, compiling a dynamic multi-band plan.
2. **Natural Skin / De-Gloss:** Compresses oily specular boundaries (Scales 3 & 4) while boosting fine skin pores (Scale 1) to eliminate plastic skin.
3. **De-Gloss / De-Shine:** Frequency-separated specular highlight compression on the low band only.
4. **Vivid Pop:** Crisp edge definition, boosted saturation, and midtone clarity.
5. **Warm Sunset:** Amber color temperature, soft glow, and subtle vignette.
6. **Cinematic Teal:** Teal/Orange dual-tone split grading, midtone clarity, and film grain.
7. **Soft Glamour / Glow:** Orton highlight diffusion and gentle skin smoothing.
8. **Moody Film:** Matte gamma curve, analog grain, and moody vignette.
9. **🔍 CLAHE Texture Extractor:** Contrast-Limited Adaptive Histogram Equalization in perceptual LAB space for dark game assets or muddy renders.
10. **✨ Auto White-Balance:** Shades-of-Gray Minkowski $p$-norm color constancy illuminant neutralization.
11. **🧹 Despeckle AI Artifacts:** Selective median outlier replacement for AI pinholes and dead pixels.
12. **🌫️ Dark Channel Prior Dehaze (DCP):** Physical atmospheric scattering removal for fog and murky washes.
13. **🎯 Radial / Tilt-Shift Focus:** Miniature and portrait circular focus blur.
14. **🛡️ Privacy Censor Blur:** Heavy Gaussian privacy blur for SFW protection.
15. **🔲 Mosaic Pixelate Censor:** Classic retro mosaic block pixelation.
16. **B&W Contrast:** Fine-art high-contrast monochrome with clarity and grain.
17. **Custom (Manual):** Full manual control over individual stack parameters.

---

## Specialist AI Micro-Model Rack ("Rack of Tiny Experts")

Instead of booting a giant multi-gigabyte generative diffusion stack, the engine uses **high-performance classical deterministic filters** with an optional **rack of tiny specialist micro-models (1–20 MB)** sitting beside the filters. (See [REFERENCES.md](REFERENCES.md) for full algorithmic formulations).

All models are strictly opt-in. The user can open the **📦 Model Rack** dialog in the UI to view direct links, descriptions, file sizes, and download only the experts they need:

| Category | Model Candidate | Size | Format | Primary Role |
| :--- | :--- | :--- | :--- | :--- |
| `SR_FAST` | **Real-ESRGAN General x4v3** | ~4.65 MB | Torch / INT8 | Ultra-tiny 1.21M parameter general scene restoration & 4x upscale. |
| `SR_FAST` | **Real-ESRGAN AnimeVideo v3 x2** | ~2.1 MB | NCNN / Torch | Native 2x super-resolution for illustrations and clean digital art. |
| `SR_QUALITY` | **SPAN-F** | ~1.3 MB | ONNX | NTIRE Efficient SR challenge winner; swift parameter-free attention. |
| `SR_QUALITY` | **SAFMN++** | ~1.5 MB | ONNX | Spatially-adaptive feature modulation for real-world super-resolution. |
| `MULTI_RESTORE` | **RAMiT** | ~4.2 MB | ONNX | Unified tiny transformer for denoising, deraining, and low-light enhancement. |
| `DEBLUR` | **NAFNet** | ~17.2 MB | ONNX | Nonlinear activation-free network; GoPro deblurring at 8.4% compute of prior SOTA. |
| `DENOISE_SMART` | **SCUNet** | ~24.5 MB | Torch | Swin-Conv UNet for practical blind real-world noise and compression artifacts. |
| `DEHAZE` | **DehazeFormer-T** | ~1.2 MB | ONNX | Tiny atmospheric haze and mist removal (25% params of classic FFA-Net). |
| `FACE_REPAIR` | **CodeFormer** | ~48.0 MB | Torch | Identity-guarded facial restoration fallback (opt-in only). |
| `DETECTION` | **YuNet** | ~0.23 MB | ONNX | 232 KB local face bounding box detector (CPU). |
| `SEGMENTATION` | **U2NetP** | ~4.57 MB | ONNX | 4.5 MB salient subject and character segmentation model (CPU). |

---

## Modes

- **Deterministic (Default):** Pure CPU execution via Pillow & NumPy (<60ms per 1K image). Zero models loaded. Never alters geometry, anatomy, text, composition, or facial identity.
- **Hybrid:** Starts with the deterministic plan and invokes local utility models (YuNet face detection, Real-ESRGAN super-resolution) only when requested by output scale or targeted face passes.
- **AI only:** Runs local Real-ESRGAN NCNN Vulkan super-resolution (2×/3×/4×) with native scale matching to prevent tile seams.

---

## Headless CLI Batch Runner (Fleet-Compatible)

For automated batch execution across fleet machines (**Boss**, **Laptop**, **Mint**):

```powershell
# One-button Auto Enhance on a single image or folder
python -m tools.image_enhancer.cli --input "D:\path\to\images" --out "D:\path\to\enhanced" --preset auto

# Natural Skin / De-Gloss pass on a character folder
python -m tools.image_enhancer.cli --input "D:\path\to\portraits" --out "D:\path\to\enhanced" --preset natural_skin --mode deterministic

# Super-resolution 2x with Real-ESRGAN NCNN
python -m tools.image_enhancer.cli --input "D:\path\to\images" --out "D:\path\to\enhanced" --scale 2 --model realesr-animevideov3-x2
```

---

## Verification

```powershell
python -m tools.image_enhancer.test_smoke
```

Runs the complete smoke test suite verifying Auto-Enhance, De-Gloss, CLAHE, Auto-WB, Despeckle, DCP Dehaze, Privacy Blurs, Model Rack, Alpha-Hole repair, Real-ESRGAN 2×, and Headless CLI execution.

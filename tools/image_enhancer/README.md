# Image Enhancer & Studio Restoration Suite

A local, non-destructive batch image enhancer, studio lighting editor, and surface restoration suite built for `KS-ToolBox`. It never connects to external cloud servers or requires heavy ComfyUI processes for enhancement. Every output is written cleanly as a new PNG while keeping the original file untouched, with full queue management, provenance manifests, retry mechanisms, and real-time interactive Before/After preview controls.

---

## 1. Core Philosophy: One-Button Auto Enhance

The primary workflow is **instant, single-button intelligence**:
1. Drop one image or an entire folder into the queue.
2. Select preset **Auto (Recommended)** and click **Enhance Selected**.

Each image is automatically profiled on CPU in milliseconds:
* **Linear-Light Luma & Dynamic Contrast:** Analyzes shadow clipping, midtone depth, and highlight blowout.
* **Color Balance & Cast:** Detects warm/cool/green illuminant casts on near-neutral surfaces and calculates correction factors.
* **Noise vs Edge Separation:** Uses Laplacian Median Absolute Deviation (MAD) to separate fine texture edges from sensor noise or AI generation grain.
* **Surface Quality & De-Gloss:** Evaluates specular highlight distance ($P_{92} - P_{45}$) to eliminate oily, waxy, or plastic artificial skin without flattening pores.
* **Dynamic Plan Compilation:** Compiles a custom-tailored multi-band filter pass specifically balanced for that individual image.

---

## 2. Interactive Studio UI & Live Before / After Canvas

For users who want manual control and real-time visual feedback:

* **Interactive Live Split Preview Canvas:**
  * Draggable vertical split divider to inspect changes side-by-side in real-time ($<10\text{ms}$ preview proxy on CPU).
  * `👁️ Hold to Compare` button for instantaneous comparison with the 100% untouched original.
  * `🛡️ Instant Privacy Shield` toggle in the header: immediately overlays a frosted-glass Gaussian blur across the canvas when kids, family, or colleagues are nearby.

* **Android-Style Tabbed Studio Sliders:**
  * **Light & Color:** Brightness, Contrast, Gamma, Saturation, Vibrance (skin-safe linear luminance), CLAHE Local Contrast, Auto White-Balance, Color Temperature, Tint.
  * **Skin & Texture:** 5-Scale Wavelet De-Gloss, Pore Micro-Texture, Despeckle Outlier Cleaning, Denoise, Selective Sharpening, High-Pass Micro-Contrast, Edge Boost.
  * **Creative & Privacy:** Soft Glow (Orton Bloom), Midtone Clarity, Vignette, Film Grain, Split Tone, Tilt-Shift Focus Blur, Vector/Cel-Shade Toon Stylizer, Privacy Blur, Mosaic Pixelation.
  * **Target Regions & Repair:** Salient Subject Masking (`u2netp`), Face Detail Crops (`yunet`), Manual Bounding Box, Enclosed Alpha Hole Inpainting (`telea`).

---

## 3. Curated Presets

| Preset | Description |
| :--- | :--- |
| **Auto (Recommended)** | Independent per-image profiling with dynamic multi-band plan compilation. |
| **Natural Skin / De-Gloss** | Dyadic wavelet decomposition compressing specular sheen on scales 3 & 4 while enhancing pores on scale 1. |
| **De-Gloss / De-Shine** | High-frequency preserving specular highlight compression. |
| **Vivid Pop** | Boosted edge clarity, vibrant saturation, and balanced midtone punch. |
| **Warm Sunset** | Golden hour color temperature, Orton soft glow, and subtle vignetting. |
| **Cinematic Teal** | Teal/Orange dual-tone split grading, midtone clarity, and analog film grain. |
| **Soft Glamour / Glow** | Orton highlight bloom with gentle skin smoothing. |
| **Moody Film** | Matte shadow curve, analog grain, and moody corner vignette. |
| **🔍 CLAHE Texture Extractor** | Contrast-Limited Adaptive Histogram Equalization in LAB space for dark game assets and muddy renders. |
| **✨ Auto White-Balance** | Minkowski $p=6$ Shades-of-Gray illuminant estimation and color constancy neutralization. |
| **🧹 Despeckle AI Artifacts** | Selective adaptive median outlier replacement for AI pinholes and sensor speckles. |
| **🌫️ Dark Channel Prior Dehaze (DCP)** | Physical atmospheric light scattering removal for smoke, fog, and murky contrast. |
| **🎨 Vector / Cel-Shade Toon** | Edge-preserving bilateral smoothing with posterized color quantization and subtle line strokes. |
| **🎯 Radial / Tilt-Shift Focus** | Depth-of-field portrait and miniature focus blur. |
| **🛡️ Privacy Censor Blur** | Heavy Gaussian privacy blur for SFW shielding. |
| **🔲 Mosaic Pixelate Censor** | Retro mosaic block censorship. |
| **B&W Contrast** | Fine-art monochrome conversion with clarity and grain. |
| **Custom (Manual)** | Full stack customization with arbitrary filter stacking. |

---

## 4. Specialist AI Micro-Model Rack ("Rack of Tiny Experts")

Instead of running a giant multi-gigabyte diffusion model, the engine combines **deterministic filters** with an optional **rack of specialist micro-models (1–20 MB)**.

All models are strictly opt-in and accessible via the **📦 Model Rack** manager dialog in the UI:

| Category | Model | Size | Format | Function |
| :--- | :--- | :--- | :--- | :--- |
| `SR_FAST` | **Real-ESRGAN General x4v3** | ~4.65 MB | Torch / INT8 | Ultra-compact 1.21M param super-resolution & restoration. |
| `SR_FAST` | **Real-ESRGAN AnimeVideo v3 x2** | ~2.1 MB | NCNN / Torch | Fast 2x upscale for anime, illustrations, and clean sprites. |
| `SR_QUALITY` | **SPAN-F** | ~1.3 MB | ONNX | Swift parameter-free attention super-resolution. |
| `SR_QUALITY` | **SAFMN++** | ~1.5 MB | ONNX | Spatially-adaptive feature modulation for detailed textures. |
| `MULTI_RESTORE` | **RAMiT** | ~4.2 MB | ONNX | Unified tiny transformer for denoising, deraining, and low-light. |
| `DEBLUR` | **NAFNet** | ~17.2 MB | ONNX | Nonlinear activation-free GoPro deblurring at 8.4% compute of prior SOTA. |
| `DENOISE_SMART` | **SCUNet** | ~24.5 MB | Torch | Swin-Conv UNet for practical blind compression and noise cleanup. |
| `DEHAZE` | **DehazeFormer-T** | ~1.2 MB | ONNX | Micro transformer for atmospheric mist removal. |
| `FACE_REPAIR` | **CodeFormer** | ~48.0 MB | Torch | Identity-guarded facial feature restoration fallback. |
| `DETECTION` | **YuNet** | ~0.23 MB | ONNX | 232 KB local face bounding box detector (CPU). |
| `SEGMENTATION` | **U2NetP** | ~4.57 MB | ONNX | 4.5 MB salient subject segmentation model (CPU). |

---

## 5. Execution Modes

* **Deterministic (Default):** Pure CPU execution via Pillow & NumPy (<60ms per 1K image). Zero models loaded. Never alters geometry, anatomy, text, composition, or facial identity.
* **Hybrid:** Executes the deterministic plan and invokes local utility models (YuNet face detection, Real-ESRGAN super-resolution) only when requested.
* **AI only:** Direct Real-ESRGAN NCNN Vulkan super-resolution (2×/3×/4×) with seamless scale matching.

---

## 6. Autonomous Agent & CLI Fleet Automation (Prothik / Kendro / CLI)

Image Enhancer is built to be driven either interactively via GUI or autonomously by background coding agents (**Prothik**, **Kendro**, or scripts) across fleet machines (**Boss**, **Laptop**, **Mint**).

### A. Python Programmatic API
```python
from pathlib import Path
from tools.image_enhancer import engine as e

# 1. One-button Auto Enhance
res = e.process(
    Path("D:/assets/character.png"),
    e.EnhanceOptions(preset="auto", mode="deterministic", out_root=Path("D:/enhanced"))
)
print("Enhanced image saved to:", res.out_path)

# 2. Natural Skin / De-Gloss on Portraits
res = e.process(
    Path("D:/assets/portrait.png"),
    e.EnhanceOptions(preset="natural_skin", de_gloss_strength=0.85, pore_boost=1.20)
)
```

### B. Headless CLI Runner
```powershell
# One-button Auto Enhance on a folder
python -m tools.image_enhancer.cli --input "D:\path\to\images" --out "D:\path\to\enhanced" --preset auto

# Natural Skin / De-Gloss pass on portraits
python -m tools.image_enhancer.cli --input "D:\path\to\portraits" --out "D:\path\to\enhanced" --preset natural_skin --mode deterministic

# Super-Resolution 2x via Real-ESRGAN NCNN
python -m tools.image_enhancer.cli --input "D:\path\to\images" --out "D:\path\to\enhanced" --scale 2 --model realesr-animevideov3-x2
```

---

## 7. Verification

```powershell
python -m tools.image_enhancer.test_smoke
```

Runs the complete automated test suite validating Auto-Enhance, De-Gloss, CLAHE, Auto-WB, Despeckle, DCP Dehaze, Vector Cel-Shade, Privacy Blurs, Model Rack, Alpha-Hole Inpainting, Real-ESRGAN 2×, and Headless CLI execution.

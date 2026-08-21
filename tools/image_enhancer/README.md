# Image Enhancer

A standalone, local batch image enhancer and surface restoration system. It never starts or contacts ComfyUI or external servers. Every output is a new PNG, non-destructive, with the normal Toolbox queue, manifest reports, retry, and preview controls.

---

## One-Button Auto Enhance

Select one image or an entire folder and press **Enhance Selected**.

Every item is automatically profiled in milliseconds on CPU:
- **Linear-Light Luma & Contrast:** Measures exposure, shadow clipping, and highlight blowout.
- **Color Balance & Cast:** Detects warm/cool/green casts on near-neutral pixels.
- **Noise vs Sharpness:** Computes robust Laplacian Median Absolute Deviation (MAD) to separate true edges from noise.
- **Surface Quality & De-Gloss:** Measures specular highlight distance ($P_{92} - P_{45}$) and waxy flat surfaces to compile a targeted, frequency-separated correction plan.

### Presets Available:

1. **Auto (Recommended):** Analyzes each image independently, compiling a dynamic multi-band plan.
2. **Natural Skin / De-Gloss:** Closes harsh specular-to-midtone highlight gaps on face/neck skin and injects subtle, luminance-coupled micro-pore texture into flat AI surfaces without destroying facial anatomy or eye reflections.
3. **De-Gloss / De-Shine:** Frequency-separated specular highlight compression on the low band only (leaving pores and fine hairs bit-for-bit intact on the high band).
4. **Gentle Restore:** Light denoise, edge clarity, and subtle contrast balance.
5. **Clarity & Detail:** High-pass micro-contrast boost, edge boost, and selective sharpening.
6. **Portrait Polish:** De-gloss, gentle smoothing, high-frequency pore preservation, and warm skin retention.
7. **Texture Cleanup:** High-pass filter with noise reduction for game textures and scans.
8. **Color Recovery:** Restores vibrancy, contrast, and neutral white-balance.
9. **Custom (Manual):** Full manual control over individual stack parameters.

---

## Specialist AI Micro-Model Rack ("Rack of Tiny Experts")

Instead of booting a giant multi-gigabyte generative diffusion stack, the engine uses **high-performance classical deterministic filters** with an optional **rack of tiny specialist micro-models (1–20 MB)** sitting behind the filters. (See [REFERENCES.md](REFERENCES.md) for full algorithmic formulations).

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

## Colour Continuity Guard

Before and after every run, Smart Enhance compares a conservative warm-surface colour signature in YCbCr space. It does not classify ethnicity or a person. If an enhancement materially bleaches or washes out a warm complexion, the item is flagged as **`needs-review`** in the manifest for operator review.

---

## Targeted Regions & Repair

- **Subject mask:** Uses local U2NetP ONNX (~4.5 MB) to limit enhancement to the main foreground subject.
- **Faces:** Uses local YuNet ONNX (~232 KB) for face bounding boxes; optional face-detail refinement applies super-resolution to detected crops and feather-blends them back.
- **Manual box:** Accepts `x,y,width,height` percentages for repeatable batch crops.
- **Repair enclosed transparent holes:** OpenCV Telea inpainting on interior alpha holes without touching outer sprite borders.

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

Runs the complete smoke test suite verifying Auto-Enhance, De-Gloss, Model Rack, Alpha-Hole repair, Real-ESRGAN 2×, and Headless CLI execution.

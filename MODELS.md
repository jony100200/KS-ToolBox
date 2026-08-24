# KS ToolBox — Micro-Model Directory & Weights Guide

> **Deterministic-First Principle:**  
> KS ToolBox runs **100% offline out-of-the-box**. All 19 tools work without any neural models using high-performance mathematical filters, Pillow, NumPy, and bundled FFmpeg.  
> 
> The neural models listed below are **optional micro-cartridges** (most under 5 MB) that provide AI-assisted super-resolution, background segmentation, face detection, motion deblurring, and style transfer.

---

## Quick Setup: Where to Place Models

Place downloaded model weights directly into the `models/` directory in the root of KS ToolBox:

```text
KS-ToolBox/
├── main.py
├── models/                     <--- Put .onnx, .pth, and NCNN binaries here
│   ├── face_detection_yunet_2023mar.onnx
│   ├── u2netp.onnx
│   ├── u2net.onnx
│   ├── span_f_x4.onnx
│   ├── safmn_plus_x4.onnx
│   ├── ramit_denoise_color.onnx
│   ├── nafnet_gopro_deblur.onnx
│   ├── dehazeformer_t.onnx
│   ├── animegan2_shinkai.onnx
│   ├── realesr-general-x4v3.pth
│   ├── realesr-animevideov3-x2.pth
│   ├── scunet_color_real_psnr.pth
│   └── codeformer.pth
└── tools/
```

---

## Micro-Model Catalog & Direct Download Links

### 1. Detection & Background Segmentation (Alpha Doctor & Image Enhancer)

| Model Name | Target Filename | Size | Purpose & Architecture | Direct Download Link |
|---|---|---|---|---|
| **YuNet Face Detector** | `face_detection_yunet_2023mar.onnx` | **232 KB** | Ultra-fast local face bounding box detector | [Download from OpenCV Zoo](https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx) |
| **U2NetP (Compact)** | `u2netp.onnx` | **4.57 MB** | Salient subject & portrait foreground segmentation mask | [Download from rembg v0.0.0](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx) |
| **U2Net (Full Precision)** | `u2net.onnx` | **176 MB** | High-precision background removal for complex photographs | [Download from rembg v0.0.0](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx) |

---

### 2. Super-Resolution & Detail Restoration (Image Enhancer & Image Rescale)

| Model Name | Target Filename | Size | Purpose & Architecture | Direct Download Link |
|---|---|---|---|---|
| **SPAN-F x4** | `span_f_x4.onnx` | **1.3 MB** | NTIRE Efficient SR Challenge Winner; parameter-free attention | [Download from SPAN v1.0](https://github.com/hongyuanyu/SPAN/releases/download/v1.0/span_f_x4.onnx) |
| **SAFMN++ x4** | `safmn_plus_x4.onnx` | **1.5 MB** | Spatially-Adaptive Feature Modulation; compact native ONNX | [Download from SAFMN v1.0](https://github.com/sunny2109/SAFMN/releases/download/v1.0/safmn_plus_x4.onnx) |
| **Real-ESRGAN Anime x2** | `realesr-animevideov3-x2.pth` | **2.1 MB** | Native 2x super-resolution for clean 2D anime, sprites, and line art | [Download from Real-ESRGAN v0.2.5](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3-x2.pth) |
| **Real-ESRGAN General x4v3** | `realesr-general-x4v3.pth` | **4.65 MB** | Tiny 1.21M parameter general-scene 4x upscale with denoise | [Download from Real-ESRGAN v0.2.5](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth) |
| **Real-ESRGAN NCNN Vulkan** | `realesrgan-ncnn-20220424/` | **~25 MB** | Zero-dependency standalone GPU Vulkan upscaler (`.exe` + `.param`/`.bin`) | [Download Real-ESRGAN NCNN Windows](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip) |

---

### 3. Specialized Restoration: Deblur, Denoise, Dehaze & Face Repair

| Model Name | Target Filename | Size | Purpose & Architecture | Direct Download Link |
|---|---|---|---|---|
| **DehazeFormer-T** | `dehazeformer_t.onnx` | **1.2 MB** | Ultra-light atmospheric mist and haze removal (25% size of FFA-Net) | [Download from DehazeFormer v1.0](https://github.com/IDKiro/DehazeFormer/releases/download/v1.0/dehazeformer_t.onnx) |
| **RAMiT Multi-Restore** | `ramit_denoise_color.onnx` | **4.2 MB** | Reciprocal Attention Mixing Transformer for low-light & noise | [Download from RAMiT v1.0](https://github.com/rami0205/RAMiT/releases/download/v1.0/ramit_denoise_color.onnx) |
| **AnimeGANv2 (Shinkai)** | `animegan2_shinkai.onnx` | **8.2 MB** | Cinematic anime art style transfer | [Download from AnimeGAN2 v0.1](https://github.com/bryandlee/animegan2-pytorch/releases/download/v0.1/face_paint_512_v2.onnx) |
| **NAFNet GoPro Deblur** | `nafnet_gopro_deblur.onnx` | **17.2 MB** | Nonlinear Activation Free Network for camera motion deblurring | [Download from NAFNet v0.1.0](https://github.com/megvii-research/NAFNet/releases/download/v0.1.0/nafnet_gopro_deblur.onnx) |
| **SCUNet Blind Denoise** | `scunet_color_real_psnr.pth` | **24.5 MB** | Swin-Conv UNet practical blind real-world noise & JPEG cleanup | [Download from SCUNet v1.0](https://github.com/cszn/SCUNet/releases/download/v1.0/scunet_color_real_psnr.pth) |
| **CodeFormer Face Repair** | `codeformer.pth` | **48.0 MB** | Identity-guarded facial restoration with controllable fidelity | [Download from CodeFormer v0.1.0](https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth) |

---

## Automated Download Script (Optional)

You can download all recommended ONNX micro-models automatically with PowerShell or Python:

### PowerShell (Windows One-Liner):
```powershell
$models = @{
    "face_detection_yunet_2023mar.onnx" = "https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    "u2netp.onnx" = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
    "span_f_x4.onnx" = "https://github.com/hongyuanyu/SPAN/releases/download/v1.0/span_f_x4.onnx"
    "safmn_plus_x4.onnx" = "https://github.com/sunny2109/SAFMN/releases/download/v1.0/safmn_plus_x4.onnx"
    "dehazeformer_t.onnx" = "https://github.com/IDKiro/DehazeFormer/releases/download/v1.0/dehazeformer_t.onnx"
    "ramit_denoise_color.onnx" = "https://github.com/rami0205/RAMiT/releases/download/v1.0/ramit_denoise_color.onnx"
    "animegan2_shinkai.onnx" = "https://github.com/bryandlee/animegan2-pytorch/releases/download/v0.1/face_paint_512_v2.onnx"
}

New-Item -ItemType Directory -Force -Path "models" | Out-Null
foreach ($name in $models.Keys) {
    $target = Join-Path "models" $name
    if (-not (Test-Path $target)) {
        Write-Host "Downloading $name..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $models[$name] -OutFile $target
    }
}
Write-Host "All core micro-models downloaded successfully!" -ForegroundColor Green
```

---

## Verifying Model Readiness in KS ToolBox

To verify which models are installed and ready:

```bash
# Via Python
python -c "from tools.image_enhancer.model_rack import get_rack_status; import pprint; pprint.pprint(get_rack_status())"
```

Or open the **Image Enhancer** / **Alpha Doctor** tab in the KS ToolBox UI — the status pill in the top right will show **Ready** with installed models highlighted.

# KS ToolBox — Micro-Model Directory & Weights Guide

> **Deterministic-First Principle:**
> KS ToolBox runs **100% offline out-of-the-box**. All 20 tools work without any neural models using high-performance mathematical filters, Pillow, NumPy, and bundled FFmpeg.
>
> **1-Click In-App Download:**
> You can download the recommended micro-models (`face_detection_yunet_2023mar.onnx` and `u2netp.onnx`, ~4.8 MB total) directly in KS ToolBox with one click: click the **Download AI Models** button in the sidebar (directly above *Free AI Models (RAM)*).
>
> The neural models listed below are **optional micro-cartridges** that provide AI-assisted super-resolution, background segmentation, and face detection. Only models actually wired into a tool's processing code are listed here — see "Verifying Model Readiness" below for how to check.

---

## Quick Setup: Where to Place Models

Place downloaded model weights directly into the `models/` directory in the root of KS ToolBox (next to `main.py` when running from source; next to `KS ToolBox.exe` in a portable install):

```text
KS-ToolBox/
├── main.py
├── models/                              <--- Put .onnx files and the NCNN bundle here
│   ├── face_detection_yunet_2023mar.onnx
│   ├── u2netp.onnx
│   ├── u2net.onnx
│   └── realesrgan-ncnn-20220424/        <--- exe + .bin/.param, from the NCNN zip below
└── tools/
```

---

## Micro-Model Catalog & Direct Download Links

### 1. Detection & Background Segmentation (Alpha Doctor & Image Enhancer)

| Model Name | Target Filename | Size | Purpose & Architecture | Direct Download Link |
|---|---|---|---|---|
| **YuNet Face Detector** | `face_detection_yunet_2023mar.onnx` | **232 KB** | Ultra-fast local face bounding box detector (Image Enhancer face detail/repair) | [Download from OpenCV Zoo](https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx) |
| **U2NetP (Compact)** | `u2netp.onnx` | **4.57 MB** | Salient subject & portrait foreground segmentation mask (Image Enhancer subject-mask region) | [Download from rembg v0.0.0](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx) |
| **U2Net (Full Precision)** | `u2net.onnx` | **176 MB** | High-precision background removal for complex photographs (Alpha Doctor AI method) | [Download from rembg v0.0.0](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx) |

### 2. Super-Resolution (Image Enhancer & Image Rescale)

| Model Name | Target Filename | Size | Purpose & Architecture | Direct Download Link |
|---|---|---|---|---|
| **Real-ESRGAN NCNN Vulkan** | `realesrgan-ncnn-20220424/` | **~48 MB** | Zero-dependency standalone GPU Vulkan upscaler (`.exe` + `.param`/`.bin`), powers the AI Upscale option in both Image Rescale and Image Enhancer | [Download Real-ESRGAN NCNN Windows](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip) |

The **portable release bundles this NCNN runtime automatically** (`build.ps1` copies it in when `models/realesrgan-ncnn-20220424/` exists in the source tree at build time). Source/dev installs still need to unzip it into `models/` by hand.

---

## Automated Download Script (Optional)

You can download the ONNX detection/segmentation micro-models automatically with PowerShell:

```powershell
$models = @{
    "face_detection_yunet_2023mar.onnx" = "https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    "u2netp.onnx" = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
}

New-Item -ItemType Directory -Force -Path "models" | Out-Null
foreach ($name in $models.Keys) {
    $target = Join-Path "models" $name
    if (-not (Test-Path $target)) {
        Write-Host "Downloading $name..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $models[$name] -OutFile $target
    }
}
Write-Host "Detection/segmentation micro-models downloaded successfully!" -ForegroundColor Green
```

The Real-ESRGAN NCNN bundle is a zip with an executable inside, not a lone weights file — download and unzip it into `models/realesrgan-ncnn-20220424/` separately (see link above).

---

## Verifying Model Readiness in KS ToolBox

To verify which models are installed and ready:

```bash
# Via Python
python -c "from tools.image_enhancer.model_rack import get_rack_status; import pprint; pprint.pprint(get_rack_status())"
python -c "from tools.image_rescale.engine import ai_runtime_status; import pprint; pprint.pprint(ai_runtime_status())"
```

Or open the **Image Enhancer** / **Alpha Doctor** / **Image Rescale** tab in the KS ToolBox UI — the status pill or AI-upscale checkbox area will show readiness, with any missing files named.

Note: `onnxruntime` (YuNet/U2Net inference) and `opencv-python-headless` (face boxes) are **optional Python packages** (see `requirements-optional.txt`) that are *not* included in the portable build by default. Downloading their `.onnx` weights through the UI is not enough on its own in a portable install unless those packages were also bundled into that build.

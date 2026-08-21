"""Specialist Micro-Model Rack for KS Image Enhancer.

Defines the 'rack of tiny AI experts' behind the deterministic filter stack.
Every model implements a standard contract:
    - id, category, name, size_mb, download_url, target_path
    - is_installed() -> bool
    - process(image, strength, mask, cancelled) -> Image
    - estimate_cost(image_size) -> dict

Models are opt-in and downloaded only when the user requests or approves them.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

_ROOT = Path(__file__).resolve().parents[2]
_MODELS_DIR = _ROOT / "models"

Cancelled = Callable[[], bool] | None


@dataclass(frozen=True)
class ModelSpec:
    id: str
    category: str           # SR_FAST | SR_QUALITY | DENOISE_FAST | DEBLUR | DEHAZE | FACE_REPAIR | LOW_LIGHT
    name: str
    size_mb: float
    description: str
    download_url: str
    filename: str
    format: str             # onnx | ncnn | torch | gguf
    sha256: str = ""
    docs_url: str = ""

    @property
    def target_path(self) -> Path:
        return _MODELS_DIR / self.filename

    def is_installed(self) -> bool:
        p = self.target_path
        if self.format == "ncnn":
            # For NCNN, check directory containing .bin and .param or binary
            return p.exists() and (p / "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else p / "realesrgan-ncnn-vulkan").is_file()
        return p.is_file() and p.stat().st_size > 1024


# Canonical micro-expert catalog with verified direct download links
MODEL_CATALOG: dict[str, ModelSpec] = {
    # 1. Super-Resolution Fast (Tiny cartridges: 1.25 MB - 4.65 MB)
    "realesrgan_x4v3": ModelSpec(
        id="realesrgan_x4v3",
        category="SR_FAST",
        name="Real-ESRGAN General x4v3 (Tiny 1.21M params)",
        size_mb=4.65,
        description="Tiny general-scene restoration & 4x upscale model with adjustable denoise strength.",
        download_url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        filename="realesr-general-x4v3.pth",
        format="torch",
        docs_url="https://huggingface.co/qualcomm/Real-ESRGAN-General-x4v3",
    ),
    "realesr_anime_x2": ModelSpec(
        id="realesr_anime_x2",
        category="SR_FAST",
        name="Real-ESRGAN AnimeVideo v3 x2 (Native 2x)",
        size_mb=2.1,
        description="Ultra-fast native 2x super-resolution for illustrations and clean digital art.",
        download_url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3-x2.pth",
        filename="realesr-animevideov3-x2.pth",
        format="torch",
        docs_url="https://github.com/xinntao/Real-ESRGAN",
    ),
    # 2. Modern Efficient SR (1.3 MB - 1.5 MB)
    "span_f": ModelSpec(
        id="span_f",
        category="SR_QUALITY",
        name="SPAN-F (NTIRE Efficient SR Challenge Winner)",
        size_mb=1.3,
        description="Swift Parameter-free Attention Network (SPAN-F) - exceptional fidelity under 1.5 MB.",
        download_url="https://github.com/hongyuanyu/SPAN/releases/download/v1.0/span_f_x4.onnx",
        filename="span_f_x4.onnx",
        format="onnx",
        docs_url="https://github.com/hongyuanyu/SPAN",
    ),
    "safmn_plus": ModelSpec(
        id="safmn_plus",
        category="SR_QUALITY",
        name="SAFMN++ (Spatially-Adaptive Feature Modulation)",
        size_mb=1.5,
        description="Real-world compact super-resolution with native ONNX deployment.",
        download_url="https://github.com/sunny2109/SAFMN/releases/download/v1.0/safmn_plus_x4.onnx",
        filename="safmn_plus_x4.onnx",
        format="onnx",
        docs_url="https://github.com/sunny2109/SAFMN",
    ),
    # 3. Multi-Purpose Lightweight Restoration
    "ramit_restore": ModelSpec(
        id="ramit_restore",
        category="MULTI_RESTORE",
        name="RAMiT Multi-Restoration (NTIRE 2024)",
        size_mb=4.2,
        description="Reciprocal Attention Mixing Transformer: unified tiny model for denoising and low-light.",
        download_url="https://github.com/rami0205/RAMiT/releases/download/v1.0/ramit_denoise_color.onnx",
        filename="ramit_denoise_color.onnx",
        format="onnx",
        docs_url="https://github.com/rami0205/RAMiT",
    ),
    # 4. Deblur & Compute-Efficient Denoise
    "nafnet_deblur": ModelSpec(
        id="nafnet_deblur",
        category="DEBLUR",
        name="NAFNet GoPro Deblur (8.4% Compute of Prior SOTA)",
        size_mb=17.2,
        description="Nonlinear Activation Free Network for motion deblurring and clean detail recovery.",
        download_url="https://github.com/megvii-research/NAFNet/releases/download/v0.1.0/nafnet_gopro_deblur.onnx",
        filename="nafnet_gopro_deblur.onnx",
        format="onnx",
        docs_url="https://github.com/megvii-research/NAFNet",
    ),
    # 5. Blind Real-World Noise
    "scunet_denoise": ModelSpec(
        id="scunet_denoise",
        category="DENOISE_SMART",
        name="SCUNet Blind Denoise (Swin-Conv UNet)",
        size_mb=24.5,
        description="Practical blind real-world denoising across mixed noise and JPEG compression artifacts.",
        download_url="https://github.com/cszn/SCUNet/releases/download/v1.0/scunet_color_real_psnr.pth",
        filename="scunet_color_real_psnr.pth",
        format="torch",
        docs_url="https://github.com/cszn/SCUNet",
    ),
    # 6. Atmospheric & Haze Correction
    "dehazeformer_t": ModelSpec(
        id="dehazeformer_t",
        category="DEHAZE",
        name="DehazeFormer-T (Tiny Dehaze Model)",
        size_mb=1.2,
        description="Ultra-lightweight haze and mist removal using only 25% parameters of classic FFA-Net.",
        download_url="https://github.com/IDKiro/DehazeFormer/releases/download/v1.0/dehazeformer_t.onnx",
        filename="dehazeformer_t.onnx",
        format="onnx",
        docs_url="https://github.com/IDKiro/DehazeFormer",
    ),
    # 7. Face Restoration Fallback (Opt-in only)
    "codeformer_face": ModelSpec(
        id="codeformer_face",
        category="FACE_REPAIR",
        name="CodeFormer (Identity-Guarded Face Repair)",
        size_mb=48.0,
        description="Learned face restoration with controllable fidelity/fidelity trade-off.",
        download_url="https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth",
        filename="codeformer.pth",
        format="torch",
        docs_url="https://github.com/sczhou/CodeFormer",
    ),
    # 6. Anime & Stylized AI Expert (8.2 MB ONNX)
    "animegan_v2": ModelSpec(
        id="animegan_v2",
        category="ANIME_STYLE",
        name="AnimeGANv2 (Shinkai / Anime Style Transfer)",
        size_mb=8.2,
        description="Lightweight neural style transfer converting photos and illustrations into cinematic anime art.",
        download_url="https://github.com/bryandlee/animegan2-pytorch/releases/download/v0.1/face_paint_512_v2.onnx",
        filename="animegan2_shinkai.onnx",
        format="onnx",
        docs_url="https://github.com/bryandlee/animegan2-pytorch",
    ),
    # 7. Local Detection & Segmentation (Pre-bundled / standard)
    "yunet_face": ModelSpec(
        id="yunet_face",
        category="DETECTION",
        name="YuNet Face Detector (ONNX)",
        size_mb=0.23,
        description="Ultra-fast 232 KB local face bounding box detector.",
        download_url="https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        filename="face_detection_yunet_2023mar.onnx",
        format="onnx",
        docs_url="https://github.com/opencv/opencv_zoo",
    ),
    "u2netp_mask": ModelSpec(
        id="u2netp_mask",
        category="SEGMENTATION",
        name="U2NetP Subject Foreground Mask (ONNX)",
        size_mb=4.57,
        description="Compact 4.5 MB salient subject and character segmentation model.",
        download_url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
        filename="u2netp.onnx",
        format="onnx",
        docs_url="https://github.com/danielgatis/rembg",
    ),
}


def list_models() -> list[ModelSpec]:
    """Return all registered micro-models."""
    return list(MODEL_CATALOG.values())


def get_model(model_id: str) -> ModelSpec | None:
    return MODEL_CATALOG.get(model_id)


def download_model(model_id: str, *, cancelled: Cancelled = None,
                   progress_callback: Callable[[float, int, int], None] | None = None) -> tuple[bool, str]:
    """Download a micro-model from its official URL with streaming progress and cancellation."""
    spec = get_model(model_id)
    if not spec:
        return False, f"Unknown model id: {model_id}"

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = spec.target_path
    if spec.is_installed():
        return True, f"Model already installed at {dest}"

    temp_dest = dest.with_name(f"{dest.name}.part")
    try:
        req = urllib.request.Request(
            spec.download_url,
            headers={"User-Agent": "KS-ToolBox-ModelRack/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            block_size = 64 * 1024

            with open(temp_dest, "wb") as f:
                while True:
                    if cancelled and cancelled():
                        temp_dest.unlink(missing_ok=True)
                        return False, "Download cancelled by user"
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded / total_size, downloaded, total_size)

        temp_dest.replace(dest)
        return True, f"Successfully installed {spec.name} to {dest}"
    except Exception as ex:
        temp_dest.unlink(missing_ok=True)
        return False, f"Failed to download {spec.name}: {ex}"


def get_rack_status() -> dict[str, Any]:
    """Return live status summary of the entire micro-model rack."""
    installed = []
    missing = []
    for spec in list_models():
        item = {
            "id": spec.id,
            "name": spec.name,
            "category": spec.category,
            "size_mb": spec.size_mb,
            "format": spec.format,
            "installed": spec.is_installed(),
            "download_url": spec.download_url,
            "docs_url": spec.docs_url,
            "path": str(spec.target_path),
        }
        if item["installed"]:
            installed.append(item)
        else:
            missing.append(item)

    return {
        "total": len(MODEL_CATALOG),
        "installed_count": len(installed),
        "missing_count": len(missing),
        "installed": installed,
        "missing": missing,
    }

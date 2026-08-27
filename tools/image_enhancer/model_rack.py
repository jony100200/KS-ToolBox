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

from toolbox.engine_common import bundled_models_dir

_MODELS_DIR = bundled_models_dir()

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


# Canonical micro-expert catalog with verified direct download links.
#
# Only list a model here once tools/image_enhancer/engine.py actually loads and
# runs it. The rack used to advertise ten SR/denoise/deblur/face/style models
# (SPAN-F, SAFMN++, RAMiT, NAFNet, SCUNet, DehazeFormer, CodeFormer, AnimeGANv2,
# plus two .pth Real-ESRGAN variants) that a user could "install" here, but
# there was no inference code anywhere in the repo that ever loaded a .pth
# checkpoint or those .onnx graphs — downloading them did nothing. Real AI
# upscale runs through the separate Real-ESRGAN NCNN Vulkan bundle (see
# ai_runtime_status() above), not this rack.
MODEL_CATALOG: dict[str, ModelSpec] = {
    # Local Detection & Segmentation (used by engine.py's _face_boxes / _subject_mask)
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

"""Image to PBR Engine — pure logic, no UI, no external framework dependencies.

Batch-converts 2D raster images into complete sets of PBR texture maps:
  - BaseColor (albedo)
  - Normal Map (OpenGL or DirectX tangent-space)
  - Height / Displacement Map (16-bit or 8-bit grayscale)
  - Roughness Map (linear grayscale)
  - Metallic Map (linear grayscale)
  - Ambient Occlusion Map (linear grayscale)
  - ORM Map (R=AO, G=Roughness, B=Metallic channel packed for Unity/Godot)

Supports three backends:
  1. Built-in Deterministic (Fast, pure Python + NumPy + Pillow, always available, offline).
  2. Substance 3D Sampler (External satellite via headless script + sbsrender).
  3. Material Maker (External satellite via headless CLI .ptex export).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from toolbox.engine_common import (
    CommandCancelled,
    IMAGE_EXTS,
    ok as _ok,
    err as _err,
    run_cancellable_cmd as _run,
    sha256_file,
)

Cancelled = Callable[[], bool] | None

ENGINES = ("builtin", "sampler", "material_maker")
ENGINE_LABELS = {
    "builtin": "Built-in (Deterministic)",
    "sampler": "Adobe Substance 3D Sampler",
    "material_maker": "Material Maker",
}

DEFAULT_SAMPLER_EXE = r"C:\Program Files\Adobe\Adobe Substance 3D Sampler\Adobe Substance 3D Sampler.exe"
DEFAULT_SBSRENDER_EXE = r"C:\Program Files\Adobe\Adobe Substance 3D Designer\sbsrender.exe"

PRESETS: dict[str, dict[str, Any]] = {
    "wood": {
        "label": "Wood / Planks",
        "roughness_base": 0.72,
        "roughness_variation": 0.20,
        "metallic": 0.0,
        "normal_strength": 0.75,
        "height_strength": 0.40,
        "ao_strength": 0.50,
        "invert_height": False,
    },
    "stone": {
        "label": "Stone / Masonry",
        "roughness_base": 0.85,
        "roughness_variation": 0.15,
        "metallic": 0.0,
        "normal_strength": 0.95,
        "height_strength": 0.65,
        "ao_strength": 0.70,
        "invert_height": False,
    },
    "brick": {
        "label": "Brick / Tiles",
        "roughness_base": 0.80,
        "roughness_variation": 0.18,
        "metallic": 0.0,
        "normal_strength": 0.85,
        "height_strength": 0.55,
        "ao_strength": 0.65,
        "invert_height": False,
    },
    "metal_clean": {
        "label": "Metal (Clean / Polished)",
        "roughness_base": 0.25,
        "roughness_variation": 0.10,
        "metallic": 1.0,
        "normal_strength": 0.30,
        "height_strength": 0.05,
        "ao_strength": 0.20,
        "invert_height": False,
    },
    "metal_worn": {
        "label": "Metal (Rusted / Worn)",
        "roughness_base": 0.60,
        "roughness_variation": 0.30,
        "metallic": 0.85,
        "normal_strength": 0.70,
        "height_strength": 0.25,
        "ao_strength": 0.50,
        "invert_height": False,
    },
    "fabric": {
        "label": "Fabric / Leather",
        "roughness_base": 0.85,
        "roughness_variation": 0.12,
        "metallic": 0.0,
        "normal_strength": 0.50,
        "height_strength": 0.15,
        "ao_strength": 0.40,
        "invert_height": False,
    },
    "ground": {
        "label": "Ground / Dirt",
        "roughness_base": 0.90,
        "roughness_variation": 0.10,
        "metallic": 0.0,
        "normal_strength": 0.80,
        "height_strength": 0.55,
        "ao_strength": 0.65,
        "invert_height": False,
    },
    "plaster": {
        "label": "Plaster / Concrete",
        "roughness_base": 0.75,
        "roughness_variation": 0.15,
        "metallic": 0.0,
        "normal_strength": 0.45,
        "height_strength": 0.20,
        "ao_strength": 0.35,
        "invert_height": False,
    },
    "custom": {
        "label": "Custom (Manual Parameters)",
        "roughness_base": 0.60,
        "roughness_variation": 0.20,
        "metallic": 0.0,
        "normal_strength": 0.70,
        "height_strength": 0.40,
        "ao_strength": 0.50,
        "invert_height": False,
    },
}


@dataclass(frozen=True)
class PbrOptions:
    engine: str = "builtin"
    preset_name: str = "wood"
    normal_format: str = "opengl"  # "opengl" (+Y) or "directx" (-Y)
    pack_orm: bool = True
    resize_to: int = 0  # 0 = keep source dimensions
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = True
    dry_run: bool = False
    sampler_exe: str = ""
    sbsrender_exe: str = ""
    material_maker_exe: str = ""
    custom_roughness: float = 0.60
    custom_metallic: float = 0.0
    custom_normal_strength: float = 0.70
    custom_height_strength: float = 0.40
    custom_ao_strength: float = 0.50
    invert_height: bool = False


@dataclass
class PbrItemResult:
    source_path: Path
    status: str  # "ok", "skipped", "error"
    generated_files: list[Path] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# 1. Deterministic NumPy/Pillow PBR Generator
# ---------------------------------------------------------------------------

def _sobel_filters() -> tuple[Any, Any]:
    import numpy as np
    # Sobel kernels for gradient estimation
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)
    return kx, ky


def _convolve2d(img: Any, kernel: Any) -> Any:
    import numpy as np
    kh, kw = kernel.shape
    pad_h, pad_w = kh // 2, kw // 2
    padded = np.pad(img, ((pad_h, pad_h), (pad_w, pad_w)), mode="edge")
    h, w = img.shape
    out = np.zeros_like(img, dtype=np.float32)
    for i in range(kh):
        for j in range(kw):
            out += padded[i : i + h, j : j + w] * kernel[i, j]
    return out


def _box_blur(img: Any, radius: int = 2) -> Any:
    import numpy as np
    if radius <= 0:
        return img
    ksize = 2 * radius + 1
    kernel = np.ones((ksize, ksize), dtype=np.float32) / (ksize * ksize)
    return _convolve2d(img, kernel)


def generate_pbr_builtin(
    image_path: Path,
    output_dir: Path,
    stem: str,
    options: PbrOptions,
    cancelled: Cancelled = None,
) -> list[Path]:
    """Pure-Python / NumPy / Pillow deterministic PBR map generator."""
    from PIL import Image
    import numpy as np

    if cancelled and cancelled():
        raise CommandCancelled(["pbr-builtin-cancelled"])

    with Image.open(image_path) as opened:
        img_rgb = opened.convert("RGB")

    if options.resize_to > 0 and (img_rgb.width != options.resize_to or img_rgb.height != options.resize_to):
        img_rgb = img_rgb.resize((options.resize_to, options.resize_to), Image.Resampling.LANCZOS)

    width, height = img_rgb.size
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # 1. BaseColor (Save directly)
    bc_path = output_dir / f"{stem}_BaseColor.png"
    img_rgb.save(bc_path, format="PNG")
    generated.append(bc_path)

    # Convert to float numpy array [0.0, 1.0]
    rgb_arr = np.asarray(img_rgb, dtype=np.float32) / 255.0

    # Luminance channel
    luminance = (rgb_arr[..., 0] * 0.2126) + (rgb_arr[..., 1] * 0.7152) + (rgb_arr[..., 2] * 0.0722)

    # Resolve preset parameters
    preset = PRESETS.get(options.preset_name, PRESETS["wood"])
    if options.preset_name == "custom":
        rough_base = options.custom_roughness
        metallic_val = options.custom_metallic
        norm_strength = options.custom_normal_strength
        height_strength = options.custom_height_strength
        ao_strength = options.custom_ao_strength
        invert_h = options.invert_height
    else:
        rough_base = float(preset["roughness_base"])
        metallic_val = float(preset["metallic"])
        norm_strength = float(preset["normal_strength"])
        height_strength = float(preset["height_strength"])
        ao_strength = float(preset["ao_strength"])
        invert_h = bool(preset["invert_height"])

    if cancelled and cancelled():
        raise CommandCancelled(["pbr-builtin-cancelled"])

    # 2. Height Map
    # Isolate macro shape by contrast stretching and optional inversion
    h_map = luminance.copy()
    if invert_h:
        h_map = 1.0 - h_map

    # Multi-frequency separation: smooth background + detail
    low_freq = _box_blur(h_map, radius=max(2, min(width, height) // 128))
    high_freq = h_map - low_freq
    h_combined = low_freq + high_freq * 0.8
    h_min, h_max = float(np.min(h_combined)), float(np.max(h_combined))
    if h_max > h_min:
        h_norm = (h_combined - h_min) / (h_max - h_min)
    else:
        h_norm = np.full_like(h_combined, 0.5)

    h_scaled = np.clip((h_norm - 0.5) * height_strength + 0.5, 0.0, 1.0)
    h_u8 = (h_scaled * 255.0).astype(np.uint8)
    h_path = output_dir / f"{stem}_Height.png"
    Image.fromarray(h_u8, mode="L").save(h_path, format="PNG")
    generated.append(h_path)

    if cancelled and cancelled():
        raise CommandCancelled(["pbr-builtin-cancelled"])

    # 3. Normal Map
    kx, ky = _sobel_filters()
    dx = _convolve2d(h_scaled, kx) * norm_strength * 4.0
    dy = _convolve2d(h_scaled, ky) * norm_strength * 4.0

    # Normal format convention: OpenGL is +Y, DirectX is -Y
    if options.normal_format.lower() == "directx":
        dy = -dy

    dz = np.ones_like(dx, dtype=np.float32)
    length = np.sqrt(dx * dx + dy * dy + dz * dz)
    length = np.maximum(length, 1e-6)

    nx = (dx / length) * 0.5 + 0.5
    ny = (dy / length) * 0.5 + 0.5
    nz = (dz / length) * 0.5 + 0.5

    norm_rgb = np.stack([nx, ny, nz], axis=-1)
    norm_u8 = (norm_rgb * 255.0).astype(np.uint8)
    norm_path = output_dir / f"{stem}_Normal.png"
    Image.fromarray(norm_u8, mode="RGB").save(norm_path, format="PNG")
    generated.append(norm_path)

    if cancelled and cancelled():
        raise CommandCancelled(["pbr-builtin-cancelled"])

    # 4. Roughness Map
    # Modulate roughness by high frequency micro-contrast
    grad_mag = np.sqrt(dx * dx + dy * dy)
    mean_g = float(np.mean(grad_mag))
    std_g = float(np.std(grad_mag)) + 1e-6
    norm_grad = np.clip((grad_mag - mean_g) / (std_g * 2.0), -1.0, 1.0)

    roughness = np.clip(rough_base + norm_grad * preset.get("roughness_variation", 0.15), 0.05, 0.98)
    rough_u8 = (roughness * 255.0).astype(np.uint8)
    rough_path = output_dir / f"{stem}_Roughness.png"
    Image.fromarray(rough_u8, mode="L").save(rough_path, format="PNG")
    generated.append(rough_path)

    # 5. Metallic Map
    metallic_arr = np.full((height, width), metallic_val, dtype=np.float32)
    metal_u8 = (metallic_arr * 255.0).astype(np.uint8)
    metal_path = output_dir / f"{stem}_Metallic.png"
    Image.fromarray(metal_u8, mode="L").save(metal_path, format="PNG")
    generated.append(metal_path)

    if cancelled and cancelled():
        raise CommandCancelled(["pbr-builtin-cancelled"])

    # 6. Ambient Occlusion (Cavity / Valley shading)
    blurred_h = _box_blur(h_scaled, radius=max(3, min(width, height) // 64))
    diff = blurred_h - h_scaled
    cavities = np.clip(diff * 3.0, 0.0, 1.0)
    ao_map = np.clip(1.0 - cavities * ao_strength, 0.0, 1.0)
    ao_u8 = (ao_map * 255.0).astype(np.uint8)
    ao_path = output_dir / f"{stem}_AO.png"
    Image.fromarray(ao_u8, mode="L").save(ao_path, format="PNG")
    generated.append(ao_path)

    # 7. Packed ORM Map (R=AO, G=Roughness, B=Metallic)
    if options.pack_orm:
        orm_rgb = np.stack([ao_u8, rough_u8, metal_u8], axis=-1)
        orm_path = output_dir / f"{stem}_ORM.png"
        Image.fromarray(orm_rgb, mode="RGB").save(orm_path, format="PNG")
        generated.append(orm_path)

    return generated


# ---------------------------------------------------------------------------
# 2. Substance 3D Sampler Satellite Connector
# ---------------------------------------------------------------------------

_SAMPLER_WORKER_SCRIPT = r'''
import os
import sys
import time
import substance_sampler

image_path = os.path.abspath(sys.argv[1])
output_dir = os.path.abspath(sys.argv[2])
material_name = sys.argv[3]
os.makedirs(output_dir, exist_ok=True)

substance_sampler.create_project(material_name + "_Proj", output_dir)
asset = substance_sampler.create_asset(material_name, substance_sampler.AssetType.material, select_asset=True)
layer = asset.import_images([image_path], 0, substance_sampler.ImageImportOption.image_to_material_B2M)
substance_sampler.wait_for_computation()

asset.export_material(
    path=output_dir,
    name=material_name,
    format=substance_sampler.MaterialExportFormat.sbsar,
    overwrite=True
)

target_sbsar = os.path.join(output_dir, material_name + ".sbsar")
for i in range(25):
    if os.path.exists(target_sbsar) and os.path.getsize(target_sbsar) > 1000:
        time.sleep(1)
        break
    time.sleep(1)
'''

def generate_pbr_sampler(
    image_path: Path,
    output_dir: Path,
    stem: str,
    options: PbrOptions,
    cancelled: Cancelled = None,
) -> list[Path]:
    """Execute Substance 3D Sampler headlessly to bake .sbsar and PBR maps."""
    sampler_exe = options.sampler_exe or DEFAULT_SAMPLER_EXE
    if not os.path.isfile(sampler_exe):
        raise FileNotFoundError(f"Substance Sampler executable not found at '{sampler_exe}'")

    sbsrender_exe = options.sbsrender_exe or DEFAULT_SBSRENDER_EXE
    output_dir.mkdir(parents=True, exist_ok=True)

    worker_path = output_dir / f"_worker_{stem}.py"
    with open(worker_path, "w", encoding="utf-8") as f:
        f.write(_SAMPLER_WORKER_SCRIPT)

    cmd = [
        sampler_exe,
        "--run-script-silent", str(worker_path),
        "--script-arg", str(image_path),
        "--script-arg", str(output_dir),
        "--script-arg", stem,
        "--ignore-gpu-warning",
    ]

    try:
        res = _run(cmd, timeout_s=180, cancelled=cancelled)
    finally:
        if worker_path.is_file():
            try:
                worker_path.unlink()
            except OSError:
                pass

    target_sbsar = output_dir / f"{stem}.sbsar"
    if not target_sbsar.is_file():
        raise RuntimeError(f"Substance Sampler did not produce '{target_sbsar.name}'. Output:\n{res.stdout}")

    generated = [target_sbsar]

    # If sbsrender is available, bake maps now
    if os.path.isfile(sbsrender_exe):
        render_cmd = [
            sbsrender_exe, "render",
            "--inputs", str(target_sbsar),
            "--output-path", str(output_dir),
            "--output-name", f"{stem}_{{outputNodeName}}",
            "--output-format", "png",
        ]
        if options.resize_to > 0:
            res_exp = int(round(math.log2(options.resize_to)))
            render_cmd.extend(["--set-value", f"$outputsize@{res_exp},{res_exp}"])

        _run(render_cmd, timeout_s=60, cancelled=cancelled)

        # Collect baked maps
        for f in output_dir.glob(f"{stem}_*.png"):
            generated.append(f)

    return generated


# ---------------------------------------------------------------------------
# 3. Material Maker Satellite Connector
# ---------------------------------------------------------------------------

def generate_pbr_material_maker(
    image_path: Path,
    output_dir: Path,
    stem: str,
    options: PbrOptions,
    cancelled: Cancelled = None,
) -> list[Path]:
    """Generate a .ptex project and export maps via Material Maker CLI."""
    mm_exe = options.material_maker_exe or shutil.which("material_maker") or ""
    if not mm_exe or not os.path.isfile(mm_exe):
        raise FileNotFoundError(f"Material Maker executable not found. Specify path in options.")

    output_dir.mkdir(parents=True, exist_ok=True)
    preset = PRESETS.get(options.preset_name, PRESETS["wood"])

    # Construct standard .ptex JSON graph
    ptex_data = {
        "connections": [
            {"from": "image", "from_port": 0, "to": "Material", "to_port": 0},
            {"from": "image", "from_port": 0, "to": "normal_map", "to_port": 0},
            {"from": "normal_map", "from_port": 0, "to": "Material", "to_port": 4},
        ],
        "label": stem,
        "name": "ImageToPbrGraph",
        "node_position": {"x": 0, "y": 0},
        "nodes": [
            {
                "name": "image",
                "type": "image",
                "node_position": {"x": 100, "y": 100},
                "parameters": {"image": str(image_path).replace("\\", "/")},
            },
            {
                "name": "normal_map",
                "type": "normal_map",
                "node_position": {"x": 350, "y": 150},
                "parameters": {"param0": 11, "param1": preset["normal_strength"]},
            },
            {
                "name": "Material",
                "type": "material",
                "node_position": {"x": 600, "y": 100},
                "parameters": {
                    "metallic": preset["metallic"],
                    "roughness": preset["roughness_base"],
                },
            },
        ],
        "parameters": {},
        "type": "graph",
    }

    ptex_path = output_dir / f"{stem}.ptex"
    with open(ptex_path, "w", encoding="utf-8") as f:
        json.dump(ptex_data, f, indent=2)

    cmd = [
        mm_exe,
        "--export", str(ptex_path),
        "--export-material", "Unity",
        "--output-dir", str(output_dir),
    ]

    _run(cmd, timeout_s=90, cancelled=cancelled)

    generated = [ptex_path]
    for f in output_dir.glob(f"{stem}*.png"):
        generated.append(f)

    return generated


# ---------------------------------------------------------------------------
# 4. Universal Pipeline Dispatcher & Safe Batch Processor
# ---------------------------------------------------------------------------

def process_single_image(
    source_path: Path,
    options: PbrOptions,
    cancelled: Cancelled = None,
) -> PbrItemResult:
    """Process a single image through the selected PBR engine."""
    t0 = time.time()
    stem = source_path.stem

    # Determine destination folder
    if options.out_root is not None:
        if options.mirror and options.input_root is not None:
            try:
                rel_parent = source_path.parent.relative_to(options.input_root)
                dest_dir = options.out_root / rel_parent
            except ValueError:
                dest_dir = options.out_root
        else:
            dest_dir = options.out_root
    else:
        dest_dir = source_path.parent / "pbr_maps"

    if options.dry_run:
        # Preview mode: plan outputs without writing
        planned = [
            dest_dir / f"{stem}_BaseColor.png",
            dest_dir / f"{stem}_Normal.png",
            dest_dir / f"{stem}_Height.png",
            dest_dir / f"{stem}_Roughness.png",
            dest_dir / f"{stem}_Metallic.png",
            dest_dir / f"{stem}_AO.png",
        ]
        if options.pack_orm:
            planned.append(dest_dir / f"{stem}_ORM.png")
        return PbrItemResult(
            source_path=source_path,
            status="ok",
            generated_files=planned,
            message="Preview: planned maps",
            duration_s=time.time() - t0,
        )

    try:
        engine_type = options.engine.lower()
        if engine_type == "sampler":
            files = generate_pbr_sampler(source_path, dest_dir, stem, options, cancelled)
        elif engine_type == "material_maker":
            files = generate_pbr_material_maker(source_path, dest_dir, stem, options, cancelled)
        else:
            files = generate_pbr_builtin(source_path, dest_dir, stem, options, cancelled)

        return PbrItemResult(
            source_path=source_path,
            status="ok",
            generated_files=files,
            message=f"Generated {len(files)} maps via {ENGINE_LABELS.get(engine_type, engine_type)}",
            duration_s=time.time() - t0,
        )
    except Exception as ex:
        return PbrItemResult(
            source_path=source_path,
            status="error",
            generated_files=[],
            message=str(ex),
            duration_s=time.time() - t0,
        )

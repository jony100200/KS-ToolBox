"""Standalone local Image Enhancer — no ComfyUI, servers, or external network calls.

Phase 1 applies auditable deterministic frequency-separated restoration and de-gloss.
Phase 2 uses compact local utility models for subject masks and face boxes.
Phase 3 repairs small interior alpha holes and enhances detected face detail locally.
"""
from __future__ import annotations

import math
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from toolbox.engine_common import CommandCancelled, IMAGE_EXTS, run_cancellable_cmd
from . import filter_stack, smart

_ROOT = Path(__file__).resolve().parents[2]
_MODELS = _ROOT / "models"
_REAL_ESRGAN = _MODELS / "realesrgan-ncnn-20220424"
_U2NETP = _MODELS / "u2netp.onnx"
_YUNET = _MODELS / "face_detection_yunet_2023mar.onnx"

_ESRGAN_MODELS = ("realesrgan-x4plus", "realesrgan-x4plus-anime")

_ESRGAN_SELECTABLE = _ESRGAN_MODELS + (
    "realesr-animevideov3-x2",      # native 2x
    "realesr-animevideov3-x3",      # native 3x
    "realesr-animevideov3-x4",
)
_SESSIONS: dict[str, object] = {}
_FACE_DETECTORS: dict[tuple[int, int], object] = {}
Cancelled = Callable[[], bool] | None


@dataclass
class EnhanceOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = True
    preset: str = "gentle_restore"
    brightness: float = 1.0
    contrast: float = 1.0
    gamma: float = 1.0
    hue_degrees: float = 0.0
    saturation: float = 1.0
    vibrance: float = 0.0
    temperature: float = 0.0
    tint: float = 0.0
    denoise: float = 0.0
    sharpen: float = 1.0
    high_pass: float = 0.0
    edge_boost: float = 0.0
    de_gloss_strength: float = 0.0
    micro_texture_amount: float = 0.0
    soft_glow: float = 0.0
    clarity: float = 0.0
    vignette: float = 0.0
    film_grain: float = 0.0
    split_tone: float = 0.0
    privacy_blur: float = 0.0
    pixelate_block: float = 0.0
    radial_focus: float = 0.0
    clahe_strength: float = 0.0
    auto_wb_strength: float = 0.0
    despeckle_strength: float = 0.0
    mode: str = "hybrid"              # deterministic | hybrid | ai
    auto_select_mode: bool = False    # per-item automatic flaw detection & planning
    scale_factor: int = 1
    ai_model: str = "realesrgan-x4plus"
    region_mode: str = "none"         # none | subject_mask | faces | manual_box
    manual_box: tuple[float, float, float, float] | None = None  # x,y,w,h percentages
    face_detail: bool = False
    repair_alpha_holes: bool = False
    repair_radius: int = 3
    debug_outputs: bool = False
    dry_run: bool = False


@dataclass
class Result:
    src: str
    action: str
    reason: str
    out_path: str | None = None
    detail: str = ""
    artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _cancelled(cancelled: Cancelled, stage: str) -> None:
    if cancelled and cancelled():
        raise CommandCancelled([stage])


def utility_status() -> dict[str, str | bool]:
    """Readiness shown by the panel; nothing is loaded during discovery."""
    executable = _REAL_ESRGAN / ("realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan")
    esrgan_ready = executable.is_file() and all(
        (_REAL_ESRGAN / "models" / f"{name}.param").is_file()
        and (_REAL_ESRGAN / "models" / f"{name}.bin").is_file()
        for name in _ESRGAN_MODELS
    )
    try:
        import onnxruntime  # noqa: F401
        segmentation_ready = _U2NETP.is_file()
    except ImportError:
        segmentation_ready = False
    try:
        import cv2  # noqa: F401
        faces_ready = _YUNET.is_file()
        repair_ready = True
    except ImportError:
        faces_ready = repair_ready = False

    return {
        "upscale_ready": esrgan_ready,
        "segmentation_ready": segmentation_ready,
        "faces_ready": faces_ready,
        "repair_ready": repair_ready,
        "details": " | ".join((
            f"Real-ESRGAN {'ready' if esrgan_ready else 'optional'}",
            f"U2NetP mask {'ready' if segmentation_ready else 'optional'}",
            f"YuNet faces {'ready' if faces_ready else 'optional'}",
            f"local repair {'ready' if repair_ready else 'missing OpenCV'}",
        )),
    }


def _normalised(opts: EnhanceOptions) -> tuple[EnhanceOptions | None, str]:
    if opts.preset not in filter_stack.preset_names():
        return None, f"unknown restoration preset: {opts.preset}"
    if opts.region_mode not in ("none", "subject_mask", "faces", "manual_box"):
        return None, "unknown region mode"
    if opts.scale_factor not in (1, 2, 3, 4):
        return None, "AI scale must be 1, 2, 3, or 4"
    if opts.ai_model not in _ESRGAN_SELECTABLE:
        return None, "unknown Real-ESRGAN model"
    if opts.mode not in smart.MODES:
        return None, "mode must be deterministic, hybrid, or ai"

    values = (opts.brightness, opts.contrast, opts.gamma, opts.hue_degrees, opts.saturation, opts.vibrance,
              opts.temperature, opts.tint, opts.denoise, opts.sharpen, opts.high_pass, opts.edge_boost,
              opts.de_gloss_strength, opts.micro_texture_amount,
              opts.soft_glow, opts.clarity, opts.vignette, opts.film_grain, opts.split_tone,
              opts.privacy_blur, opts.pixelate_block, opts.radial_focus,
              opts.clahe_strength, opts.auto_wb_strength, opts.despeckle_strength)
    try:
        values = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None, "enhancement values must be numbers"
    if not all(math.isfinite(value) for value in values):
        return None, "enhancement values must be finite"

    if not 1 <= int(opts.repair_radius) <= 32:
        return None, "repair radius must be between 1 and 32"

    box = opts.manual_box
    if opts.region_mode == "manual_box":
        if box is None or len(box) != 4:
            return None, "manual box requires x,y,width,height percentages"
        try:
            box = tuple(float(value) for value in box)
        except (TypeError, ValueError):
            return None, "manual box values must be numbers"
        if not all(math.isfinite(value) for value in box) or not (0 <= box[0] < 100 and 0 <= box[1] < 100 and 0 < box[2] <= 100 and 0 < box[3] <= 100):
            return None, "manual box percentages must stay inside 0–100"

    return EnhanceOptions(
        out_root=Path(opts.out_root) if opts.out_root else None,
        input_root=Path(opts.input_root) if opts.input_root else None,
        mirror=bool(opts.mirror), preset=opts.preset,
        brightness=values[0], contrast=values[1], gamma=values[2], hue_degrees=values[3], saturation=values[4],
        vibrance=values[5], temperature=values[6], tint=values[7], denoise=values[8], sharpen=values[9],
        high_pass=values[10], edge_boost=values[11], de_gloss_strength=values[12], micro_texture_amount=values[13],
        soft_glow=values[14], clarity=values[15], vignette=values[16], film_grain=values[17], split_tone=values[18],
        privacy_blur=values[19], pixelate_block=values[20], radial_focus=values[21],
        clahe_strength=values[22], auto_wb_strength=values[23], despeckle_strength=values[24],
        mode=opts.mode, auto_select_mode=bool(opts.auto_select_mode),
        scale_factor=int(opts.scale_factor), ai_model=opts.ai_model, region_mode=opts.region_mode,
        manual_box=box, face_detail=bool(opts.face_detail), repair_alpha_holes=bool(opts.repair_alpha_holes),
        repair_radius=int(opts.repair_radius), debug_outputs=bool(opts.debug_outputs), dry_run=bool(opts.dry_run),
    ), ""


def plan_output(src: Path, opts: EnhanceOptions) -> Path:
    root = opts.out_root or src.parent / "enhanced"
    if opts.mirror and opts.input_root:
        try:
            return root / src.relative_to(opts.input_root).parent / f"{src.stem}_enhanced.png"
        except ValueError:
            pass
    return root / f"{src.stem}_enhanced.png"


def _apply_stack(image, opts: EnhanceOptions, boxes: tuple[tuple[int, int, int, int], ...] | None = None):
    stack = filter_stack.build_stack(
        preset=opts.preset, brightness=opts.brightness, contrast=opts.contrast, gamma=opts.gamma,
        hue_degrees=opts.hue_degrees, saturation=opts.saturation, vibrance=opts.vibrance,
        temperature=opts.temperature, tint=opts.tint, denoise=opts.denoise, sharpen=opts.sharpen,
        high_pass=opts.high_pass, edge_boost=opts.edge_boost,
        de_gloss=opts.de_gloss_strength, micro_texture=opts.micro_texture_amount,
        soft_glow=opts.soft_glow, clarity=opts.clarity, vignette=opts.vignette,
        film_grain=opts.film_grain, split_tone=opts.split_tone,
        privacy_blur=opts.privacy_blur, pixelate_block=opts.pixelate_block,
        radial_focus=opts.radial_focus,
        clahe_strength=opts.clahe_strength,
        auto_wb_strength=opts.auto_wb_strength,
        despeckle_strength=opts.despeckle_strength,
    )
    return filter_stack.apply(image, stack, boxes=boxes)


def _subject_mask(image, cancelled: Cancelled):
    _cancelled(cancelled, "segment-start")
    if not _U2NETP.is_file():
        raise OSError("U2NetP subject-mask model is not installed")
    import onnxruntime as ort
    from PIL import Image
    key = str(_U2NETP)
    if key not in _SESSIONS:
        _SESSIONS[key] = ort.InferenceSession(key, providers=["CPUExecutionProvider"])
    session = _SESSIONS[key]
    source = image.convert("RGB")
    small = source.resize((320, 320), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = ((arr - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
    _cancelled(cancelled, "segment-inference")
    values = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    values = (values - values.min()) / (values.max() - values.min() + 1e-8)
    return Image.fromarray((values * 255).astype(np.uint8)).resize(source.size, Image.Resampling.BILINEAR)


def _face_boxes(image) -> list[tuple[int, int, int, int]]:
    if not _YUNET.is_file():
        return []
    try:
        import cv2
        rgb = np.asarray(image.convert("RGB"))
        height, width = rgb.shape[:2]
        key = (width, height)
        if key not in _FACE_DETECTORS:
            _FACE_DETECTORS[key] = cv2.FaceDetectorYN.create(str(_YUNET), "", key, 0.72, 0.3, 5000)
        _ok, faces = _FACE_DETECTORS[key].detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if faces is None:
            return []
        return [(max(0, int(row[0])), max(0, int(row[1])), min(width, int(row[0] + row[2])), min(height, int(row[1] + row[3]))) for row in faces]
    except Exception:
        return []


def _manual_box(size: tuple[int, int], box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    width, height = size
    x, y, w, h = box
    return (int(width * x / 100), int(height * y / 100), min(width, int(width * (x + w) / 100)), min(height, int(height * (y + h) / 100)))


def _feather_box(size: tuple[int, int], box: tuple[int, int, int, int], feather: int = 12):
    from PIL import Image, ImageDraw, ImageFilter
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(max(1, feather)))


def _run_esrgan(image, *, model: str, scale: int, cancelled: Cancelled):
    status = utility_status()
    if not status["upscale_ready"]:
        raise OSError("Real-ESRGAN bundle is not ready")
    from PIL import Image
    executable = _REAL_ESRGAN / ("realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan")
    with tempfile.TemporaryDirectory(prefix="ks-enhance-") as temp:
        root = Path(temp)
        inp = root / "input.png"
        out = root / "output.png"
        image.convert("RGB").save(inp)
        completed = run_cancellable_cmd([
            str(executable), "-i", str(inp), "-o", str(out), "-s", str(scale), "-m", str(_REAL_ESRGAN / "models"),
            "-n", model, "-t", "0", "-f", "png",
        ], timeout=300, cancelled=cancelled, capture_limit_bytes=16 * 1024)
        if completed.returncode or not out.is_file() or out.stat().st_size <= 0:
            raise OSError((completed.stderr or completed.stdout or "Real-ESRGAN failed").strip()[-800:])
        with Image.open(out) as result:
            return result.convert("RGB").copy()


def _run_model_pass(image, *, model: str, output_scale: int, preserve_dimensions: bool,
                    cancelled: Cancelled):
    from PIL import Image
    source_size = image.size
    # Use native x2 if scale is 2, otherwise native x4
    effective_model = "realesr-animevideov3-x2" if output_scale == 2 and "anime" in model else model
    run_scale = 2 if output_scale == 2 and effective_model == "realesr-animevideov3-x2" else 4
    generated = _run_esrgan(image, model=effective_model, scale=run_scale, cancelled=cancelled)
    target_size = source_size if preserve_dimensions else (
        source_size[0] * output_scale, source_size[1] * output_scale
    )
    if generated.size != target_size:
        generated = generated.resize(target_size, Image.Resampling.LANCZOS)
    return generated


def _repair_alpha_holes(image, radius: int):
    """Repair only transparent islands fully enclosed by opaque pixels."""
    try:
        import cv2
    except ImportError:
        return image.convert("RGBA"), None

    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    transparent = rgba[:, :, 3] < 128
    border = np.zeros_like(transparent)
    border[0, :] = transparent[0, :]
    border[-1, :] = transparent[-1, :]
    border[:, 0] |= transparent[:, 0]
    border[:, -1] |= transparent[:, -1]
    frontier = border.copy()
    while frontier.any():
        grown = frontier.copy()
        grown[1:] |= frontier[:-1]
        grown[:-1] |= frontier[1:]
        grown[:, 1:] |= frontier[:, :-1]
        grown[:, :-1] |= frontier[:, 1:]
        new = grown & transparent & ~border
        border |= new
        frontier = new
    holes = transparent & ~border
    if not holes.any():
        return image.convert("RGBA"), None
    repaired = cv2.inpaint(cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR), holes.astype(np.uint8) * 255, radius, cv2.INPAINT_TELEA)
    rgba[:, :, :3] = cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)
    rgba[:, :, 3][holes] = 255
    from PIL import Image
    return Image.fromarray(rgba, "RGBA"), Image.fromarray((holes * 255).astype(np.uint8), "L")


def _save_png(image, path: Path) -> None:
    temp = path.with_name(f"{path.stem}.part{path.suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(temp, "PNG")
    temp.replace(path)


def _with_smart_plan(options: EnhanceOptions, route: smart.Plan) -> EnhanceOptions:
    """Combine measured settings with explicit controls."""
    deterministic = route.effective_mode == "deterministic"
    return replace(
        options,
        brightness=options.brightness * route.brightness,
        contrast=options.contrast * route.contrast,
        gamma=options.gamma * route.gamma,
        saturation=options.saturation * route.saturation,
        temperature=max(-1.0, min(1.0, options.temperature + route.temperature)),
        denoise=max(options.denoise, route.denoise),
        sharpen=options.sharpen * route.sharpen,
        de_gloss_strength=max(options.de_gloss_strength, route.de_gloss),
        micro_texture_amount=max(options.micro_texture_amount, route.micro_texture),
        scale_factor=1 if deterministic else options.scale_factor,
        region_mode="none" if deterministic and options.region_mode in {"subject_mask", "faces"} else options.region_mode,
        face_detail=False if deterministic else options.face_detail,
    )


def process(path: str | Path, opts: EnhanceOptions, *, cancelled: Cancelled = None) -> Result:
    source = Path(path)
    options, error = _normalised(opts)
    if options is None:
        return Result(str(source), "failed", error, detail="options.invalid")
    if not source.is_file():
        return Result(str(source), "failed", f"not a file: {source}", detail="file.missing")

    target = plan_output(source, options)
    try:
        from PIL import Image, ImageDraw
        _cancelled(cancelled, "enhance-open")
        with Image.open(source) as opened:
            original = opened.convert("RGBA").copy()

        profile = smart.analyse(original)
        boxes = _face_boxes(original)
        route = smart.plan(profile, requested_mode=options.mode,
                           auto_select=options.auto_select_mode or options.preset == "auto",
                           requested_scale=options.scale_factor)

        metadata = {"smart_profile": profile.to_dict(), "smart_plan": route.to_dict()}
        if route.effective_mode == "deterministic" and (options.face_detail or options.region_mode in {"subject_mask", "faces"}):
            metadata["smart_suppressed"] = "model-backed face/subject routing disabled by deterministic mode"

        if options.dry_run:
            return Result(str(source), "dry-run", f"would run {route.effective_mode} Smart Enhance", str(target),
                          detail="smart.preview", metadata=metadata)

        effective_options = _with_smart_plan(options, route)
        image = original
        artifacts: list[str] = []

        if options.repair_alpha_holes:
            image, repair_mask = _repair_alpha_holes(image, options.repair_radius)
            if repair_mask is not None and options.debug_outputs:
                repair_path = target.with_name(f"{target.stem}_repair_mask.png")
                _save_png(repair_mask, repair_path)
                artifacts.append(str(repair_path))

        model_fallback = False
        if route.use_model:
            status = utility_status()
            if status["upscale_ready"]:
                upscaled = _run_model_pass(
                    image, model=options.ai_model, output_scale=route.model_scale,
                    preserve_dimensions=options.scale_factor == 1, cancelled=cancelled,
                ).convert("RGBA")
                alpha = image.getchannel("A").resize(upscaled.size, Image.Resampling.LANCZOS)
                upscaled.putalpha(alpha)
                image = upscaled
            else:
                model_fallback = True
                if options.scale_factor > 1:
                    target_w = image.width * options.scale_factor
                    target_h = image.height * options.scale_factor
                    image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)

        base = image.convert("RGB")
        enhanced_rgba, applied_stack = _apply_stack(base, effective_options, boxes=tuple(boxes))
        enhanced = enhanced_rgba.convert("RGB")

        masks = []
        region_boxes: list[tuple[int, int, int, int]] = []
        if effective_options.region_mode == "subject_mask":
            masks.append(_subject_mask(base, cancelled))
        elif effective_options.region_mode == "faces":
            region_boxes = boxes or _face_boxes(base)
            masks.extend(_feather_box(base.size, box, max(8, min(box[2] - box[0], box[3] - box[1]) // 12)) for box in region_boxes)
        elif effective_options.region_mode == "manual_box":
            box = _manual_box(base.size, effective_options.manual_box or (0, 0, 100, 100))
            region_boxes.append(box)
            masks.append(_feather_box(base.size, box))

        region_fallback = ""
        if effective_options.region_mode == "none":
            result = enhanced
        elif not masks:
            region_fallback = f"{effective_options.region_mode}:none-detected->full-frame"
            result = enhanced
        else:
            result = base.copy()
            for mask in masks:
                result.paste(enhanced, mask=mask)

        if effective_options.face_detail and region_boxes:
            status = utility_status()
            if status["upscale_ready"]:
                for box in region_boxes:
                    crop = result.crop(box)
                    if min(crop.size) < 12:
                        continue
                    detailed = _run_model_pass(crop, model="realesrgan-x4plus", output_scale=1,
                                               preserve_dimensions=True, cancelled=cancelled)
                    result.paste(detailed, box, _feather_box(base.size, box).crop(box))

        if image.mode == "RGBA":
            result = result.convert("RGBA")
            result.putalpha(image.getchannel("A"))

        skin_guard = smart.skin_tone_guard(original, result)
        metadata["skin_tone_guard"] = skin_guard

        _cancelled(cancelled, "enhance-save")
        _save_png(result, target)

        if effective_options.debug_outputs and masks:
            combined = Image.new("L", base.size, 0)
            for mask in masks:
                combined = __import__("PIL.ImageChops", fromlist=["lighter"]).lighter(combined, mask)
            mask_path = target.with_name(f"{target.stem}_region_mask.png")
            _save_png(combined, mask_path)
            artifacts.append(str(mask_path))

        if effective_options.debug_outputs and region_boxes:
            overlay = base.copy()
            draw = ImageDraw.Draw(overlay)
            for box in region_boxes:
                draw.rectangle(box, outline="lime", width=3)
            box_path = target.with_name(f"{target.stem}_boxes.png")
            _save_png(overlay, box_path)
            artifacts.append(str(box_path))

        with Image.open(target) as check:
            check.verify()

        tags = [f"mode:{route.effective_mode}", options.preset, f"stack:{','.join(applied_stack)}"]
        if route.use_model:
            tags.append("ai-restoration" if options.scale_factor == 1 else f"ai-{options.scale_factor}x")
        if region_fallback:
            tags.append(region_fallback)
        elif effective_options.region_mode != "none":
            tags.append(effective_options.region_mode)
        if effective_options.face_detail:
            tags.append("face-detail" if region_boxes else "face-detail:no-face")
        if effective_options.repair_alpha_holes:
            tags.append("alpha-hole-repair")

        action = "needs-review" if skin_guard["needs_review"] else "enhanced"
        if action == "needs-review":
            tags.append("skin-tone-review")

        return Result(str(source), action, " + ".join(tags), str(target), "+".join(tags), tuple(artifacts), metadata)
    except CommandCancelled:
        raise
    except (ImportError, OSError, ValueError) as ex:
        return Result(str(source), "failed", f"enhancement failed: {ex}", detail="enhance.failed")


def validate_result(result: Result) -> bool:
    if result.action == "dry-run":
        return True
    if result.action not in {"enhanced", "needs-review"} or not result.out_path:
        return False
    try:
        from PIL import Image
        path = Path(result.out_path)
        with Image.open(path) as image:
            image.verify()
        return path.stat().st_size > 0
    except (OSError, ImportError):
        return False

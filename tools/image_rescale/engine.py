"""Image Rescale engine — batch-resize images by one of four sizing modes.

Pure logic, no UI, no global state. Pillow only. The sizing math (resample
picking, snap-to-grid, upscale gating, half-up rounding) is distilled from
RupayanFlow's multi-mode resize engine, keeping the four modes a batch resizer
actually needs and dropping the crop/pad/mask machinery it doesn't.

Modes:
    longest_side  - scale the longest edge to N pixels
    max_mp        - scale to a target megapixel count (1 MP = 1024x1024)
    scale_factor  - multiply both dimensions by a factor
    fit_inside    - scale to fit within a WxH box (aspect preserved)

Errors are values: the standard envelope where a call can fail. Sizing itself is
a pure function. See CodingPrinciples.md.

Public interface:
    compute_size(mode, w, h, opts)  -> (w, h)          (pure)
    process(path, opts)             -> Result          (load -> resize -> save)
"""
from __future__ import annotations

import math
import os
import subprocess
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import CommandCancelled, IMAGE_EXTS, bundled_models_dir, run_cancellable_cmd
MODES = ("longest_side", "max_mp", "scale_factor", "fit_inside")

_AI_MODELS = {
    "realesrgan-x4plus": "Real-ESRGAN general 4×",
    "realesrgan-x4plus-anime": "Real-ESRGAN anime/art 4×",
}
_AI_BUNDLE_DIRNAME = "realesrgan-ncnn-20220424"

_MAX_DIM = 16384
_MAX_UPSCALE = 8.0


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _clamp_dims(w: int, h: int) -> tuple[int, int]:
    return (max(1, min(int(w), _MAX_DIM)), max(1, min(int(h), _MAX_DIM)))


def _apply_snap(w: int, h: int, snap: int) -> tuple[int, int]:
    if not snap or snap <= 0:
        return (w, h)
    return (max(1, (int(w) // snap) * snap), max(1, (int(h) // snap) * snap))


def _pick_resample(name: str, factor: float):
    from PIL import Image
    table = {"nearest": Image.NEAREST, "bilinear": Image.BILINEAR,
             "bicubic": Image.BICUBIC, "lanczos": Image.LANCZOS}
    if name in table:
        return table[name]
    # auto: Lanczos when shrinking (sharpest downscale), bilinear when enlarging.
    return Image.LANCZOS if factor < 1.0 else Image.BILINEAR


# ---------------------------------------------------------------------------
# sizing math — pure, no I/O
# ---------------------------------------------------------------------------

@dataclass
class ResizeOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    mode: str = "longest_side"
    longest_side: int = 1024
    max_mp: float = 1.0
    scale_factor: float = 0.5
    fit_w: int = 1024
    fit_h: int = 1024
    allow_upscale: bool = False       # by default never enlarge (avoids blurry blow-ups)
    snap: int = 0                     # round dims down to a multiple of this (0 = off)
    resample: str = "auto"            # auto | nearest | bilinear | bicubic | lanczos
    ai_upscale: bool = False            # explicit local Real-ESRGAN/NCNN execution
    ai_model: str = "realesrgan-x4plus"
    ai_tile_size: int = 0               # 0 = runtime auto; smaller values use less VRAM
    keep_format: bool = True          # keep the source extension (else force PNG)
    dry_run: bool = True


def ai_model_choices() -> tuple[str, ...]:
    """Stable model identifiers exposed by the UI and command line."""
    return tuple(_AI_MODELS)


def ai_runtime_status() -> dict[str, str | bool]:
    """Report the source-worktree Real-ESRGAN NCNN bundle without importing AI libs.

    The portable NCNN executable owns GPU inference.  It is deliberately kept
    under the ignored ``models/`` cache, so this tool does not add PyTorch or a
    background model service to Toolbox startup.
    """
    root = bundled_models_dir() / _AI_BUNDLE_DIRNAME
    executable_name = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
    executable = root / executable_name
    models_dir = root / "models"
    missing = []
    if not executable.is_file():
        missing.append("portable NCNN executable")
    for model_name in _AI_MODELS:
        if not (models_dir / f"{model_name}.param").is_file() or not (models_dir / f"{model_name}.bin").is_file():
            missing.append(model_name)
    ready = not missing
    return {
        "ready": ready,
        "root": str(root),
        "executable": str(executable),
        "models_dir": str(models_dir),
        "details": "Real-ESRGAN NCNN 4× models ready." if ready else f"Missing: {', '.join(missing)}.",
    }


def ai_runtime_dependencies(opts: ResizeOptions) -> tuple[Path, ...]:
    """Files that make an AI job's durable identity reproducible."""
    if not opts.ai_upscale:
        return ()
    status = ai_runtime_status()
    root = Path(str(status["root"]))
    model = opts.ai_model if opts.ai_model in _AI_MODELS else "realesrgan-x4plus"
    return (
        Path(str(status["executable"])),
        root / "models" / f"{model}.param",
        root / "models" / f"{model}.bin",
    )


def _factor_for(mode: str, orig_w: int, orig_h: int, opts: ResizeOptions) -> float:
    if mode == "longest_side":
        long_dim = max(orig_w, orig_h)
        return opts.longest_side / long_dim if long_dim else 1.0
    if mode == "max_mp":
        target_px = max(0.01, min(opts.max_mp, 64.0)) * 1024.0 * 1024.0
        cur = float(orig_w * orig_h)
        return math.sqrt(target_px / cur) if cur > 0 else 1.0
    if mode == "scale_factor":
        return max(0.01, opts.scale_factor)
    if mode == "fit_inside":
        fw = max(1, min(opts.fit_w, _MAX_DIM))
        fh = max(1, min(opts.fit_h, _MAX_DIM))
        return min(fw / orig_w, fh / orig_h) if orig_w and orig_h else 1.0
    return 1.0


def compute_size(mode: str, orig_w: int, orig_h: int, opts: ResizeOptions) -> tuple[int, int]:
    """The target (w, h) for a source, honouring upscale gating and snap. Pure."""
    factor = _factor_for(mode, orig_w, orig_h, opts)
    if not opts.allow_upscale:
        factor = min(factor, 1.0)
    factor = min(factor, _MAX_UPSCALE)
    w = _round_half_up(orig_w * factor)
    h = _round_half_up(orig_h * factor)
    w, h = _apply_snap(w, h, opts.snap)
    return _clamp_dims(w, h)


# ---------------------------------------------------------------------------
# process — one file
# ---------------------------------------------------------------------------

def plan_output(src: Path, opts: ResizeOptions) -> Path:
    """Where the resized image goes. Mirror preserves the input subtree under
    out_root; else flat under out_root; else a `resized` folder beside src."""
    src = Path(src)
    suffix = src.suffix if opts.keep_format else ".png"
    name = src.stem + suffix
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "resized" / name


@dataclass
class Result:
    src: str
    action: str                       # resized | skipped | failed | dry-run
    reason: str
    before: str = ""                  # "WxH"
    after: str = ""                   # "WxH"
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(
    path: str | Path,
    opts: ResizeOptions,
    *,
    cancelled=None,
) -> Result:
    """load -> compute target size -> resize -> save, for one image."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    try:
        from PIL import Image
    except ImportError:
        return Result(str(src), "failed", "Pillow not installed — pip install pillow", detail="dep.missing")

    # A `with` block guarantees the source handle is released on every path —
    # skip, dry-run, and the write path alike (matters for 100s-of-files batches
    # on Windows, where a held handle blocks the file).
    try:
        with Image.open(src) as im:
            orig_w, orig_h = im.size
            tw, th = compute_size(opts.mode, orig_w, orig_h, opts)
            before, after = f"{orig_w}x{orig_h}", f"{tw}x{th}"
            dst = plan_output(src, opts)

            if (tw, th) == (orig_w, orig_h):
                return Result(str(src), "skipped", "already at target size (or upscale disabled)",
                              before=before, after=after)
            if opts.dry_run:
                return Result(str(src), "dry-run", f"would resize {before} -> {after}",
                              before=before, after=after, out_path=str(dst))

            if opts.ai_upscale:
                return _process_ai_upscale(src, dst, tw, th, before, after, opts, cancelled=cancelled)

            factor = tw / orig_w if orig_w else 1.0
            resized = im.resize((tw, th), _pick_resample(opts.resample, factor))
            # JPEG can't hold alpha — flatten to RGB when saving a jpg.
            if dst.suffix.lower() in (".jpg", ".jpeg") and resized.mode in ("RGBA", "P", "LA"):
                resized = resized.convert("RGB")
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")  # atomic write
            resized.save(tmp)
            tmp.replace(dst)
    except Exception as ex:                       # PIL raises many types on bad images / saves
        return Result(str(src), "failed", f"resize failed on {src.name}: {ex}", detail="resize.failed")

    return Result(str(src), "resized", f"{before} -> {after}", before=before, after=after,
                  out_path=str(dst), detail=opts.mode)


def _process_ai_upscale(
    src: Path,
    dst: Path,
    target_w: int,
    target_h: int,
    before: str,
    after: str,
    opts: ResizeOptions,
    *,
    cancelled,
) -> Result:
    """Run one explicit NCNN job then precisely resize its output if required.

    NCNN supports native 2×/3×/4× outputs.  A non-native requested target is
    produced by the next native scale and a final deterministic resize, never a
    hidden Pillow-only fallback.
    """
    status = ai_runtime_status()
    if not status["ready"]:
        return Result(str(src), "failed", f"AI upscale unavailable — {status['details']}", detail="ai.not_ready")
    model = opts.ai_model if opts.ai_model in _AI_MODELS else "realesrgan-x4plus"
    source_w, source_h = (int(value) for value in before.split("x", 1))
    requested_scale = max(target_w / source_w, target_h / source_h)
    native_scale = 2 if requested_scale <= 2 else 3 if requested_scale <= 3 else 4
    work_output = dst.with_name(f".{dst.stem}.realesrgan.part.png")
    final_tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    cmd = [
        str(status["executable"]), "-i", str(src), "-o", str(work_output),
        "-s", str(native_scale), "-m", str(status["models_dir"]), "-n", model,
        "-t", str(max(0, int(opts.ai_tile_size))), "-f", "png",
    ]
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        completed = run_cancellable_cmd(
            cmd, timeout=300, cancelled=cancelled, capture_limit_bytes=16 * 1024,
        )
        if completed.returncode != 0 or not work_output.is_file() or work_output.stat().st_size <= 0:
            message = (completed.stderr or completed.stdout or "unknown NCNN failure").strip()
            return Result(str(src), "failed", f"AI upscale failed: {message[-800:]}", detail="ai.failed")
        from PIL import Image
        with Image.open(work_output) as generated:
            image = generated.convert("RGBA") if generated.mode in ("RGBA", "LA", "P") else generated.convert("RGB")
            if image.size != (target_w, target_h):
                image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            if dst.suffix.lower() in (".jpg", ".jpeg") and image.mode == "RGBA":
                image = image.convert("RGB")
            image.save(final_tmp)
        final_tmp.replace(dst)
    except CommandCancelled:
        return Result(str(src), "failed", "AI upscale cancelled", detail="ai.cancelled")
    except subprocess.TimeoutExpired:
        return Result(str(src), "failed", "AI upscale timed out after 300 seconds", detail="ai.timeout")
    except (OSError, ValueError) as ex:
        return Result(str(src), "failed", f"AI upscale failed: {ex}", detail="ai.failed")
    finally:
        for temporary in (work_output, final_tmp):
            try:
                temporary.unlink(missing_ok=True)
            except OSError as ex:
                warnings.warn(f"Could not clean temporary AI upscale output {temporary.name}: {ex}", RuntimeWarning)

    label = _AI_MODELS[model]
    return Result(
        str(src), "resized", f"{before} -> {after} ({label})", before=before, after=after,
        out_path=str(dst), detail=f"ai.realesrgan.{model}",
    )


def validate_result(result: Result) -> bool:
    """Deterministically verify a stored completed result before reusing it."""
    if result.action != "resized" or not result.out_path:
        return result.action in {"skipped", "dry-run"}
    path = Path(result.out_path)
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        expected = tuple(int(value) for value in result.after.lower().split("x", 1))
        if len(expected) != 2:
            return False
        from PIL import Image
        with Image.open(path) as image:
            actual = image.size
            image.verify()
        return actual == expected
    except (ImportError, OSError, ValueError):
        return False

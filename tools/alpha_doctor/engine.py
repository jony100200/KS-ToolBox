"""Alpha Doctor engine — deterministic background removal + alpha repair.

Pure logic, no UI, no global state. Cross-platform. **Deterministic-first**: the
default methods need no model, no download, no GPU, no network — just numpy +
Pillow — and give repeatable output from identical inputs. An optional AI method
(u2net on ONNX Runtime) is available for hard photographic subjects, but it is
never required and never the default.

Methods (pick per batch):
    solid       - auto-detect the flat background colour and key it out  (default)
    chroma      - key a chosen colour (green/blue/white/custom)
    edge_flood  - remove background regions connected to the image border
    ai          - u2net matte (opt-in; model download requires explicit permission)

Post-ops (all deterministic): green despill, defringe (erode+feather), premultiply.

Errors are values: every fallible call returns the standard envelope. See
CodingPrinciples.md.

Public (pure) interface:
    chroma_alpha(rgb, key, tol, feather) / detect_bg_color(rgb) / edge_flood_alpha(rgb, tol)
    despill(rgb) / defringe(rgba, ...) / premultiply(rgba) / alpha_coverage(rgba)
    matte(rgb, opts) -> alpha            (deterministic dispatch)
    remove_background(path, opts) -> envelope(RGBA)
    process(path, opts) -> Result
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from toolbox.engine_common import (
    IMAGE_EXTS,
    CommandCancelled,
    find_output_collisions as _find_output_collisions,
    sha256_file,
    ok as _ok,
    err as _err,
)

METHODS = ("solid", "chroma", "edge_flood", "ai")
KEY_PRESETS = {"green": "#00FF00", "blue": "#0000FF", "white": "#FFFFFF",
               "black": "#000000", "magenta": "#FF00FF"}

# --- optional AI backend (u2net on onnxruntime) -------------------------------
_ONNX_MODELS = {
    "u2net": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
              "60024c5c889badc19c04ad937298a77b"),
    "u2netp": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
               "8e83ca70e441ab06c318d82300c84806"),
}
AI_MODELS = tuple(_ONNX_MODELS)
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_SESSIONS: dict[str, Any] = {}
_VERIFIED_MODELS: dict[str, tuple[int, int, str]] = {}
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_COLOR_DISTANCE = math.sqrt(3 * 255 * 255)
_MAX_MODEL_BYTES = 512 * 1024 * 1024
Cancelled = Callable[[], bool] | None


def _cancelled(cancelled: Cancelled, stage: str, path: str | Path = "") -> None:
    if cancelled is not None and cancelled():
        command = [stage]
        if path:
            command.append(str(path))
        raise CommandCancelled(command)


def _hex_to_rgb(s: str) -> np.ndarray:
    s = (s or "").lstrip("#")
    try:
        return np.array([int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)], dtype=np.float32)
    except (ValueError, IndexError):
        return np.array([0, 255, 0], dtype=np.float32)


# --- deterministic keying (pure) ----------------------------------------------

def chroma_alpha(rgb: np.ndarray, key: np.ndarray, tol: float = 100.0,
                 feather: float = 60.0) -> np.ndarray:
    """Soft alpha by colour distance from `key`: 0 (transparent) where a pixel is
    within `tol` of the key colour, ramping to 255 (opaque) over `feather`."""
    d = np.sqrt(((rgb.astype(np.float32) - key) ** 2).sum(-1))
    a = np.clip((d - tol) / max(1e-3, feather), 0.0, 1.0)
    return (a * 255).astype(np.uint8)


def detect_bg_color(rgb: np.ndarray) -> np.ndarray:
    """Median colour of the 1-px border — a robust guess at a flat background."""
    b = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0).astype(np.float32)
    return np.median(b, axis=0)


def edge_flood_alpha(
    rgb: np.ndarray, tol: float = 30.0, cancelled: Cancelled = None
) -> np.ndarray:
    """Remove background regions connected to the image border. Marks pixels
    within `tol` of the border colour, then keeps only those reachable from the
    border via morphological reconstruction (border-seeded dilation ∩ that mask).
    Pure numpy — fast and deterministic (PIL's floodfill is pure-Python and far
    too slow to seed per-border-pixel)."""
    h, w = rgb.shape[:2]
    bg = detect_bg_color(rgb)
    like = np.sqrt(((rgb.astype(np.float32) - bg) ** 2).sum(-1)) <= tol   # background-coloured
    marker = np.zeros((h, w), dtype=bool)                                 # seed at the four borders
    marker[0, :] |= like[0, :]; marker[-1, :] |= like[-1, :]
    marker[:, 0] |= like[:, 0]; marker[:, -1] |= like[:, -1]
    for _ in range(h + w):                                # geodesic reach ≤ h+w; converges early
        _cancelled(cancelled, "alpha-edge-flood")
        dil = marker.copy()
        dil[1:, :] |= marker[:-1, :]; dil[:-1, :] |= marker[1:, :]
        dil[:, 1:] |= marker[:, :-1]; dil[:, :-1] |= marker[:, 1:]
        new = dil & like
        if np.array_equal(new, marker):
            break
        marker = new
    return np.where(marker, 0, 255).astype(np.uint8)


def matte(
    rgb: np.ndarray, opts: "AlphaOptions", cancelled: Cancelled = None
) -> np.ndarray:
    """Deterministic alpha for one RGB image by the chosen method. Pure."""
    if opts.method == "chroma":
        return chroma_alpha(rgb, _hex_to_rgb(opts.key_color), opts.tolerance, opts.feather_band)
    if opts.method == "solid":
        return chroma_alpha(rgb, detect_bg_color(rgb), max(20.0, opts.tolerance * 0.4), opts.feather_band)
    if opts.method == "edge_flood":
        return edge_flood_alpha(rgb, opts.tolerance, cancelled)
    raise ValueError(f"not a deterministic method: {opts.method}")


# --- optional AI matte --------------------------------------------------------

def _model_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "models")
    else:
        # Keep source-worktree models off the system drive.  A bundled app uses
        # the executable-adjacent directory above; a normal installed app still
        # falls back to its user cache below.
        dirs.append(Path(__file__).resolve().parents[2] / "models")
    dirs.append(Path.home() / ".u2net")
    dirs.append(Path.home() / ".cache" / "kstoolbox" / "models")
    return dirs


def cached_model_path(model: str) -> Path | None:
    if model not in _ONNX_MODELS:
        return None
    filename = f"{model}.onnx"
    return next(
        (directory / filename for directory in _model_dirs()
         if (directory / filename).is_file()),
        None,
    )


def _download_model_path(model: str) -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2] / "models" / f"{model}.onnx"
    return Path.home() / ".u2net" / f"{model}.onnx"


def _md5(path: Path, cancelled: Cancelled = None) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            _cancelled(cancelled, "alpha-model-checksum", path)
            h.update(chunk)
    return h.hexdigest()


def _verified_model(
    path: Path, expected_md5: str, cancelled: Cancelled = None
) -> dict:
    try:
        stat = path.stat()
        cache_key = str(path.resolve(strict=False))
        signature = (stat.st_size, stat.st_mtime_ns, expected_md5)
        if _VERIFIED_MODELS.get(cache_key) == signature:
            return _ok(path)
        if expected_md5 and _md5(path, cancelled) != expected_md5:
            _VERIFIED_MODELS.pop(cache_key, None)
            return _err(
                "model.corrupt",
                f"{path.name} failed its checksum; remove or replace {path}",
            )
        _VERIFIED_MODELS[cache_key] = signature
        return _ok(path)
    except CommandCancelled:
        raise
    except OSError as ex:
        return _err(
            "model.unreadable", f"could not verify {path.name}: {ex}", retryable=True
        )


def _ensure_model(
    model: str,
    allow_download: bool = False,
    cancelled: Cancelled = None,
) -> dict:
    if model not in _ONNX_MODELS:
        return _err("model.unknown", f"no verified model is known for '{model}'")
    _url, expected_md5 = _ONNX_MODELS[model]
    candidate = cached_model_path(model)
    if candidate is not None:
        return _verified_model(candidate, expected_md5, cancelled)
    if not allow_download:
        return _err(
            "model.permission",
            f"{model} is not installed; explicit permission is required before download",
        )
    url, md5 = _ONNX_MODELS[model]
    dst = _download_model_path(model)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".onnx.part")
    try:
        _cancelled(cancelled, "alpha-model-download", dst)
        digest = hashlib.md5()
        total = 0
        with urllib.request.urlopen(url, timeout=30) as response, tmp.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_MODEL_BYTES:
                raise OSError(
                    f"model download is larger than {_MAX_MODEL_BYTES} bytes"
                )
            while chunk := response.read(1 << 20):
                _cancelled(cancelled, "alpha-model-download", dst)
                total += len(chunk)
                if total > _MAX_MODEL_BYTES:
                    raise OSError(
                        f"model download exceeded {_MAX_MODEL_BYTES} bytes"
                    )
                output.write(chunk)
                digest.update(chunk)
        _cancelled(cancelled, "alpha-model-download-commit", dst)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return _err("model.download", f"could not download the {model} model: {ex}", retryable=True)
    if md5 and digest.hexdigest() != md5:
        tmp.unlink(missing_ok=True)
        return _err("model.corrupt", f"{model} model failed its checksum")
    tmp.replace(dst)
    stat = dst.stat()
    _VERIFIED_MODELS[str(dst.resolve(strict=False))] = (
        stat.st_size, stat.st_mtime_ns, md5,
    )
    return _ok(dst, degraded=True, details=f"downloaded {model} model")


def _ai_matte(
    img_rgb,
    model: str,
    allow_download: bool = False,
    cancelled: Cancelled = None,
) -> dict:
    try:
        import onnxruntime as ort
        from PIL import Image
    except ImportError as ex:
        return _err("dep.missing", f"the AI method needs onnxruntime ({ex.name}) — pip install onnxruntime")
    mp = _ensure_model(
        model, allow_download=allow_download, cancelled=cancelled
    )
    if mp["error"]:
        return mp
    key = str(mp["data"])
    if key not in _SESSIONS:
        _cancelled(cancelled, "alpha-model-load", key)
        _SESSIONS[key] = ort.InferenceSession(key, providers=["CPUExecutionProvider"])
        _cancelled(cancelled, "alpha-model-load", key)
    sess = _SESSIONS[key]
    w, h = img_rgb.size
    small = img_rgb.resize((320, 320), Image.BILINEAR)
    arr = ((np.asarray(small, dtype=np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[None]
    _cancelled(cancelled, "alpha-inference", key)
    mask = sess.run(None, {sess.get_inputs()[0].name: arr.astype(np.float32)})[0][0, 0]
    _cancelled(cancelled, "alpha-inference", key)
    mi, ma = float(mask.min()), float(mask.max())
    mask = (mask - mi) / (ma - mi + 1e-8)
    up = Image.fromarray((mask * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    return _ok(np.asarray(up, dtype=np.uint8), degraded=mp["degraded"], details=mp["details"])


# --- edge cleanup + alpha ops (pure) ------------------------------------------

def despill(rgb: np.ndarray) -> np.ndarray:
    """Clamp green to max(red, blue) per pixel — kills green-screen tint. float32."""
    r, g, b = rgb[:, :, 0].astype(np.float32), rgb[:, :, 1].astype(np.float32), rgb[:, :, 2].astype(np.float32)
    return np.stack([r, np.minimum(g, np.maximum(r, b)), b], -1)


def defringe(rgba: np.ndarray, erode_px: int = 1, feather: float = 0.6) -> np.ndarray:
    """Erode the alpha edge inward then feather — removes the 1-px halo. RGBA in/out."""
    from PIL import Image, ImageFilter
    alpha = Image.fromarray(rgba[:, :, 3])
    if erode_px > 0:
        alpha = alpha.filter(ImageFilter.MinFilter(2 * erode_px + 1))
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = rgba.copy()
    out[:, :, 3] = np.asarray(alpha, dtype=np.uint8)
    return out


def premultiply(rgba: np.ndarray) -> np.ndarray:
    """Multiply RGB by alpha (straight → premultiplied). Deterministic."""
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    out = rgba.copy()
    out[:, :, :3] = (rgba[:, :, :3].astype(np.float32) * a).clip(0, 255).astype(np.uint8)
    return out


def alpha_coverage(rgba: np.ndarray) -> float:
    """Fraction of pixels meaningfully opaque — a sanity number (near 0 = bad cut)."""
    return float((rgba[:, :, 3].astype(np.float32) / 255.0 > 0.5).mean())


# --- process ------------------------------------------------------------------

@dataclass
class AlphaOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    method: str = "solid"             # deterministic default (no model/download)
    key_color: str = "#00FF00"        # chroma method
    tolerance: float = 100.0          # keying tolerance / flood threshold
    feather_band: float = 60.0        # chroma soft-edge width
    model: str = "u2net"              # ai method only
    allow_model_download: bool = False
    do_defringe: bool = True
    erode_px: int = 1
    feather: float = 0.6
    green_despill: bool = False
    do_premultiply: bool = False
    min_coverage: float = 0.005
    dry_run: bool = True


def normalized_options(opts: AlphaOptions) -> tuple[AlphaOptions | None, str]:
    method = str(opts.method).strip().lower()
    if method not in METHODS:
        return None, f"unknown alpha method: {opts.method!r}"
    model = str(opts.model).strip().lower()
    if method == "ai" and model not in AI_MODELS:
        return None, f"unknown AI model: {opts.model!r}"
    key_color = str(opts.key_color).strip()
    if method == "chroma" and not _HEX_COLOR.fullmatch(key_color):
        return None, "key colour must use #RRGGBB"
    numeric = {
        "tolerance": opts.tolerance,
        "feather band": opts.feather_band,
        "feather": opts.feather,
        "minimum coverage": opts.min_coverage,
    }
    try:
        values = {name: float(value) for name, value in numeric.items()}
    except (TypeError, ValueError):
        return None, "alpha numeric settings must be numbers"
    if any(not math.isfinite(value) for value in values.values()):
        return None, "alpha numeric settings must be finite"
    if not 0 <= values["tolerance"] <= _MAX_COLOR_DISTANCE:
        return None, f"tolerance must be between 0 and {_MAX_COLOR_DISTANCE:.1f}"
    if not 0 <= values["feather band"] <= _MAX_COLOR_DISTANCE:
        return None, f"feather band must be between 0 and {_MAX_COLOR_DISTANCE:.1f}"
    if not 0 <= values["feather"] <= 100:
        return None, "defringe feather must be between 0 and 100"
    if not 0 <= values["minimum coverage"] <= 1:
        return None, "minimum coverage must be between 0 and 1"
    if isinstance(opts.erode_px, bool):
        return None, "defringe erosion must be a whole number between 0 and 32"
    try:
        erode_px = int(opts.erode_px)
    except (TypeError, ValueError):
        return None, "defringe erosion must be a whole number between 0 and 32"
    if erode_px != opts.erode_px or not 0 <= erode_px <= 32:
        return None, "defringe erosion must be a whole number between 0 and 32"
    try:
        out_root = Path(opts.out_root) if opts.out_root is not None else None
        input_root = Path(opts.input_root) if opts.input_root is not None else None
    except TypeError:
        return None, "input and output roots must be filesystem paths"
    return replace(
        opts,
        out_root=out_root,
        input_root=input_root,
        method=method,
        model=model,
        key_color=key_color.upper(),
        tolerance=values["tolerance"],
        feather_band=values["feather band"],
        feather=values["feather"],
        min_coverage=values["minimum coverage"],
        erode_px=erode_px,
        mirror=bool(opts.mirror),
        allow_model_download=bool(opts.allow_model_download),
        do_defringe=bool(opts.do_defringe),
        green_despill=bool(opts.green_despill),
        do_premultiply=bool(opts.do_premultiply),
        dry_run=bool(opts.dry_run),
    ), ""


def plan_output(src: Path, opts: AlphaOptions) -> Path:
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / (src.stem + ".png")
            except ValueError:
                pass
        return root / (src.stem + ".png")
    return src.parent / "cutouts" / (src.stem + ".png")


def find_output_collisions(paths, opts: AlphaOptions) -> dict[str, tuple[str, ...]]:
    normalized, _ = normalized_options(opts)
    if normalized is None:
        return {}
    return _find_output_collisions(
        [Path(path) for path in paths],
        lambda source: [plan_output(source, normalized)],
    )


def remove_background(
    path: str | Path,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
) -> dict:
    """RGBA cut for one image by the chosen method. Envelope out."""
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        return _err("options.invalid", options_error)
    opts = normalized
    p = Path(path)
    _cancelled(cancelled, "alpha-open", p)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    try:
        from PIL import Image
    except ImportError:
        return _err("dep.missing", "Alpha Doctor needs Pillow + numpy — pip install pillow numpy")
    try:
        src = Image.open(p).convert("RGB")
        _cancelled(cancelled, "alpha-open", p)
    except CommandCancelled:
        raise
    except Exception as ex:
        return _err("image.unreadable", f"cannot read {p.name}: {ex}")
    rgb = np.asarray(src, dtype=np.uint8)

    degraded, details = False, ""
    if opts.method == "ai":
        am = _ai_matte(
            src, opts.model, allow_download=opts.allow_model_download,
            cancelled=cancelled,
        )
        if am["error"]:
            return am
        alpha = am["data"]; degraded, details = am["degraded"], am["details"]
    else:
        try:
            alpha = matte(rgb, opts, cancelled)
        except CommandCancelled:
            raise
        except Exception as ex:
            return _err("matte.failed", f"{p.name}: {ex}")
    return _ok(np.dstack([rgb, alpha]), degraded=degraded, details=details)


@dataclass
class Result:
    src: str
    action: str                       # cut | skipped | failed | dry-run
    reason: str
    coverage: float = 0.0
    out_path: str | None = None
    detail: str = ""
    artifact: dict | None = None
    degraded: bool = False
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _png_artifact(
    path: Path,
    expected_size: tuple[int, int] | None = None,
    cancelled: Cancelled = None,
) -> dict:
    from PIL import Image

    _cancelled(cancelled, "alpha-output-validation", path)
    with Image.open(path) as image:
        image.load()
        if image.mode != "RGBA":
            raise OSError(f"output is {image.mode}, expected RGBA")
        if expected_size is not None and image.size != expected_size:
            raise OSError(
                f"output is {image.width}x{image.height}, expected "
                f"{expected_size[0]}x{expected_size[1]}"
            )
        coverage = alpha_coverage(np.asarray(image, dtype=np.uint8))
        width, height = image.size
    _cancelled(cancelled, "alpha-output-validation", path)
    size = path.stat().st_size
    if size <= 0:
        raise OSError("output is empty")
    return {
        "path": str(path),
        "bytes": size,
        "sha256": sha256_file(path, cancelled=cancelled),
        "width": width,
        "height": height,
        "mode": "RGBA",
        "coverage": coverage,
    }


def process(
    path: str | Path,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
) -> Result:
    """key -> [despill] -> [defringe] -> [premultiply] -> save RGBA PNG."""
    src = Path(path)
    _cancelled(cancelled, "alpha-start", src)
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        return Result(str(src), "failed", options_error, detail="options.invalid")
    opts = normalized
    dst = plan_output(src, opts)
    collisions = find_output_collisions([src], opts)
    if collisions:
        return Result(
            str(src), "failed", "output would overwrite the selected source",
            out_path=str(dst), detail="output.collision",
        )
    if opts.dry_run:
        return Result(str(src), "dry-run", f"would cut ({opts.method})", out_path=str(dst))

    rb = remove_background(src, opts, cancelled)
    if rb["error"]:
        return Result(
            str(src), "failed", rb["details"], detail=rb["error_type"],
            degraded=rb["degraded"], retryable=rb["retryable"],
        )
    rgba: np.ndarray = rb["data"]

    _cancelled(cancelled, "alpha-postprocess", src)
    if opts.green_despill:
        rgba = np.dstack([despill(rgba[:, :, :3]).clip(0, 255).astype(np.uint8), rgba[:, :, 3]])
        _cancelled(cancelled, "alpha-despill", src)
    if opts.do_defringe:
        rgba = defringe(rgba, opts.erode_px, opts.feather)
        _cancelled(cancelled, "alpha-defringe", src)
    if opts.do_premultiply:
        rgba = premultiply(rgba)
        _cancelled(cancelled, "alpha-premultiply", src)

    coverage = alpha_coverage(rgba)
    if coverage < opts.min_coverage:
        return Result(str(src), "skipped", f"kept only {coverage*100:.1f}% — likely a bad key, not saved",
                      coverage=coverage, out_path=str(dst), detail="empty-matte",
                      degraded=rb["degraded"])

    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    try:
        from PIL import Image
        dst.parent.mkdir(parents=True, exist_ok=True)
        _cancelled(cancelled, "alpha-save", dst)
        Image.fromarray(rgba, "RGBA").save(tmp)
        artifact = _png_artifact(
            tmp, expected_size=(rgba.shape[1], rgba.shape[0]), cancelled=cancelled
        )
        _cancelled(cancelled, "alpha-output-commit", dst)
        tmp.replace(dst)
        if dst.stat().st_size != artifact["bytes"]:
            raise OSError("committed output size changed")
        artifact["path"] = str(dst)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as ex:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            return Result(
                str(src), "failed",
                f"could not save {dst.name}; staged-file cleanup also failed: {cleanup}",
                detail="save.cleanup", retryable=True,
            )
        return Result(
            str(src), "failed", f"could not save {dst.name}: {ex}",
            detail="save.failed", retryable=isinstance(ex, OSError),
        )

    tags = [opts.method]
    if opts.green_despill: tags.append("despill")
    if opts.do_defringe: tags.append("defringe")
    if opts.do_premultiply: tags.append("premult")
    reason = "background removed"
    if rb["details"]:
        reason += f"; {rb['details']}"
    return Result(
        str(src), "cut", reason, coverage=artifact["coverage"],
        out_path=str(dst), detail="+".join(tags), artifact=artifact,
        degraded=rb["degraded"],
    )


def validate_result(
    result: Result,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
    expected_src: str | Path | None = None,
) -> bool:
    normalized, _ = normalized_options(opts)
    if normalized is None or result.retryable or result.action == "failed":
        return False
    if (
        expected_src is not None
        and Path(result.src).resolve(strict=False)
        != Path(expected_src).resolve(strict=False)
    ):
        return False
    expected = plan_output(Path(result.src), normalized)
    if result.out_path is None or Path(result.out_path).resolve(strict=False) != expected.resolve(strict=False):
        return False
    if result.action == "dry-run":
        return normalized.dry_run and result.artifact is None
    if normalized.dry_run:
        return False
    if result.action == "skipped":
        return result.detail == "empty-matte" and result.artifact is None
    if result.action != "cut" or not isinstance(result.artifact, dict):
        return False
    try:
        record = result.artifact
        if Path(record["path"]).resolve(strict=False) != expected.resolve(strict=False):
            return False
        actual = _png_artifact(
            expected,
            expected_size=(int(record["width"]), int(record["height"])),
            cancelled=cancelled,
        )
    except CommandCancelled:
        raise
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return actual == record and actual["coverage"] == result.coverage

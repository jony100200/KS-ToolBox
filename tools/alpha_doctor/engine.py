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

Post-ops (all deterministic): despill, defringe (erode+feather), premultiply.
Despill defaults to *auto*: when the background is a green or blue screen, the
screen colour is suppressed in the edge band only (real green/blue subject colour
inside the cut is untouched). ``green_despill=True`` keeps the old whole-image
green clamp; ``False`` turns despill off.

Errors are values: every fallible call returns the standard envelope. See
CodingPrinciples.md.

Public (pure) interface:
    chroma_alpha(rgb, key, tol, feather) / detect_bg_color(rgb) / edge_flood_alpha(rgb, tol)
    despill(rgb) / decontaminate_edges(rgba, ...) / defringe(rgba, ...) / premultiply(rgba)
    alpha_coverage(rgba) / is_video_path(path) / tools_status()
    matte(rgb, opts) -> alpha            (deterministic dispatch)
    remove_background(path, opts) -> envelope(RGBA)
    process(path, opts) -> Result
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from toolbox.engine_common import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    CommandCancelled,
    _terminate_owned_process,
    _wait_for_owned_exit,
    bundled_models_dir,
    find_output_collisions as _find_output_collisions,
    resolve_tool,
    run_cancellable_cmd,
    sha256_file,
    tools_status as _tools_status_raw,
    ok as _ok,
    err as _err,
)

MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
METHODS = ("solid", "chroma", "edge_flood", "ai")
DESPILL_MODES = ("green", "blue", "red", "orange")
KEY_PRESETS = {"green": "#00FF00", "blue": "#0000FF", "white": "#FFFFFF",
               "black": "#000000", "magenta": "#FF00FF"}
_MOV_COPY_AUDIO_CODECS = frozenset({
    "aac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "mp3", "ac3",
})
_VIDEO_PROBE_TIMEOUT = 60
_STDERR_TAIL_BYTES = 64 * 1024


def is_video_path(path: str | Path) -> bool:
    """True when *path* carries a supported video extension."""
    return Path(path).suffix.lower() in VIDEO_EXTS


def tools_status() -> dict[str, str | None]:
    """External media binary availability (required for video inputs only)."""
    return _tools_status_raw(["ffprobe", "ffmpeg"])

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


def unload_models() -> int:
    """Drop cached onnxruntime sessions (the optional u2net AI method) so the
    process can free their memory. Returns how many were dropped."""
    n = len(_SESSIONS)
    _SESSIONS.clear()
    return n
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


def _reconstruct(marker: np.ndarray, mask: np.ndarray, cancelled: Cancelled) -> np.ndarray:
    """Morphological reconstruction: grow ``marker`` inside ``mask`` (4-connected)."""
    h, w = mask.shape
    marker = marker & mask
    for _ in range(h + w):                                # geodesic reach <= h+w; converges early
        _cancelled(cancelled, "alpha-edge-flood")
        dil = marker.copy()
        dil[1:, :] |= marker[:-1, :]; dil[:-1, :] |= marker[1:, :]
        dil[:, 1:] |= marker[:, :-1]; dil[:, :-1] |= marker[:, 1:]
        new = dil & mask
        if np.array_equal(new, marker):
            break
        marker = new
    return marker


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    from PIL import Image, ImageFilter
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    return np.asarray(img.filter(ImageFilter.MinFilter(2 * radius + 1))) > 127


def edge_flood_alpha(
    rgb: np.ndarray, tol: float = 30.0, cancelled: Cancelled = None,
    remove_enclosed: bool = False, enclosed_min_px: int = 4,
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
    marker = _reconstruct(marker, like, cancelled)
    if remove_enclosed:
        # Background trapped inside the subject (e.g. the gap between an arm and the
        # body) is not connected to the border. Remove enclosed background-coloured
        # areas that are thicker than 2*enclosed_min_px+1 px; thin strokes and small
        # highlights of the same colour stay. Off by default: a white subject on a
        # white background would lose its white parts.
        inner = like & ~marker
        seeds = _erode(inner, max(1, int(enclosed_min_px)))
        if seeds.any():
            marker |= _reconstruct(seeds, inner, cancelled)
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
        return edge_flood_alpha(rgb, opts.tolerance, cancelled, remove_enclosed=opts.remove_enclosed)
    raise ValueError(f"not a deterministic method: {opts.method}")


# --- optional AI matte --------------------------------------------------------

def _model_dirs() -> list[Path]:
    dirs: list[Path] = [bundled_models_dir()]
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
    target = bundled_models_dir() / f"{model}.onnx"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


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

def despill(rgb: np.ndarray, screen: str = "green") -> np.ndarray:
    """Clamp screen or rim spill channel (`green`, `blue`, `red`, `orange`). float32."""
    r, g, b = rgb[:, :, 0].astype(np.float32), rgb[:, :, 1].astype(np.float32), rgb[:, :, 2].astype(np.float32)
    mode = (screen or "green").strip().lower()
    if mode == "blue":
        return np.stack([r, g, np.minimum(b, np.maximum(r, g))], -1)
    if mode == "red":
        return np.stack([np.minimum(r, np.maximum(g, b)), g, b], -1)
    if mode == "orange":
        hi = np.maximum(g, b)
        excess_r = np.maximum(0.0, r - hi)
        g_pull = 0.75 * np.minimum(np.maximum(0.0, g - b), excess_r)
        g_new = g - g_pull
        r_new = np.minimum(r, np.maximum(g_new, b))
        return np.stack([r_new, g_new, b], -1)
    return np.stack([r, np.minimum(g, np.maximum(r, b)), b], -1)


def screen_colour(bg: np.ndarray) -> str | None:
    """'green' / 'blue' / 'red' when a background colour is a strong colour screen, else None."""
    r, g, b = (float(v) for v in bg[:3])
    if g >= 80 and g > 1.4 * max(r, b):
        return "green"
    if b >= 80 and b > 1.4 * max(r, g):
        return "blue"
    if r >= 80 and r > 1.4 * max(g, b):
        return "red"
    return None


def edge_despill(rgba: np.ndarray, screen: str, band_px: int = 2) -> np.ndarray:
    """Despill everything except the opaque interior deeper than ``band_px``.

    Transparent pixels are included: their colour is invisible but would bleed back
    in when the cut is resized or filtered.
    """
    from PIL import Image, ImageFilter
    alpha = rgba[:, :, 3]
    band = np.asarray(Image.fromarray(alpha).filter(ImageFilter.MinFilter(2 * band_px + 1))) < 255
    out = rgba.copy()
    if band.any():
        fixed = despill(rgba[:, :, :3], screen).clip(0, 255).astype(np.uint8)
        out[band, :3] = fixed[band]
    return out


def decontaminate_edges(
    rgba: np.ndarray,
    inward_px: int = 2,
    bleed_px: int = 2,
    core_alpha: int = 250,
) -> np.ndarray:
    """Propagate clean interior RGB outward across eroded and partial-alpha edges."""
    inward = max(0, int(inward_px))
    bleed = max(0, int(bleed_px))
    if inward == 0 and bleed == 0:
        return rgba.copy()
    h, w = rgba.shape[:2]
    solid = rgba[:, :, 3] >= core_alpha
    core = _erode(solid, inward) if inward > 0 else solid
    if not core.any():
        core = solid
    if not core.any():
        return rgba.copy()

    known = core.copy()
    color = np.where(known[..., None], rgba[:, :, :3].astype(np.float32), 0.0)
    steps = max(1, inward + max(1, bleed))
    w_pad = np.zeros((h + 2, w + 2), dtype=np.float32)
    c_pad = np.zeros((h + 2, w + 2, 3), dtype=np.float32)
    for _ in range(steps):
        w_pad[1:-1, 1:-1] = known
        c_pad[1:-1, 1:-1] = color
        wh = w_pad[:, :-2] + w_pad[:, 1:-1] + w_pad[:, 2:]
        ws = wh[:-2, :] + wh[1:-1, :] + wh[2:, :]
        ch = c_pad[:, :-2] + c_pad[:, 1:-1] + c_pad[:, 2:]
        cs = ch[:-2, :] + ch[1:-1, :] + ch[2:, :]
        frontier = (~known) & (ws > 0.0)
        if not frontier.any():
            break
        color[frontier] = cs[frontier] / ws[frontier, None]
        known |= frontier

    apply_mask = (~core) & known
    out = rgba.copy()
    if apply_mask.any():
        out[apply_mask, :3] = np.clip(color[apply_mask], 0, 255).astype(np.uint8)
    return out


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
    green_despill: bool | str | None = None  # None = auto; bool or "green"/"blue"/"red"/"orange"
    remove_enclosed: bool = False     # edge_flood: also remove enclosed background areas
    do_decontaminate: bool = False    # inward RGB edge decontamination before defringe
    decontaminate_px: int = 2
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
    raw_despill = opts.green_despill
    if raw_despill is None or isinstance(raw_despill, bool):
        green_despill: bool | str | None = raw_despill
    elif isinstance(raw_despill, str):
        gval = raw_despill.strip().lower()
        if gval == "auto":
            green_despill = None
        elif gval == "off":
            green_despill = False
        elif gval == "green_all":
            green_despill = True
        elif gval in DESPILL_MODES:
            green_despill = gval
        else:
            return None, f"unknown despill mode: {opts.green_despill!r}"
    else:
        return None, f"unknown despill mode: {opts.green_despill!r}"
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
    if isinstance(opts.decontaminate_px, bool):
        return None, "edge decontamination radius must be a whole number between 1 and 16"
    try:
        decontaminate_px = int(opts.decontaminate_px)
    except (TypeError, ValueError):
        return None, "edge decontamination radius must be a whole number between 1 and 16"
    if decontaminate_px != opts.decontaminate_px or not 1 <= decontaminate_px <= 16:
        return None, "edge decontamination radius must be a whole number between 1 and 16"
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
        decontaminate_px=decontaminate_px,
        mirror=bool(opts.mirror),
        allow_model_download=bool(opts.allow_model_download),
        do_defringe=bool(opts.do_defringe),
        green_despill=green_despill,
        remove_enclosed=bool(opts.remove_enclosed),
        do_decontaminate=bool(opts.do_decontaminate),
        do_premultiply=bool(opts.do_premultiply),
        dry_run=bool(opts.dry_run),
    ), ""


def plan_output(src: Path, opts: AlphaOptions) -> Path:
    src = Path(src)
    ext = ".mov" if is_video_path(src) else ".png"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / (src.stem + ext)
            except ValueError:
                pass
        return root / (src.stem + ext)
    return src.parent / "cutouts" / (src.stem + ext)


def find_output_collisions(paths, opts: AlphaOptions) -> dict[str, tuple[str, ...]]:
    normalized, _ = normalized_options(opts)
    if normalized is None:
        return {}
    return _find_output_collisions(
        [Path(path) for path in paths],
        lambda source: [plan_output(source, normalized)],
    )


def _matte_rgb(
    rgb: np.ndarray,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
) -> dict:
    """Compute alpha for an in-memory uint8 RGB array. Envelope out."""
    if opts.method == "ai":
        from PIL import Image

        return _ai_matte(
            Image.fromarray(rgb, "RGB"),
            opts.model,
            allow_download=opts.allow_model_download,
            cancelled=cancelled,
        )
    try:
        alpha = matte(rgb, opts, cancelled)
    except CommandCancelled:
        raise
    except Exception as ex:
        return _err("matte.failed", str(ex))
    return _ok(alpha)


def _apply_post_ops(
    rgba: np.ndarray,
    opts: AlphaOptions,
    src: Path,
    cancelled: Cancelled = None,
) -> tuple[np.ndarray, list[str]]:
    """Apply deterministic despill -> inward decontamination -> defringe -> premultiply."""
    _cancelled(cancelled, "alpha-postprocess", src)
    tags = [opts.method]
    if opts.remove_enclosed and opts.method == "edge_flood":
        tags.append("enclosed")

    despill_tag = ""
    if opts.green_despill is True:
        rgba = np.dstack([despill(rgba[:, :, :3], "green").clip(0, 255).astype(np.uint8), rgba[:, :, 3]])
        despill_tag = "despill"
        _cancelled(cancelled, "alpha-despill", src)
    elif isinstance(opts.green_despill, str):
        band_px = max(2, opts.decontaminate_px if opts.do_decontaminate else 2)
        rgba = edge_despill(rgba, opts.green_despill, band_px=band_px)
        despill_tag = f"despill:{opts.green_despill}"
        _cancelled(cancelled, "alpha-despill", src)
    elif opts.green_despill is None:
        bg = _hex_to_rgb(opts.key_color) if opts.method == "chroma" else detect_bg_color(rgba[:, :, :3])
        screen = screen_colour(bg)
        if screen:
            rgba = edge_despill(rgba, screen)
            despill_tag = f"despill:auto-{screen}"
            _cancelled(cancelled, "alpha-despill", src)
    if despill_tag:
        tags.append(despill_tag)

    if opts.do_decontaminate:
        rgba = decontaminate_edges(
            rgba,
            inward_px=opts.decontaminate_px,
            bleed_px=max(2, opts.erode_px + 2),
        )
        tags.append(f"decontam:{opts.decontaminate_px}")
        _cancelled(cancelled, "alpha-decontaminate", src)

    if opts.do_defringe:
        rgba = defringe(rgba, opts.erode_px, opts.feather)
        tags.append("defringe")
        _cancelled(cancelled, "alpha-defringe", src)

    if opts.do_premultiply:
        rgba = premultiply(rgba)
        tags.append("premult")
        _cancelled(cancelled, "alpha-premultiply", src)

    return rgba, tags


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
    mm = _matte_rgb(rgb, opts, cancelled=cancelled)
    if mm["error"]:
        if mm["error_type"] == "matte.failed":
            return _err("matte.failed", f"{p.name}: {mm['details']}")
        return mm
    return _ok(
        np.dstack([rgb, mm["data"]]),
        degraded=mm["degraded"],
        details=mm["details"],
    )


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


def _parse_fps_str(stream: dict) -> str:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(stream.get(key) or "").strip()
        if "/" in raw:
            num_s, den_s = raw.split("/", 1)
            try:
                num, den = int(num_s), int(den_s)
            except ValueError:
                continue
            if num > 0 and den > 0:
                return f"{num}/{den}"
        elif raw:
            try:
                val = float(raw)
            except ValueError:
                continue
            if math.isfinite(val) and val > 0:
                return raw
    return "30/1"


def _read_exact(stream: Any, nbytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = nbytes
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _start_stderr_drain(stream: Any) -> tuple[threading.Thread, bytearray]:
    tail = bytearray()

    def _drain() -> None:
        try:
            while chunk := stream.read(64 * 1024):
                tail.extend(chunk)
                overflow = len(tail) - _STDERR_TAIL_BYTES
                if overflow > 0:
                    del tail[:overflow]
        except OSError:
            pass

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread, tail


def _stop_video_procs(
    procs: list[subprocess.Popen],
    threads: list[threading.Thread],
) -> None:
    for proc in procs:
        if proc.poll() is None:
            _terminate_owned_process(proc)
    for proc in procs:
        try:
            _wait_for_owned_exit(proc)
        except OSError:
            pass
    for stream_attr in ("stdin", "stdout", "stderr"):
        for proc in procs:
            handle = getattr(proc, stream_attr, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
    for thread in threads:
        thread.join(timeout=2.0)


def _probe_video(path: Path, cancelled: Cancelled = None) -> dict:
    ffprobe = resolve_tool("ffprobe")
    if not ffprobe:
        return _err(
            "dep.missing",
            "ffprobe not found (bundle a bin/ or install ffmpeg)",
        )
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,nb_frames:format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        completed = run_cancellable_cmd(
            cmd,
            timeout=_VIDEO_PROBE_TIMEOUT,
            cancelled=cancelled,
            capture_limit_bytes=256 * 1024,
        )
    except subprocess.TimeoutExpired:
        return _err("probe.timeout", f"ffprobe timed out on {path.name}", retryable=True)
    if completed.returncode != 0:
        return _err("probe.failed", f"ffprobe failed: {(completed.stderr or '').strip()[:200]}")
    try:
        info = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as ex:
        return _err("probe.parse", f"could not parse ffprobe output: {ex}")
    streams = info.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v_stream is None:
        return _err("probe.no_video", f"{path.name} has no video stream")
    try:
        width = int(v_stream.get("width") or 0)
        height = int(v_stream.get("height") or 0)
    except (TypeError, ValueError):
        width, height = 0, 0
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        return _err("probe.dimensions", f"unsupported video dimensions: {width}x{height}")
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    raw_nb = v_stream.get("nb_frames")
    try:
        nb_frames = int(raw_nb) if raw_nb not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        nb_frames = None
    return _ok({
        "width": width,
        "height": height,
        "codec_name": str(v_stream.get("codec_name") or "").lower(),
        "pix_fmt": str(v_stream.get("pix_fmt") or "").lower(),
        "fps_str": _parse_fps_str(v_stream),
        "nb_frames": nb_frames,
        "has_audio": a_stream is not None,
        "audio_codec": str((a_stream or {}).get("codec_name") or "").lower(),
    })


def _decode_first_frame_rgba(
    path: Path,
    width: int,
    height: int,
    cancelled: Cancelled = None,
) -> dict:
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg:
        raise OSError("ffmpeg not found")
    expected = width * height * 4
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
        "-vf", "format=rgba", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(0x08000000 if os.name == "nt" else 0),
        start_new_session=(os.name != "nt"),
    )
    assert proc.stdout is not None and proc.stderr is not None
    err_thread, err_tail = _start_stderr_drain(proc.stderr)
    try:
        _cancelled(cancelled, "alpha-output-validation", path)
        raw = _read_exact(proc.stdout, expected)
        _cancelled(cancelled, "alpha-output-validation", path)
        proc.stdout.close()
        rc = proc.wait(timeout=_VIDEO_PROBE_TIMEOUT)
        err_thread.join(timeout=2.0)
        proc.stderr.close()
    except CommandCancelled:
        _stop_video_procs([proc], [err_thread])
        raise
    except subprocess.TimeoutExpired as ex:
        _stop_video_procs([proc], [err_thread])
        raise OSError(f"ffmpeg validation timed out: {ex}") from ex
    except BaseException:
        _stop_video_procs([proc], [err_thread])
        raise
    if rc != 0 or len(raw) != expected:
        msg = bytes(err_tail).decode("utf-8", errors="replace").strip()[-200:]
        raise OSError(f"cannot decode RGBA frame from {path.name}: {msg or f'got {len(raw)}/{expected} bytes'}")
    rgba = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
    alpha = rgba[:, :, 3]
    return {
        "alpha_min": int(alpha.min()),
        "alpha_max": int(alpha.max()),
        "coverage": alpha_coverage(rgba),
    }


def _video_artifact(
    path: Path,
    expected_size: tuple[int, int] | None = None,
    expected_frames: int | None = None,
    expected_audio: bool | None = None,
    cancelled: Cancelled = None,
) -> dict:
    _cancelled(cancelled, "alpha-output-validation", path)
    probed = _probe_video(path, cancelled=cancelled)
    if probed["error"]:
        raise OSError(probed["details"])
    info = probed["data"]
    size_wh = (info["width"], info["height"])
    if expected_size is not None and size_wh != expected_size:
        raise OSError(
            f"output is {size_wh[0]}x{size_wh[1]}, expected "
            f"{expected_size[0]}x{expected_size[1]}"
        )
    if info["codec_name"] != "prores":
        raise OSError(f"output codec is {info['codec_name']!r}, expected 'prores'")
    if not info["pix_fmt"].startswith("yuva"):
        raise OSError(f"output pixel format is {info['pix_fmt']!r}, expected alpha yuva*")
    frames = info["nb_frames"] if info["nb_frames"] is not None else expected_frames
    if frames is None or frames <= 0:
        raise OSError("output contains no video frames")
    if expected_frames is not None and frames != expected_frames:
        raise OSError(f"output has {frames} frames, expected {expected_frames}")
    if expected_audio is True and not info["has_audio"]:
        raise OSError("output lost source audio track")
    first = _decode_first_frame_rgba(path, info["width"], info["height"], cancelled=cancelled)
    _cancelled(cancelled, "alpha-output-validation", path)
    size = path.stat().st_size
    if size <= 0:
        raise OSError("output is empty")
    return {
        "path": str(path),
        "bytes": size,
        "sha256": sha256_file(path, cancelled=cancelled),
        "width": info["width"],
        "height": info["height"],
        "mode": "ProRes4444",
        "pix_fmt": info["pix_fmt"],
        "frames": frames,
        "has_audio": info["has_audio"],
        "alpha_min": first["alpha_min"],
        "alpha_max": first["alpha_max"],
        "first_frame_coverage": first["coverage"],
    }


def _process_video(
    src: Path,
    dst: Path,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
) -> Result:
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg or not resolve_tool("ffprobe"):
        return Result(
            str(src), "failed",
            "FFmpeg and ffprobe are required for video alpha processing",
            detail="dep.missing",
        )
    probed = _probe_video(src, cancelled=cancelled)
    if probed["error"]:
        return Result(
            str(src), "failed", probed["details"], detail=probed["error_type"],
            retryable=probed["retryable"],
        )
    info = probed["data"]
    width, height = info["width"], info["height"]
    frame_bytes = width * height * 3
    tmp = dst.with_name(f"{dst.stem}.part.mov")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)
    except OSError as ex:
        return Result(
            str(src), "failed", f"could not prepare output folder: {ex}",
            detail="save.failed", retryable=True,
        )

    dec_cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(src), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]
    enc_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-r", info["fps_str"], "-i", "pipe:0",
    ]
    if info["has_audio"]:
        audio_codec = "copy" if info["audio_codec"] in _MOV_COPY_AUDIO_CODECS else "pcm_s16le"
        enc_cmd.extend(["-i", str(src), "-map", "0:v:0", "-map", "1:a:0", "-c:a", audio_codec])
    else:
        enc_cmd.extend(["-map", "0:v:0", "-an"])
    enc_cmd.extend([
        "-sn", "-dn",
        "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
        str(tmp),
    ])

    dec: subprocess.Popen | None = None
    enc: subprocess.Popen | None = None
    threads: list[threading.Thread] = []
    frame_count = 0
    coverage_sum = 0.0
    tags: list[str] = [opts.method]
    degraded = False
    details = ""
    try:
        dec = subprocess.Popen(
            dec_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(0x08000000 if os.name == "nt" else 0),
            start_new_session=(os.name != "nt"),
        )
        enc = subprocess.Popen(
            enc_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=(0x08000000 if os.name == "nt" else 0),
            start_new_session=(os.name != "nt"),
        )
        assert dec.stdout is not None and dec.stderr is not None
        assert enc.stdin is not None and enc.stderr is not None
        dec_thread, dec_tail = _start_stderr_drain(dec.stderr)
        enc_thread, enc_tail = _start_stderr_drain(enc.stderr)
        threads = [dec_thread, enc_thread]

        while True:
            _cancelled(cancelled, "alpha-video-frame", src)
            raw = _read_exact(dec.stdout, frame_bytes)
            if not raw:
                break
            if len(raw) != frame_bytes:
                raise OSError(f"truncated video frame ({len(raw)} of {frame_bytes} bytes)")
            rgb = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            mm = _matte_rgb(rgb, opts, cancelled=cancelled)
            if mm["error"]:
                _stop_video_procs([p for p in (dec, enc) if p is not None], threads)
                tmp.unlink(missing_ok=True)
                return Result(
                    str(src), "failed", mm["details"], detail=mm["error_type"],
                    degraded=mm["degraded"], retryable=mm["retryable"],
                )
            if mm["degraded"]:
                degraded = True
            if mm["details"] and not details:
                details = mm["details"]
            rgba = np.dstack([rgb, mm["data"]])
            rgba, tags = _apply_post_ops(rgba, opts, src, cancelled=cancelled)
            coverage_sum += alpha_coverage(rgba)
            frame_count += 1
            _cancelled(cancelled, "alpha-video-encode", src)
            try:
                enc.stdin.write(np.ascontiguousarray(rgba).tobytes())
            except BrokenPipeError as ex:
                raise OSError(f"encoder pipe closed early: {ex}") from ex

        enc.stdin.close()
        dec.stdout.close()
        dec_rc = dec.wait()
        enc_rc = enc.wait()
        for thread in threads:
            thread.join(timeout=2.0)
        dec.stderr.close()
        enc.stderr.close()
        if dec_rc != 0:
            msg = bytes(dec_tail).decode("utf-8", errors="replace").strip()[-300:]
            raise OSError(f"ffmpeg decode failed: {msg or f'exit {dec_rc}'}")
        if enc_rc != 0:
            msg = bytes(enc_tail).decode("utf-8", errors="replace").strip()[-300:]
            raise OSError(f"ffmpeg prores encode failed: {msg or f'exit {enc_rc}'}")
        if frame_count == 0:
            tmp.unlink(missing_ok=True)
            return Result(
                str(src), "failed", f"{src.name} decoded zero frames",
                detail="video.empty",
            )
        mean_coverage = coverage_sum / frame_count
        if mean_coverage < opts.min_coverage:
            tmp.unlink(missing_ok=True)
            return Result(
                str(src), "skipped",
                f"kept only {mean_coverage*100:.1f}% — likely a bad key, not saved",
                coverage=mean_coverage, out_path=str(dst),
                detail="empty-matte", degraded=degraded,
            )
        artifact = _video_artifact(
            tmp,
            expected_size=(width, height),
            expected_frames=frame_count,
            expected_audio=info["has_audio"],
            cancelled=cancelled,
        )
        _cancelled(cancelled, "alpha-output-commit", dst)
        tmp.replace(dst)
        if dst.stat().st_size != artifact["bytes"]:
            raise OSError("committed output size changed")
        artifact["path"] = str(dst)
    except CommandCancelled:
        _stop_video_procs([p for p in (dec, enc) if p is not None], threads)
        tmp.unlink(missing_ok=True)
        raise
    except Exception as ex:
        _stop_video_procs([p for p in (dec, enc) if p is not None], threads)
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

    reason = f"background removed ({frame_count} frames)"
    if details:
        reason += f"; {details}"
    return Result(
        str(src), "cut", reason, coverage=mean_coverage,
        out_path=str(dst), detail="+".join(tags), artifact=artifact,
        degraded=degraded,
    )


def process(
    path: str | Path,
    opts: AlphaOptions,
    cancelled: Cancelled = None,
) -> Result:
    """key -> [despill] -> [decontaminate] -> [defringe] -> [premultiply] -> save RGBA PNG or ProRes 4444 MOV."""
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

    if is_video_path(src):
        return _process_video(src, dst, opts, cancelled=cancelled)

    rb = remove_background(src, opts, cancelled)
    if rb["error"]:
        return Result(
            str(src), "failed", rb["details"], detail=rb["error_type"],
            degraded=rb["degraded"], retryable=rb["retryable"],
        )
    rgba: np.ndarray = rb["data"]
    rgba, tags = _apply_post_ops(rgba, opts, src, cancelled=cancelled)

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
        if is_video_path(Path(result.src)):
            actual = _video_artifact(
                expected,
                expected_size=(int(record["width"]), int(record["height"])),
                expected_frames=int(record["frames"]),
                expected_audio=bool(record["has_audio"]),
                cancelled=cancelled,
            )
            return (
                actual == record
                and math.isfinite(result.coverage)
                and normalized.min_coverage <= result.coverage <= 1.0
            )
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

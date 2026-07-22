"""Clean Cutout engine — remove a photo's background and clean the edge fringe.

Pure logic, no UI, no global state. Cross-platform.

The subject matte runs on **ONNX Runtime directly** (the u2net model) by default —
a lean stack (onnxruntime + numpy + Pillow, ~90 MB). This replaces the previous
`rembg` dependency, whose optional alpha-matting path dragged in
pymatting→numba→llvmlite/scipy/scikit-image (~237 MB) for a minor edge feature.
The direct pipeline was validated to match rembg's matte (mean |Δalpha| ≈ 0.3/255,
99.97% foreground-decision agreement). rembg is still selectable as an optional
`backend="rembg"` for its extra models / alpha-matting — installed only if wanted.

Edge softness that alpha-matting used to provide is handled by our own
background-agnostic `defringe` (erode + feather), lifted from RupayanFlow's keyer.

Errors are values: every fallible call returns the standard envelope
{error, error_type, retryable, degraded, details, data}. See CodingPrinciples.md.

Public interface:
    remove_background(path, model, backend, ...) -> envelope(RGBA ndarray)
    despill(rgb) / defringe(rgba, ...) / alpha_coverage(rgba)   (pure edge math)
    process(path, opts)                          -> Result      (orchestrates + saves)
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

from toolbox.engine_common import IMAGE_EXTS, ok as _ok, err as _err

# ONNX models supported by the direct backend (same 320x320 preprocessing).
# url + md5 are the canonical rembg release assets; existing ~/.u2net downloads
# are reused as-is (no re-download).
_ONNX_MODELS = {
    "u2net": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
              "60024c5c889badc19c04ad937298a77b"),
    "u2netp": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
               "8e83ca70e441ab06c318d82300c84806"),
}
MODELS = tuple(_ONNX_MODELS)                     # what the panel offers by default
REMBG_MODELS = ("u2net", "u2netp", "isnet-general-use", "birefnet-general")

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


# ---------------------------------------------------------------------------
# 1. background removal
# ---------------------------------------------------------------------------

_SESSIONS: dict[str, Any] = {}   # one inference session per model, reused across a batch


def _model_dirs() -> list[Path]:
    """Where a model .onnx may live, in priority order."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):                # a bundled models/ next to the exe
        dirs.append(Path(sys.executable).parent / "models")
    dirs.append(Path.home() / ".u2net")             # rembg's convention — reuse its downloads
    dirs.append(Path.home() / ".cache" / "kstoolbox" / "models")
    return dirs


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_model(model: str) -> dict:
    """Locate the model .onnx, downloading it once if absent. Envelope(path).
    A first-use download is announced via `degraded`/`details`, never silent."""
    fname = f"{model}.onnx"
    for d in _model_dirs():
        p = d / fname
        if p.is_file():
            return _ok(p)                            # reuse an existing (already-validated) file
    if model not in _ONNX_MODELS:
        return _err("model.unknown", f"no download known for model '{model}'")
    url, md5 = _ONNX_MODELS[model]
    dst = Path.home() / ".u2net" / fname
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".onnx.part")
    try:
        urllib.request.urlretrieve(url, tmp)        # one-time model fetch (~4-176 MB)
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return _err("model.download", f"could not download the {model} model: {ex}", retryable=True)
    if md5 and _md5(tmp) != md5:
        tmp.unlink(missing_ok=True)
        return _err("model.corrupt", f"{model} model failed its checksum — download may be corrupt")
    tmp.replace(dst)
    return _ok(dst, degraded=True, details=f"downloaded {model} model to {dst}")


def _onnx_session(model_path: Path):
    key = str(model_path)
    if key not in _SESSIONS:
        import onnxruntime as ort
        _SESSIONS[key] = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    return _SESSIONS[key]


def _onnx_matte(img_rgb, session) -> np.ndarray:
    """u2net matte for a PIL RGB image → HxW uint8 alpha. The validated pipeline:
    resize 320, normalize, run, min-max the mask, resize back."""
    from PIL import Image
    w, h = img_rgb.size
    small = img_rgb.resize((320, 320), Image.BILINEAR)
    arr = (np.asarray(small, dtype=np.float32) / 255.0 - _MEAN) / _STD
    arr = arr.transpose(2, 0, 1)[None].astype(np.float32)
    name = session.get_inputs()[0].name
    mask = session.run(None, {name: arr})[0][0, 0]
    mi, ma = float(mask.min()), float(mask.max())
    mask = (mask - mi) / (ma - mi + 1e-8)
    up = Image.fromarray((mask * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)
    return np.asarray(up, dtype=np.uint8)


def _rembg_matte(p: Path, model: str, alpha_matting: bool) -> dict:
    """Optional fallback backend: rembg (its extra models + alpha-matting).
    Imported lazily — only needed if the user opts into backend='rembg'."""
    try:
        from rembg import remove, new_session
        from PIL import Image
    except ImportError as ex:
        return _err("dep.missing", f"backend='rembg' needs rembg ({ex.name}) — pip install rembg")
    try:
        src = Image.open(p).convert("RGB")
    except Exception as ex:
        return _err("image.unreadable", f"cannot read {p.name}: {ex}")
    if model not in _SESSIONS:
        _SESSIONS[model] = new_session(model)
    try:
        cut = remove(src, session=_SESSIONS[model], alpha_matting=alpha_matting, post_process_mask=True)
    except Exception as ex:
        return _err("rembg.failed", f"rembg failed on {p.name}: {ex}", retryable=True)
    return _ok(np.asarray(cut.convert("RGBA"), dtype=np.uint8))


def remove_background(path: str | Path, model: str = "u2net", backend: str = "onnx",
                      alpha_matting: bool = False) -> dict:
    """Cut RGBA (HxWx4 uint8) for one image. Envelope out.

    backend 'onnx' (default): direct onnxruntime u2net — lean, no rembg.
    backend 'rembg' (opt-in): rembg's models + alpha-matting (needs rembg installed).
    Missing deps yield a clear dep.missing, never a crash.
    """
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    if backend == "rembg":
        return _rembg_matte(p, model, alpha_matting)

    try:
        import onnxruntime  # noqa: F401
        from PIL import Image
    except ImportError as ex:
        return _err("dep.missing", f"Clean Cutout needs onnxruntime + Pillow ({ex.name}) — "
                    f"pip install onnxruntime pillow numpy")
    try:
        src = Image.open(p).convert("RGB")
    except Exception as ex:                          # PIL raises many types on bad images
        return _err("image.unreadable", f"cannot read {p.name}: {ex}")
    mp = _ensure_model(model)
    if mp["error"]:
        return mp
    try:
        alpha = _onnx_matte(src, _onnx_session(mp["data"]))
    except Exception as ex:
        return _err("onnx.failed", f"matte inference failed on {p.name}: {ex}", retryable=True)
    rgba = np.dstack([np.asarray(src, dtype=np.uint8), alpha])
    return _ok(rgba, degraded=mp["degraded"], details=mp["details"])


# ---------------------------------------------------------------------------
# 2. edge cleanup — pure numpy/PIL (the "fringe fix"; trivially testable)
# ---------------------------------------------------------------------------

def despill(rgb: np.ndarray) -> np.ndarray:
    """Clamp green to max(red, blue) per pixel — kills green tint from a green
    screen bleeding into edges. Green-source only (opt-in). Returns float32."""
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    g2 = np.minimum(g, np.maximum(r, b))
    return np.stack([r, g2, b], -1)


def defringe(rgba: np.ndarray, erode_px: int = 1, feather: float = 0.6) -> np.ndarray:
    """Erode the alpha edge inward by `erode_px` then feather — removes the 1-px
    background-coloured halo the matte leaves. Background-agnostic. RGBA in/out.
    """
    from PIL import Image, ImageFilter

    alpha = Image.fromarray(rgba[:, :, 3])
    if erode_px > 0:
        alpha = alpha.filter(ImageFilter.MinFilter(2 * erode_px + 1))
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out = rgba.copy()
    out[:, :, 3] = np.asarray(alpha, dtype=np.uint8)
    return out


def alpha_coverage(rgba: np.ndarray) -> float:
    """Fraction of pixels that are meaningfully opaque — a sanity number. Near 0
    means the cut removed almost everything (likely a bad matte)."""
    a = rgba[:, :, 3].astype(np.float32) / 255.0
    return float((a > 0.5).mean())


# ---------------------------------------------------------------------------
# 3. process — the whole pipeline for one image
# ---------------------------------------------------------------------------

@dataclass
class CutoutOptions:
    out_root: Path | None = None      # output root; None = <src>/cutouts beside source
    input_root: Path | None = None    # mirror mode recreates paths relative to this root
    mirror: bool = False              # rebuild input folder structure under out_root
    model: str = "u2net"              # onnx model (u2net | u2netp)
    backend: str = "onnx"             # "onnx" (lean, default) | "rembg" (opt-in, extra models)
    alpha_matting: bool = False       # rembg backend only: softer hair edges (extra CPU)
    do_defringe: bool = True          # erode+feather the alpha edge (our soft-edge path)
    erode_px: int = 1                 # halo erosion width
    feather: float = 0.6              # edge feather radius
    green_despill: bool = False       # opt-in: green-screen despill before keying
    min_coverage: float = 0.01        # reject a matte that kept <1% (all-empty result)
    dry_run: bool = True              # default safe: report planned outputs, write nothing


def plan_output(src: Path, opts: CutoutOptions) -> Path:
    """Where this image's cutout PNG goes. Mirror mode preserves the input subtree
    under out_root; else flat under out_root; else a `cutouts` folder beside src."""
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / (src.stem + ".png")
            except ValueError:
                pass  # source not under input_root — fall back to flat
        return root / (src.stem + ".png")
    return src.parent / "cutouts" / (src.stem + ".png")


@dataclass
class Result:
    src: str
    action: str                       # cut | skipped | failed | dry-run
    reason: str
    coverage: float = 0.0
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: CutoutOptions) -> Result:
    """remove bg -> [despill] -> [defringe] -> save RGBA PNG, for one image."""
    src = Path(path)
    dst = plan_output(src, opts)

    if opts.dry_run:
        return Result(str(src), "dry-run", "would cut background", out_path=str(dst))

    rb = remove_background(src, opts.model, opts.backend, opts.alpha_matting)
    if rb["error"]:
        return Result(str(src), "failed", rb["details"], detail=rb["error_type"])
    rgba: np.ndarray = rb["data"]

    if opts.green_despill:
        rgba = np.dstack([despill(rgba[:, :, :3]).clip(0, 255).astype(np.uint8),
                          rgba[:, :, 3]])
    if opts.do_defringe:
        rgba = defringe(rgba, opts.erode_px, opts.feather)

    coverage = alpha_coverage(rgba)
    if coverage < opts.min_coverage:
        return Result(str(src), "skipped", f"matte kept only {coverage*100:.1f}% — "
                      f"likely a failed cut, not saved", coverage=coverage, detail="empty-matte")

    try:
        from PIL import Image
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")  # atomic write
        Image.fromarray(rgba, "RGBA").save(tmp)
        tmp.replace(dst)
    except Exception as ex:
        return Result(str(src), "failed", f"could not save {dst.name}: {ex}", detail="save.failed")

    return Result(str(src), "cut", "background removed", coverage=coverage,
                  out_path=str(dst),
                  detail=("despill+defringe" if opts.green_despill and opts.do_defringe
                          else "defringe" if opts.do_defringe else "raw matte"))

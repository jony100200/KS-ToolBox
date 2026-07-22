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
    ai          - u2net matte (opt-in; downloads the model on first use)

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
import sys
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

from toolbox.engine_common import IMAGE_EXTS, ok as _ok, err as _err

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


def edge_flood_alpha(rgb: np.ndarray, tol: float = 30.0) -> np.ndarray:
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
        dil = marker.copy()
        dil[1:, :] |= marker[:-1, :]; dil[:-1, :] |= marker[1:, :]
        dil[:, 1:] |= marker[:, :-1]; dil[:, :-1] |= marker[:, 1:]
        new = dil & like
        if np.array_equal(new, marker):
            break
        marker = new
    return np.where(marker, 0, 255).astype(np.uint8)


def matte(rgb: np.ndarray, opts: "AlphaOptions") -> np.ndarray:
    """Deterministic alpha for one RGB image by the chosen method. Pure."""
    if opts.method == "chroma":
        return chroma_alpha(rgb, _hex_to_rgb(opts.key_color), opts.tolerance, opts.feather_band)
    if opts.method == "solid":
        return chroma_alpha(rgb, detect_bg_color(rgb), max(20.0, opts.tolerance * 0.4), opts.feather_band)
    if opts.method == "edge_flood":
        return edge_flood_alpha(rgb, opts.tolerance)
    raise ValueError(f"not a deterministic method: {opts.method}")


# --- optional AI matte --------------------------------------------------------

def _model_dirs() -> list[Path]:
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / "models")
    dirs.append(Path.home() / ".u2net")
    dirs.append(Path.home() / ".cache" / "kstoolbox" / "models")
    return dirs


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_model(model: str) -> dict:
    fname = f"{model}.onnx"
    for d in _model_dirs():
        if (d / fname).is_file():
            return _ok(d / fname)
    if model not in _ONNX_MODELS:
        return _err("model.unknown", f"no download known for model '{model}'")
    url, md5 = _ONNX_MODELS[model]
    dst = Path.home() / ".u2net" / fname
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".onnx.part")
    try:
        urllib.request.urlretrieve(url, tmp)
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return _err("model.download", f"could not download the {model} model: {ex}", retryable=True)
    if md5 and _md5(tmp) != md5:
        tmp.unlink(missing_ok=True)
        return _err("model.corrupt", f"{model} model failed its checksum")
    tmp.replace(dst)
    return _ok(dst, degraded=True, details=f"downloaded {model} model")


def _ai_matte(img_rgb, model: str) -> dict:
    try:
        import onnxruntime as ort
        from PIL import Image
    except ImportError as ex:
        return _err("dep.missing", f"the AI method needs onnxruntime ({ex.name}) — pip install onnxruntime")
    mp = _ensure_model(model)
    if mp["error"]:
        return mp
    key = str(mp["data"])
    if key not in _SESSIONS:
        _SESSIONS[key] = ort.InferenceSession(key, providers=["CPUExecutionProvider"])
    sess = _SESSIONS[key]
    w, h = img_rgb.size
    small = img_rgb.resize((320, 320), Image.BILINEAR)
    arr = ((np.asarray(small, dtype=np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[None]
    mask = sess.run(None, {sess.get_inputs()[0].name: arr.astype(np.float32)})[0][0, 0]
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
    do_defringe: bool = True
    erode_px: int = 1
    feather: float = 0.6
    green_despill: bool = False
    do_premultiply: bool = False
    min_coverage: float = 0.005
    dry_run: bool = True


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


def remove_background(path: str | Path, opts: AlphaOptions) -> dict:
    """RGBA cut for one image by the chosen method. Envelope out."""
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    try:
        from PIL import Image
    except ImportError:
        return _err("dep.missing", "Alpha Doctor needs Pillow + numpy — pip install pillow numpy")
    try:
        src = Image.open(p).convert("RGB")
    except Exception as ex:
        return _err("image.unreadable", f"cannot read {p.name}: {ex}")
    rgb = np.asarray(src, dtype=np.uint8)

    degraded, details = False, ""
    if opts.method == "ai":
        am = _ai_matte(src, opts.model)
        if am["error"]:
            return am
        alpha = am["data"]; degraded, details = am["degraded"], am["details"]
    else:
        try:
            alpha = matte(rgb, opts)
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

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: AlphaOptions) -> Result:
    """key -> [despill] -> [defringe] -> [premultiply] -> save RGBA PNG."""
    src = Path(path)
    dst = plan_output(src, opts)
    if opts.dry_run:
        return Result(str(src), "dry-run", f"would cut ({opts.method})", out_path=str(dst))

    rb = remove_background(src, opts)
    if rb["error"]:
        return Result(str(src), "failed", rb["details"], detail=rb["error_type"])
    rgba: np.ndarray = rb["data"]

    if opts.green_despill:
        rgba = np.dstack([despill(rgba[:, :, :3]).clip(0, 255).astype(np.uint8), rgba[:, :, 3]])
    if opts.do_defringe:
        rgba = defringe(rgba, opts.erode_px, opts.feather)
    if opts.do_premultiply:
        rgba = premultiply(rgba)

    coverage = alpha_coverage(rgba)
    if coverage < opts.min_coverage:
        return Result(str(src), "skipped", f"kept only {coverage*100:.1f}% — likely a bad key, not saved",
                      coverage=coverage, detail="empty-matte")

    try:
        from PIL import Image
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
        Image.fromarray(rgba, "RGBA").save(tmp)
        tmp.replace(dst)
    except Exception as ex:
        return Result(str(src), "failed", f"could not save {dst.name}: {ex}", detail="save.failed")

    tags = [opts.method]
    if opts.green_despill: tags.append("despill")
    if opts.do_defringe: tags.append("defringe")
    if opts.do_premultiply: tags.append("premult")
    return Result(str(src), "cut", "background removed", coverage=coverage,
                  out_path=str(dst), detail="+".join(tags))

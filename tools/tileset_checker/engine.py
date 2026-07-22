"""Tileset Checker engine — deterministic seamless-tile analysis + preview.

Pure logic, no UI, no global state, no AI, no network. numpy + Pillow only, both
imported lazily so discovery/the sidebar work before they're installed.

What it does, for one texture:
    1. Seam score   — compare opposite wrap edges (left↔right, top↔bottom) with
                      mean-abs-diff + RMSE, normalized 0..1 (1 = perfectly
                      seamless). Reported per-axis and overall.
    2. Offset-wrap  — roll the image by half in X and Y so any seam lands in the
                      centre where it's visible (the classic "offset filter").
    3. Tile preview — an N×N tiled montage to eyeball repetition/seams.
    4. Edge-diff    — a compact heatmap strip of the X- and Y-seam discontinuity.

Errors are values: the standard envelope-style Result where a call can fail.
The scoring/rolling/tiling math itself is pure. See CodingPrinciples.md.

Lifted from RupayanFlow `seamless/tile_tools.py` (offset_wrap / tile_preview /
seam_score) and ChobiEngine `seamless_checker/tile_metrics.py` (axis edge-diff /
RMSE), keeping the numpy+Pillow path and dropping the cv2 + job-wrapper layers
(cv2 is not required — the seam math is pure numpy).

Public interface:
    offset_wrap(arr)            -> arr            (pure — roll by half)
    seam_score(arr)             -> {x, y, overall}(pure — 0..1, 1 = seamless)
    tile_preview(img, n)        -> PIL.Image      (pure — N×N montage)
    edge_diff_strip(arr, band)  -> PIL.Image      (pure — seam heatmap)
    process(path, opts)         -> Result         (load -> score -> write previews)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS

# 8-bit range — inputs are loaded and converted to RGB uint8 before scoring, so
# edge differences live in 0..255 and normalize cleanly against this.
_RANGE = 255.0
_STRIP_LEN = 256           # edge-diff heatmap width (both seams resampled to this)
_STRIP_BAND = 18           # heatmap row height (px) per axis


# ---------------------------------------------------------------------------
# pure seam math — no I/O
# ---------------------------------------------------------------------------

def offset_wrap(arr):
    """Roll the image by half its size in both axes (numpy array in/out).

    Any wrap seam (right↔left edge, bottom↔top edge) moves to the centre cross
    where it's plainly visible. Deterministic. For even dimensions applying this
    twice is an exact identity (roll by W//2 twice == roll by W)."""
    import numpy as np
    a = np.asarray(arr)
    h, w = a.shape[0], a.shape[1]
    return np.roll(np.roll(a, w // 2, axis=1), h // 2, axis=0)


def _edge_pair_score(edge_a, edge_b) -> float:
    """0..1 seamlessness for one opposed edge pair (1 = identical edges)."""
    import numpy as np
    diff = edge_a.astype(np.float64) - edge_b.astype(np.float64)
    mad = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(np.square(diff))))
    discontinuity = ((mad + rmse) / 2.0) / _RANGE
    return float(max(0.0, min(1.0, 1.0 - discontinuity)))


def seam_score(arr) -> dict:
    """Per-axis + overall seamlessness for a texture. Pure.

    x  — right edge vs left edge (how the horizontal wrap seam lines up)
    y  — bottom edge vs top edge  (the vertical wrap seam)
    overall — the worst of the two (an image only tiles seamlessly if BOTH
              seams are clean, so the weaker axis governs the verdict).

    Each is 0..1, 1 = perfectly seamless. Uses mean-abs-diff + RMSE between the
    opposite outermost edges, normalized against the 8-bit range."""
    import numpy as np
    a = np.asarray(arr)
    x = _edge_pair_score(a[:, 0], a[:, -1])     # left column vs right column
    y = _edge_pair_score(a[0, :], a[-1, :])     # top row vs bottom row
    return {"x": x, "y": y, "overall": float(min(x, y))}


def tile_preview(img, n: int = 3):
    """An N×N tiled montage of the image (PIL in/out). Reveals repetition and
    any seam lines when the texture is laid edge-to-edge. Size is n× each way."""
    from PIL import Image
    n = max(1, int(n))
    w, h = img.size
    canvas = Image.new(img.mode, (w * n, h * n))
    for yi in range(n):
        for xi in range(n):
            canvas.paste(img, (xi * w, yi * h))
    return canvas


def _resample_1d(v, length: int):
    """Linearly resample a 1-D signal to `length` samples (pure numpy)."""
    import numpy as np
    n = len(v)
    if n == length:
        return v
    xs = np.linspace(0.0, n - 1, length)
    return np.interp(xs, np.arange(n), v)


def _heat_row(mag, band: int):
    """A `band`-tall RGB heat bar from a 0..255 magnitude vector.
    Colormap: black -> red -> yellow (higher = more mismatch)."""
    import numpy as np
    m = np.clip(np.asarray(mag) / _RANGE, 0.0, 1.0)
    r = np.clip(m * 2.0, 0.0, 1.0)
    g = np.clip(m * 2.0 - 1.0, 0.0, 1.0)
    b = np.zeros_like(m)
    row = (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)   # (L, 3)
    return np.repeat(row[None, :, :], max(1, int(band)), axis=0)     # (band, L, 3)


def edge_diff_strip(arr, band: int = _STRIP_BAND):
    """A compact heatmap strip visualizing the discontinuity at the wrap edges.

    Two stacked heat bars (numpy -> PIL): the top bar is the X-seam mismatch
    (|left col − right col| per row) and the bottom bar is the Y-seam mismatch
    (|top row − bottom row| per column), each resampled to a common width. Bright
    = large discontinuity where the texture would visibly break when tiled."""
    import numpy as np
    from PIL import Image
    a = np.asarray(arr)
    a = a[..., :3] if a.ndim == 3 else a
    a = a.astype(np.float64)
    dx = np.abs(a[:, 0] - a[:, -1])     # X-seam per row
    dy = np.abs(a[0, :] - a[-1, :])     # Y-seam per column
    if dx.ndim == 2:
        dx = dx.mean(axis=1)
    if dy.ndim == 2:
        dy = dy.mean(axis=1)
    top = _heat_row(_resample_1d(dx, _STRIP_LEN), band)
    bot = _heat_row(_resample_1d(dy, _STRIP_LEN), band)
    gap = np.full((3, _STRIP_LEN, 3), 28, np.uint8)     # thin separator line
    return Image.fromarray(np.vstack([top, gap, bot]), "RGB")


# ---------------------------------------------------------------------------
# options + result
# ---------------------------------------------------------------------------

@dataclass
class TileOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    tile_n: int = 3                   # N for the N×N tile montage
    make_offset: bool = True          # write the wrap-offset preview
    make_tile: bool = True            # write the N×N tile montage
    make_heatmap: bool = True         # write the edge-diff heatmap strip
    dry_run: bool = True              # preview: score + list writes, write nothing


@dataclass
class Result:
    src: str
    action: str                       # checked | dry-run | failed
    reason: str = ""
    x: float = 0.0
    y: float = 0.0
    overall: float = 0.0
    outputs: list[str] = field(default_factory=list)   # written (or would-write) PNGs
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# process — one file
# ---------------------------------------------------------------------------

def _out_base(src: Path, opts: TileOptions) -> Path:
    """Stem path the previews hang off (`<base>_offset.png`, etc.). Mirror
    preserves the input subtree under out_root; else flat under out_root; else a
    `tileset_check` folder beside the source."""
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / src.stem
            except ValueError:
                pass
        return root / src.stem
    return src.parent / "tileset_check" / src.stem


def _planned(base: Path, opts: TileOptions) -> list[str]:
    out = []
    if opts.make_offset:
        out.append(str(base.parent / f"{base.name}_offset.png"))
    if opts.make_tile:
        out.append(str(base.parent / f"{base.name}_tile.png"))
    if opts.make_heatmap:
        out.append(str(base.parent / f"{base.name}_heatmap.png"))
    return out


def _atomic_save(img, dst: Path) -> str:
    """Write via a `.part` temp then os-replace into place (never a half file)."""
    dst = Path(dst)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    img.save(tmp)
    tmp.replace(dst)
    return str(dst)


def process(path: str | Path, opts: TileOptions) -> Result:
    """load -> score seams -> (optionally) write previews, for one texture."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    try:
        import numpy as np
        from PIL import Image
    except ImportError as ex:
        return Result(str(src), "failed",
                      f"numpy + Pillow required — pip install numpy pillow ({ex})",
                      detail="dep.missing")

    # A `with` block releases the source handle on every path (matters for
    # large batches on Windows, where a held handle blocks the file).
    try:
        with Image.open(src) as im:
            rgb = im.convert("RGB")                  # deterministic 8-bit RGB for scoring
            arr = np.asarray(rgb)
            score = seam_score(arr)
            base = _out_base(src, opts)
            summary = (f"seam x={score['x']:.3f} y={score['y']:.3f} "
                       f"overall={score['overall']:.3f}")

            if opts.dry_run:
                return Result(str(src), "dry-run", summary, x=score["x"], y=score["y"],
                              overall=score["overall"], outputs=_planned(base, opts),
                              detail="preview")

            base.parent.mkdir(parents=True, exist_ok=True)
            written: list[str] = []
            if opts.make_offset:
                written.append(_atomic_save(Image.fromarray(offset_wrap(arr)),
                                            base.parent / f"{base.name}_offset.png"))
            if opts.make_tile:
                written.append(_atomic_save(tile_preview(rgb, opts.tile_n),
                                            base.parent / f"{base.name}_tile.png"))
            if opts.make_heatmap:
                written.append(_atomic_save(edge_diff_strip(arr),
                                            base.parent / f"{base.name}_heatmap.png"))
    except Exception as ex:                          # PIL/numpy raise many types on bad images
        return Result(str(src), "failed",
                      f"tileset check failed on {src.name}: {ex}", detail="check.failed")

    return Result(str(src), "checked", summary, x=score["x"], y=score["y"],
                  overall=score["overall"], outputs=written, detail="checked")

"""Sprite Viewer engine — headless slicing, frame loading, and export.

Pure logic, no CustomTkinter, no global state, Pillow imported lazily (so the
sidebar/discovery stay alive on a machine that hasn't installed Pillow yet — the
missing dep is announced when the tool is opened). Fallible IO returns the shared
error-envelope; the in-memory transforms (slicing, detection) return plain lists
because they can't fail on a valid image.

Lifted (deterministic, numpy dropped, job-shells dropped) from ChobiEngine:
  - grid slicing            <- sprite_processor/services/autosprite_service.py
                               (`split_sprite_sheet` cell/row/col math)
  - alpha connected-comp    <- sprite_processor/services/splitter_service.py
                               (`find_sprite_boxes` — alpha threshold, 3x3 dilate,
                               BFS flood-fill, row-banded sort). The nested-Python
                               dilation is replaced with a C-speed PIL MaxFilter.

Public interface:
    grid_boxes / cell_boxes        -> list[(l,t,r,b)]  (PIL crop convention)
    slice_grid / slice_by_cell     -> list[PIL.Image]
    detect_sprites                 -> list[(l,t,r,b)]  (auto sprite boxes)
    load_frames / load_folder      -> envelope[list[PIL.Image]]
    frame_count / describe         -> int / SpriteMeta
    export_gif / export_meta_json  -> envelope[str]
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import ok, err, IMAGE_EXTS

# Extensions Pillow can page through frame-by-frame (n_frames/seek).
ANIMATION_EXTS = {".gif", ".webp", ".apng", ".png"}


# --- grid slicing -------------------------------------------------------------

def grid_boxes(w: int, h: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    """Cell boxes for an rows x cols grid over a w x h image (PIL crop convention:
    right/bottom exclusive). Cell size is floor(w/cols) x floor(h/rows) so a sheet
    that doesn't divide evenly drops the leftover strip rather than emitting a
    ragged final cell."""
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    cw = max(1, w // cols)
    ch = max(1, h // rows)
    boxes: list[tuple[int, int, int, int]] = []
    for r in range(rows):
        for c in range(cols):
            left, top = c * cw, r * ch
            right, bottom = left + cw, top + ch
            if right <= w and bottom <= h:
                boxes.append((left, top, right, bottom))
    return boxes


def cell_boxes(w: int, h: int, cw: int, ch: int) -> list[tuple[int, int, int, int]]:
    """Cell boxes tiling a w x h image with fixed cw x ch cells (row-major).
    Partial trailing cells (past the right/bottom edge) are dropped."""
    cw = max(1, int(cw))
    ch = max(1, int(ch))
    boxes: list[tuple[int, int, int, int]] = []
    for top in range(0, h - ch + 1, ch):
        for left in range(0, w - cw + 1, cw):
            boxes.append((left, top, left + cw, top + ch))
    return boxes


def slice_grid(img, rows: int, cols: int) -> list:
    """Slice an image into rows x cols equal frames (list of PIL.Image, RGBA)."""
    src = img.convert("RGBA")
    return [src.crop(b) for b in grid_boxes(src.width, src.height, rows, cols)]


def slice_by_cell(img, cw: int, ch: int) -> list:
    """Slice an image into fixed cw x ch cells (list of PIL.Image, RGBA)."""
    src = img.convert("RGBA")
    return [src.crop(b) for b in cell_boxes(src.width, src.height, cw, ch)]


# --- auto sprite detection (alpha connected components) -----------------------

def detect_sprites(img, alpha_thresh: int = 32, *, min_area_frac: float = 0.0008,
                   min_area_floor: int = 96) -> list[tuple[int, int, int, int]]:
    """Find discrete sprites on a transparent sheet by alpha connected-components.

    Threshold alpha -> 3x3 dilate (bridges anti-aliased gaps) -> BFS flood-fill
    each blob -> keep blobs above `min_area` -> sort into reading order (row bands,
    then left-to-right). Returns boxes as (min_x, min_y, max_x, max_y) with the
    right/bottom EXCLUSIVE (ready for `img.crop`). Pillow only — no numpy.
    """
    from PIL import ImageFilter

    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    w, h = rgba.size
    total = w * h
    if total == 0:
        return []

    alpha = rgba.split()[3]
    thresh = max(0, min(255, int(alpha_thresh)))
    mask_img = alpha.point(lambda p: 255 if p >= thresh else 0, mode="L")
    # 3x3 dilation at C speed (was a nested-Python triple loop in the source).
    dilated = mask_img.filter(ImageFilter.MaxFilter(3)).tobytes()

    visited = bytearray(total)
    queue = [0] * total
    min_area = max(min_area_floor, int(total * min_area_frac))
    boxes: list[tuple[int, int, int, int]] = []

    for start in range(total):
        if not dilated[start] or visited[start]:
            continue
        head = tail = 0
        queue[tail] = start
        tail += 1
        visited[start] = 1
        min_x, min_y, max_x, max_y, area = w, h, -1, -1, 0
        while head < tail:
            p = queue[head]
            head += 1
            x, y = p % w, p // w
            area += 1
            if x < min_x: min_x = x
            if x > max_x: max_x = x
            if y < min_y: min_y = y
            if y > max_y: max_y = y
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    q = ny * w + nx
                    if dilated[q] and not visited[q]:
                        visited[q] = 1
                        queue[tail] = q
                        tail += 1
        if area >= min_area:
            # +1 on max to make right/bottom exclusive (PIL crop convention).
            boxes.append((min_x, min_y, max_x + 1, max_y + 1))

    row_tol = max(24, int(h * 0.04))
    boxes.sort(key=lambda b: (b[1] // row_tol, b[0]))
    return boxes


def crop_boxes(img, boxes) -> list:
    """Crop a list of (l,t,r,b) boxes out of an image (RGBA frames)."""
    src = img.convert("RGBA")
    return [src.crop(b) for b in boxes]


# --- frame loading (fallible IO -> envelope) ----------------------------------

def load_frames(path) -> dict:
    """Load frames from ONE file. Animated GIF/WebP/APNG page through n_frames via
    seek; a still image yields a single frame. Envelope: data = list[PIL.Image]
    (RGBA). Folders are the caller's job — see `load_folder`."""
    p = Path(path)
    if not p.is_file():
        return err("file.missing", f"not a file: {p}")
    try:
        from PIL import Image
        frames = []
        with Image.open(p) as im:
            n = int(getattr(im, "n_frames", 1))
            for i in range(n):
                im.seek(i)
                frames.append(im.convert("RGBA").copy())
        if not frames:
            return err("load.empty", f"no frames decoded from {p.name}")
        return ok(frames, details=f"{len(frames)} frame(s) from {p.name}")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except Exception as ex:                 # PIL raises many types on bad input
        return err("load.failed", f"could not load {p.name}: {ex}")


def _natural_key(name: str):
    """Sort key so frame_2 < frame_10 (digit runs compared numerically)."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def load_folder(folder) -> dict:
    """Load a numbered image sequence from a folder (natural-sorted). Envelope:
    data = list[PIL.Image] (RGBA)."""
    d = Path(folder)
    if not d.is_dir():
        return err("dir.missing", f"not a folder: {d}")
    files = sorted((f for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS),
                   key=lambda f: _natural_key(f.name))
    if not files:
        return err("input.empty", f"no images in {d}")
    try:
        from PIL import Image
        frames = []
        for f in files:
            with Image.open(f) as im:
                frames.append(im.convert("RGBA").copy())
        return ok(frames, details=f"{len(frames)} frame(s) from {d.name}/")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except Exception as ex:
        return err("load.failed", f"could not read a frame in {d}: {ex}")


# --- metadata -----------------------------------------------------------------

@dataclass
class SpriteMeta:
    """A describable summary of a loaded sprite source."""
    source: str
    kind: str                       # sheet-grid | sheet-cell | auto | animation | folder | image
    frame_count: int
    frame_size: tuple[int, int]     # (w, h) of frame 0
    fps: int = 12
    boxes: list = field(default_factory=list)   # slice boxes on the sheet, if any

    def to_dict(self) -> dict:
        return asdict(self)


def frame_count(frames) -> int:
    """How many frames — trivial helper so callers don't reach into len()."""
    return len(frames)


def describe(frames, source: str, kind: str, fps: int = 12, boxes=None) -> SpriteMeta:
    """Build a SpriteMeta from a loaded frames list (frame 0 sets the size)."""
    size = tuple(frames[0].size) if frames else (0, 0)
    return SpriteMeta(source=str(source), kind=kind, frame_count=len(frames),
                      frame_size=size, fps=int(fps),
                      boxes=[list(b) for b in (boxes or [])])


# --- export (optional, fallible IO -> envelope) -------------------------------

def export_gif(frames, dst, fps: int = 12) -> dict:
    """Write frames as an animated GIF preview. Envelope: data = dst path."""
    if not frames:
        return err("input.empty", "no frames to export")
    d = Path(dst)
    try:
        from PIL import Image  # noqa: F401  (ensures Pillow present before work)
        duration = max(1, int(round(1000.0 / max(1, int(fps)))))
        rgba = [f.convert("RGBA") for f in frames]
        d.parent.mkdir(parents=True, exist_ok=True)
        tmp = d.with_name(f"{d.stem}.part{d.suffix}")     # atomic write
        rgba[0].save(tmp, "GIF", save_all=True, append_images=rgba[1:],
                     duration=duration, loop=0, disposal=2, optimize=False)
        tmp.replace(d)
        return ok(str(d), details=f"{len(frames)} frames @ {fps}fps -> {d.name}")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except Exception as ex:
        return err("export.failed", f"could not write GIF {d.name}: {ex}")


def export_meta_json(meta, dst) -> dict:
    """Write slice metadata (a SpriteMeta or plain dict) as pretty JSON. Envelope:
    data = dst path."""
    import json
    payload = meta.to_dict() if isinstance(meta, SpriteMeta) else dict(meta)
    d = Path(dst)
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        tmp = d.with_name(f"{d.stem}.part{d.suffix}")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d)
        return ok(str(d), details=f"metadata -> {d.name}")
    except Exception as ex:
        return err("export.failed", f"could not write JSON {d.name}: {ex}")

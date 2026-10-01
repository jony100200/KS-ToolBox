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

import json
import os
import tempfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import ok, err, IMAGE_EXTS

# The viewer keeps decoded RGBA frames resident for responsive scrubbing. Bound
# both count and decoded pixels so a malformed animation/folder cannot exhaust
# RAM. 128M pixels is at most 512 MiB of RGBA frame data, before Pillow overhead.
MAX_FRAME_COUNT = 10_000
MAX_DECODED_PIXELS = 128 * 1024 * 1024
MAX_GRID_FRAMES = 10_000
MAX_FOLDER_ENTRIES = 100_000

Cancelled = Callable[[], bool] | None


class SpriteOperationCancelled(RuntimeError):
    """Raised at deterministic cancellation boundaries inside pure operations."""


def _check_cancelled(cancelled: Cancelled) -> None:
    if cancelled is not None and cancelled():
        raise SpriteOperationCancelled("sprite operation cancelled")


def _close_images(images: list) -> None:
    for image in images:
        image.close()


def _validate_budget(max_frames: int, max_decoded_pixels: int) -> tuple[int, int]:
    max_frames = int(max_frames)
    max_decoded_pixels = int(max_decoded_pixels)
    if max_frames < 1 or max_decoded_pixels < 1:
        raise ValueError("frame and decoded-pixel limits must be positive")
    return max_frames, max_decoded_pixels


def _budget_error(count: int, pixels: int, max_frames: int, max_pixels: int) -> dict | None:
    if count > max_frames:
        return err(
            "input.too_many_frames",
            f"source has {count:,} frames; viewer limit is {max_frames:,}",
        )
    if pixels > max_pixels:
        decoded_mib = pixels * 4 / (1024 * 1024)
        limit_mib = max_pixels * 4 / (1024 * 1024)
        return err(
            "input.too_large",
            f"decoded frames need about {decoded_mib:,.0f} MiB RGBA; "
            f"viewer limit is {limit_mib:,.0f} MiB",
        )
    return None


# --- grid slicing -------------------------------------------------------------

def grid_boxes(w: int, h: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    """Cell boxes for an rows x cols grid over a w x h image (PIL crop convention:
    right/bottom exclusive). Cell size is floor(w/cols) x floor(h/rows) so a sheet
    that doesn't divide evenly drops the leftover strip rather than emitting a
    ragged final cell."""
    w, h, rows, cols = int(w), int(h), int(rows), int(cols)
    if w < 1 or h < 1:
        raise ValueError("image dimensions must be positive")
    if rows < 1 or cols < 1:
        raise ValueError("rows and columns must be positive")
    if rows > h or cols > w:
        raise ValueError("rows and columns cannot exceed image dimensions")
    if rows * cols > MAX_GRID_FRAMES:
        raise ValueError(f"grid exceeds the {MAX_GRID_FRAMES:,}-frame viewer limit")
    cw = w // cols
    ch = h // rows
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
    w, h, cw, ch = int(w), int(h), int(cw), int(ch)
    if w < 1 or h < 1:
        raise ValueError("image dimensions must be positive")
    if cw < 1 or ch < 1:
        raise ValueError("cell dimensions must be positive")
    count = (w // cw) * (h // ch)
    if count > MAX_GRID_FRAMES:
        raise ValueError(f"cell grid exceeds the {MAX_GRID_FRAMES:,}-frame viewer limit")
    boxes: list[tuple[int, int, int, int]] = []
    for top in range(0, h - ch + 1, ch):
        for left in range(0, w - cw + 1, cw):
            boxes.append((left, top, left + cw, top + ch))
    return boxes


def slice_grid(img, rows: int, cols: int) -> list:
    """Slice an image into rows x cols equal frames (list of PIL.Image, RGBA)."""
    src = img if img.mode == "RGBA" else img.convert("RGBA")
    return [src.crop(b) for b in grid_boxes(src.width, src.height, rows, cols)]


def slice_by_cell(img, cw: int, ch: int) -> list:
    """Slice an image into fixed cw x ch cells (list of PIL.Image, RGBA)."""
    src = img if img.mode == "RGBA" else img.convert("RGBA")
    return [src.crop(b) for b in cell_boxes(src.width, src.height, cw, ch)]


# --- auto sprite detection (alpha connected components) -----------------------

def detect_sprites(
    img,
    alpha_thresh: int = 32,
    *,
    min_area_frac: float = 0.0008,
    min_area_floor: int = 96,
    cancelled: Cancelled = None,
    max_decoded_pixels: int = MAX_DECODED_PIXELS,
) -> list[tuple[int, int, int, int]]:
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
    if total > int(max_decoded_pixels):
        raise ValueError(
            f"sheet has {total:,} pixels; detection limit is "
            f"{int(max_decoded_pixels):,}"
        )
    _check_cancelled(cancelled)

    alpha = rgba.split()[3]
    thresh = max(0, min(255, int(alpha_thresh)))
    mask_img = alpha.point(lambda p: 255 if p >= thresh else 0, mode="L")
    # 3x3 dilation at C speed (was a nested-Python triple loop in the source).
    dilated = mask_img.filter(ImageFilter.MaxFilter(3)).tobytes()

    visited = bytearray(total)
    min_area = max(min_area_floor, int(total * min_area_frac))
    boxes: list[tuple[int, int, int, int]] = []

    for start in range(total):
        if start % 4096 == 0:
            _check_cancelled(cancelled)
        if not dilated[start] or visited[start]:
            continue
        queue = deque([start])
        processed = 0
        visited[start] = 1
        min_x, min_y, max_x, max_y, area = w, h, -1, -1, 0
        while queue:
            if processed % 4096 == 0:
                _check_cancelled(cancelled)
            p = queue.popleft()
            processed += 1
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
                        queue.append(q)
        if area >= min_area:
            # +1 on max to make right/bottom exclusive (PIL crop convention).
            boxes.append((min_x, min_y, max_x + 1, max_y + 1))

    row_tol = max(24, int(h * 0.04))
    boxes.sort(key=lambda b: (b[1] // row_tol, b[0]))
    return boxes


def crop_boxes(img, boxes, cancelled: Cancelled = None) -> list:
    """Crop a list of (l,t,r,b) boxes out of an image (RGBA frames)."""
    src = img if img.mode == "RGBA" else img.convert("RGBA")
    frames = []
    try:
        for box in boxes:
            _check_cancelled(cancelled)
            frames.append(src.crop(box))
        return frames
    except Exception:
        _close_images(frames)
        raise


# --- frame loading (fallible IO -> envelope) ----------------------------------

def load_frames(
    path,
    *,
    max_frames: int = MAX_FRAME_COUNT,
    max_decoded_pixels: int = MAX_DECODED_PIXELS,
    cancelled: Cancelled = None,
) -> dict:
    """Load frames from ONE file. Animated GIF/WebP/APNG page through n_frames via
    seek; a still image yields a single frame. Envelope: data = list[PIL.Image]
    (RGBA). Folders are the caller's job — see `load_folder`."""
    p = Path(path)
    if not p.is_file():
        return err("file.missing", f"not a file: {p}")
    frames = []
    try:
        max_frames, max_decoded_pixels = _validate_budget(
            max_frames, max_decoded_pixels
        )
        from PIL import Image
        with Image.open(p) as im:
            n = int(getattr(im, "n_frames", 1))
            problem = _budget_error(n, 0, max_frames, max_decoded_pixels)
            if problem is not None:
                return problem
            decoded_pixels = 0
            for i in range(n):
                _check_cancelled(cancelled)
                im.seek(i)
                decoded_pixels += im.width * im.height
                problem = _budget_error(
                    i + 1, decoded_pixels, max_frames, max_decoded_pixels
                )
                if problem is not None:
                    _close_images(frames)
                    return problem
                frames.append(im.convert("RGBA").copy())
        if not frames:
            return err("load.empty", f"no frames decoded from {p.name}")
        return ok(frames, details=f"{len(frames)} frame(s) from {p.name}")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except SpriteOperationCancelled:
        _close_images(frames)
        return err("operation.cancelled", "frame loading cancelled")
    except Exception as ex:                 # PIL raises many types on bad input
        _close_images(frames)
        return err("load.failed", f"could not load {p.name}: {ex}")


def load_image(
    path,
    *,
    max_decoded_pixels: int = MAX_DECODED_PIXELS,
    cancelled: Cancelled = None,
) -> dict:
    """Load only frame zero from one image for sheet operations."""
    p = Path(path)
    if not p.is_file():
        return err("file.missing", f"not a file: {p}")
    try:
        _check_cancelled(cancelled)
        _, max_decoded_pixels = _validate_budget(1, max_decoded_pixels)
        from PIL import Image
        with Image.open(p) as im:
            pixels = im.width * im.height
            problem = _budget_error(1, pixels, 1, max_decoded_pixels)
            if problem is not None:
                return problem
            image = im.convert("RGBA").copy()
        return ok(image, details=f"{image.width}x{image.height} sheet from {p.name}")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except SpriteOperationCancelled:
        return err("operation.cancelled", "image loading cancelled")
    except Exception as ex:
        return err("load.failed", f"could not load {p.name}: {ex}")


def _natural_key(name: str):
    """Sort key so frame_2 < frame_10 (digit runs compared numerically)."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def load_folder(
    folder,
    *,
    max_frames: int = MAX_FRAME_COUNT,
    max_decoded_pixels: int = MAX_DECODED_PIXELS,
    max_entries: int = MAX_FOLDER_ENTRIES,
    cancelled: Cancelled = None,
) -> dict:
    """Load a numbered image sequence from a folder (natural-sorted). Envelope:
    data = list[PIL.Image] (RGBA)."""
    d = Path(folder)
    if not d.is_dir():
        return err("dir.missing", f"not a folder: {d}")
    frames = []
    try:
        max_frames, max_decoded_pixels = _validate_budget(
            max_frames, max_decoded_pixels
        )
        max_entries = int(max_entries)
        if max_entries < 1:
            raise ValueError("folder entry limit must be positive")
        files = []
        for scanned, candidate in enumerate(d.iterdir(), start=1):
            if scanned % 256 == 0:
                _check_cancelled(cancelled)
            if scanned > max_entries:
                return err(
                    "input.too_many_entries",
                    f"folder contains more than {max_entries:,} entries",
                )
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
                files.append(candidate)
                if len(files) > max_frames:
                    return _budget_error(
                        len(files), 0, max_frames, max_decoded_pixels
                    )
        files.sort(key=lambda f: _natural_key(f.name))
        if not files:
            return err("input.empty", f"no images in {d}")
        problem = _budget_error(len(files), 0, max_frames, max_decoded_pixels)
        if problem is not None:
            return problem
        from PIL import Image
        decoded_pixels = 0
        for f in files:
            _check_cancelled(cancelled)
            with Image.open(f) as im:
                decoded_pixels += im.width * im.height
                problem = _budget_error(
                    len(frames) + 1,
                    decoded_pixels,
                    max_frames,
                    max_decoded_pixels,
                )
                if problem is not None:
                    _close_images(frames)
                    return problem
                frames.append(im.convert("RGBA").copy())
        return ok(frames, details=f"{len(frames)} frame(s) from {d.name}/")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except SpriteOperationCancelled:
        _close_images(frames)
        return err("operation.cancelled", "folder loading cancelled")
    except Exception as ex:
        _close_images(frames)
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


@dataclass
class LoadedSpriteSource:
    """Prepared viewer state returned by the headless source loader."""

    frames: list
    source_image: object | None
    boxes: list[tuple[int, int, int, int]]
    kind: str


def prepare_source(
    path,
    mode: str,
    *,
    rows: int = 4,
    cols: int = 4,
    cell_width: int = 32,
    cell_height: int = 32,
    alpha_threshold: int = 32,
    cancelled: Cancelled = None,
) -> dict:
    """Load and prepare one interactive source without any UI dependencies.

    ``mode`` is one of ``animation``, ``grid``, ``cell``, or ``auto``.
    """
    source = Path(path)
    if cancelled is not None and cancelled():
        return err("operation.cancelled", "sprite preparation cancelled")
    if mode == "animation":
        result = (
            load_folder(source, cancelled=cancelled)
            if source.is_dir()
            else load_frames(source, cancelled=cancelled)
        )
        if result["error"]:
            return result
        frames = result["data"]
        kind = "folder" if source.is_dir() else (
            "animation" if len(frames) > 1 else "image"
        )
        return ok(LoadedSpriteSource(frames, None, [], kind), details=result["details"])

    if mode not in {"grid", "cell", "auto"}:
        return err("settings.invalid", f"unknown sprite source mode: {mode}")
    if not source.is_file():
        return err("file.missing", "sheet modes need one existing image file")

    loaded = load_image(source, cancelled=cancelled)
    if loaded["error"]:
        return loaded
    source_image = loaded["data"]
    try:
        width, height = source_image.size
        if mode == "grid":
            boxes = grid_boxes(width, height, rows, cols)
            kind = "sheet-grid"
        elif mode == "cell":
            boxes = cell_boxes(width, height, cell_width, cell_height)
            kind = "sheet-cell"
        else:
            alpha_threshold = int(alpha_threshold)
            if not 0 <= alpha_threshold <= 255:
                raise ValueError("alpha threshold must be between 0 and 255")
            boxes = detect_sprites(
                source_image,
                alpha_thresh=alpha_threshold,
                cancelled=cancelled,
            )
            kind = "auto"
        if not boxes:
            source_image.close()
            return err(
                "load.empty",
                "no frames produced; check the source and mode parameters",
            )
        frame_pixels = sum((right - left) * (bottom - top)
                           for left, top, right, bottom in boxes)
        problem = _budget_error(
            len(boxes),
            width * height + frame_pixels,
            MAX_FRAME_COUNT,
            MAX_DECODED_PIXELS,
        )
        if problem is not None:
            source_image.close()
            return problem
        frames = crop_boxes(source_image, boxes, cancelled=cancelled)
        return ok(
            LoadedSpriteSource(frames, source_image, boxes, kind),
            details=f"{len(frames)} frame(s) from {source.name}",
        )
    except SpriteOperationCancelled:
        source_image.close()
        return err("operation.cancelled", "sprite preparation cancelled")
    except (TypeError, ValueError) as ex:
        source_image.close()
        return err("settings.invalid", str(ex))
    except Exception as ex:
        source_image.close()
        return err("load.failed", f"could not prepare {source.name}: {ex}")


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

def export_gif(
    frames,
    dst,
    fps: int = 12,
    *,
    cancelled: Cancelled = None,
) -> dict:
    """Write and decode-validate an animated GIF before atomic publication."""
    if not frames:
        return err("input.empty", "no frames to export")
    d = Path(dst)
    tmp: Path | None = None
    owned_images: list = []
    try:
        _check_cancelled(cancelled)
        fps = int(fps)
        if not 1 <= fps <= 120:
            return err("settings.invalid", "FPS must be between 1 and 120")
        max_frames, max_pixels = _validate_budget(
            MAX_FRAME_COUNT, MAX_DECODED_PIXELS
        )
        decoded_pixels = sum(int(f.width) * int(f.height) for f in frames)
        problem = _budget_error(len(frames), decoded_pixels, max_frames, max_pixels)
        if problem is not None:
            return problem
        from PIL import Image
        duration = max(1, int(round(1000.0 / fps)))
        # Loaded/sliced frames are already RGBA; do not copy every frame again.
        prepared = []
        for frame in frames:
            if frame.mode == "RGBA":
                prepared.append(frame)
            else:
                converted = frame.convert("RGBA")
                prepared.append(converted)
                owned_images.append(converted)
        d.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{d.stem}.", suffix=f".part{d.suffix}", dir=d.parent
        )
        os.close(fd)
        tmp = Path(tmp_name)
        prepared[0].save(
            tmp,
            "GIF",
            save_all=True,
            append_images=prepared[1:],
            duration=duration,
            loop=0,
            disposal=2,
            optimize=False,
        )
        _check_cancelled(cancelled)
        with Image.open(tmp) as check:
            if check.format != "GIF":
                raise ValueError("encoder did not produce a GIF")
            actual_frames = int(getattr(check, "n_frames", 1))
            if actual_frames != len(frames):
                raise ValueError(
                    f"expected {len(frames)} frames, decoded {actual_frames}"
                )
            for index in range(actual_frames):
                _check_cancelled(cancelled)
                check.seek(index)
                check.load()
        with tmp.open("rb+") as handle:
            os.fsync(handle.fileno())
        _check_cancelled(cancelled)
        os.replace(tmp, d)
        return ok(str(d), details=f"{len(frames)} frames @ {fps}fps -> {d.name}")
    except ImportError:
        return err("dep.missing", "Pillow not installed — pip install pillow")
    except SpriteOperationCancelled:
        return err("operation.cancelled", "GIF export cancelled")
    except Exception as ex:
        return err("export.failed", f"could not write GIF {d.name}: {ex}")
    finally:
        _close_images(owned_images)
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def export_meta_json(meta, dst, *, cancelled: Cancelled = None) -> dict:
    """Write and parse-validate metadata JSON before atomic publication."""
    d = Path(dst)
    tmp: Path | None = None
    try:
        _check_cancelled(cancelled)
        payload = meta.to_dict() if isinstance(meta, SpriteMeta) else dict(meta)
        encoded = json.dumps(
            payload, indent=2, ensure_ascii=False, allow_nan=False
        ) + "\n"
        expected = json.loads(encoded)
        d.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{d.stem}.", suffix=f".part{d.suffix}", dir=d.parent
        )
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        with tmp.open("r", encoding="utf-8") as handle:
            if json.load(handle) != expected:
                raise ValueError("JSON read-back did not match the requested metadata")
        _check_cancelled(cancelled)
        os.replace(tmp, d)
        return ok(str(d), details=f"metadata -> {d.name}")
    except SpriteOperationCancelled:
        return err("operation.cancelled", "metadata export cancelled")
    except Exception as ex:
        return err("export.failed", f"could not write JSON {d.name}: {ex}")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)

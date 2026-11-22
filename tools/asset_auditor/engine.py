"""Asset Auditor engine — a pure, deterministic batch inspector for a folder
of images/assets. No AI, no network, no GPU: numpy + Pillow + stdlib only.

Reads only, writes reports. It never modifies or deletes a source file — the
tool's "output" is three report files (HTML + JSON + CSV) describing what it
found. Every heavy dependency (Pillow, numpy) is imported lazily inside the
function that needs it, so discovery and the sidebar keep working on a machine
that hasn't installed them yet (AGENTS.md §lazy heavy-dep import).

What it detects:
    - exact duplicates       sha256 byte-identical groups
    - near duplicates        dHash Hamming distance <= threshold (union-find)
    - corrupt / unreadable   Pillow verify()/decode failure + magic-byte header
    - unsafe filenames       Windows reserved names, illegal chars, trailing . / space
    - empty files            0 bytes
    - oversized files        larger than a configurable MB threshold
    - empty folders          directories with no files beneath them
    - tiny images            below a minimum dimension
    - resolution histogram   counts per WxH
    - image health           very-dark / very-bright / low-contrast flags

The distinct hashing / metric primitives are distilled from three deterministic
deterministic primitives: dHash image fingerprints, image health checks,
`analysis_cv.collect_preflight_metrics` (corrupt/resolution/alpha health), and
streaming sha256_file, and cross-platform filename validation.
The perceptual/health code paths there carried optional imagehash/cv2/skimage
branches — we keep only the numpy/Pillow path, so the result is one code path
with no silent fallbacks.

Public interface:
    sha256_file(path)                 -> str                 (pure IO)
    dhash(img, size=8)                -> str                 (pure, PIL image in)
    hamming(a, b)                     -> int                 (pure)
    is_unsafe_name(name)              -> bool                (pure)
    check_image(path, ...)            -> dict                (corrupt/size/dims/health/dhash)
    inspect_file(path, opts)          -> FileRecord
    audit(paths, opts, progress, cancelled) -> AuditReport   (aggregate)
    write_reports(report, out_dir, cancelled) -> envelope    (staged report set)
    process(paths, opts, progress, cancelled) -> AuditResult (durable orchestrator)
    validate_result(result, opts)     -> bool                (exact recovery gate)
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from toolbox.engine_common import (
    CommandCancelled, IMAGE_EXTS, err, ok, safe_remove as _safe_remove_dup, sha256_file,
)

Cancelled = Callable[[], bool] | None
_REPORT_SCHEMA = 2
_MAX_REPORT_WARNINGS = 100
MAX_AUDIT_FILES = 100_000


def _cancelled(cancelled: Cancelled, stage: str, path: str | Path = "") -> None:
    if cancelled is not None and cancelled():
        command = [stage]
        if path:
            command.append(str(path))
        raise CommandCancelled(command)

# --- filename safety ----------------------------------------------------------

# The stem before the first dot is what Windows treats as the device name, so
# "CON.png" is as unsafe as "CON". Kept as-is from pack_core's proven set.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Characters no portable filesystem accepts; control chars (<32) added below.
_ILLEGAL_CHARS = set('<>:"/\\|?*')

# --- magic-byte signatures for the common raster formats ----------------------
# A header check catches files whose bytes are not a recognised image at all
# (truncated to nothing, renamed .txt, garbage) even before Pillow tries to
# decode — and lets us flag an extension/format mismatch.
_MAGIC = {
    "png":  lambda h: h[:8] == b"\x89PNG\r\n\x1a\n",
    "jpeg": lambda h: h[:3] == b"\xff\xd8\xff",
    "gif":  lambda h: h[:6] in (b"GIF87a", b"GIF89a"),
    "webp": lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP",
    "bmp":  lambda h: h[:2] == b"BM",
    "tiff": lambda h: h[:4] in (b"II*\x00", b"MM\x00*"),
}
# Which detected magic format an extension is allowed to carry (jpg==jpeg, etc.).
_EXT_TO_FORMAT = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif",
    ".webp": "webp", ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff",
}

# Health thresholds on a 0..1 luminance scale (mean = brightness, std = contrast).
_DARK_MAX = 0.12
_BRIGHT_MIN = 0.88
_LOW_CONTRAST_MAX = 0.06


# --- pure primitives ----------------------------------------------------------

def dhash(img, size: int = 8) -> str:
    """Difference hash of a PIL image as a bit-string of length `size*size`.

    Grayscale, resize to (size+1, size), then compare each pixel to its left
    neighbour — encoding gradients, which are robust to scale and mild colour
    shifts. Pure over the image; PIL/numpy imported lazily. (dHash bits distilled
    using pure Pillow + NumPy.)"""
    from PIL import Image
    import numpy as np
    gray = img.convert("L").resize((size + 1, size), Image.BILINEAR)
    arr = np.asarray(gray, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]
    return "".join("1" if bit else "0" for bit in diff.flatten())


def hamming(a: str, b: str) -> int:
    """Hamming distance between two equal-length bit-strings. Unequal lengths are
    incomparable, so we return the larger length (maximally distant) rather than
    silently comparing a prefix."""
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def is_unsafe_name(name: str) -> bool:
    """True if `name` (a bare filename) would break on some target OS: a Windows
    reserved device name, an illegal/control character, or a trailing dot/space
    (which Windows silently strips, corrupting the name)."""
    if not name:
        return True
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        return True
    if any(ch in _ILLEGAL_CHARS or ord(ch) < 32 for ch in name):
        return True
    if name.endswith((".", " ")):
        return True
    return False


# --- per-file inspection ------------------------------------------------------

def check_image(path: str | Path, *, compute_health: bool = True,
                compute_dhash: bool = True, dhash_size: int = 8,
                cancelled: Cancelled = None) -> dict:
    """Inspect one image file: corruption, dimensions, alpha, health, and (opt.)
    its perceptual dHash — opening the file once. Never raises: a decode failure
    is reported as `corrupt=True` with a reason, which is the whole point of this
    check (not an error to propagate)."""
    p = Path(path)
    rec: dict[str, Any] = {
        "corrupt": False, "reason": "", "magic": "", "ext_mismatch": False,
        "width": 0, "height": 0, "mode": "", "has_alpha": False,
        "brightness": None, "contrast": None, "dhash": "", "flags": [],
    }

    # 1) magic-byte header — cheap, and catches non-images before decode.
    try:
        _cancelled(cancelled, "image-header", p)
        with p.open("rb") as fh:
            header = fh.read(16)
        _cancelled(cancelled, "image-header", p)
    except CommandCancelled:
        raise
    except OSError as ex:
        rec["corrupt"] = True
        rec["reason"] = f"unreadable: {ex}"
        return rec
    rec["magic"] = next((fmt for fmt, test in _MAGIC.items() if test(header)), "")
    expected = _EXT_TO_FORMAT.get(p.suffix.lower())
    if rec["magic"] and expected and rec["magic"] != expected:
        rec["ext_mismatch"] = True                 # informative, not corrupt
        rec["flags"].append("ext_mismatch")

    # 2) Pillow verify() catches truncation/garbage; it consumes the handle, so
    #    a second open is needed to actually read pixels.
    from PIL import Image
    try:
        _cancelled(cancelled, "image-verify", p)
        with Image.open(p) as im:
            im.verify()
        _cancelled(cancelled, "image-verify", p)
    except CommandCancelled:
        raise
    except Exception as ex:                         # PIL raises many types
        rec["corrupt"] = True
        detail = "unrecognised header" if not rec["magic"] else str(ex)
        rec["reason"] = f"decode failed: {detail}"
        return rec

    try:
        _cancelled(cancelled, "image-decode", p)
        with Image.open(p) as im:
            im.load()
            rec["width"], rec["height"] = im.size
            rec["mode"] = im.mode
            rec["has_alpha"] = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            if compute_dhash:
                rec["dhash"] = dhash(im, dhash_size)
            if compute_health:
                b, c = _health(im)
                rec["brightness"], rec["contrast"] = b, c
                if b <= _DARK_MAX:
                    rec["flags"].append("very_dark")
                elif b >= _BRIGHT_MIN:
                    rec["flags"].append("very_bright")
                if c <= _LOW_CONTRAST_MAX:
                    rec["flags"].append("low_contrast")
        _cancelled(cancelled, "image-decode", p)
    except CommandCancelled:
        raise
    except Exception as ex:
        rec["corrupt"] = True
        rec["reason"] = f"decode failed: {ex}"
    return rec


def _health(im) -> tuple[float, float]:
    """Mean luminance (brightness) and its std (contrast), both 0..1. Downscaled
    to bound cost on large images — the statistics are scale-stable. (numpy path
    using pure Pillow + NumPy.)"""
    import numpy as np
    from PIL import Image
    small = im.convert("L").resize((128, 128), Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    return float(np.mean(arr)), float(np.std(arr))


@dataclass
class FileRecord:
    """One row of the audit — everything known about a single scanned file."""
    path: str
    size_bytes: int
    sha256: str = ""
    corrupt: bool = False
    reason: str = ""
    width: int = 0
    height: int = 0
    mode: str = ""
    has_alpha: bool = False
    brightness: float | None = None
    contrast: float | None = None
    dhash: str = ""
    unsafe_name: bool = False
    empty: bool = False
    oversized: bool = False
    tiny: bool = False
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def inspect_file(path: str | Path, opts: "AuditOptions",
                 cancelled: Cancelled = None) -> FileRecord:
    """Gather every per-file signal for one path — size, hash, image check,
    name safety — into a FileRecord. Never raises; IO failures land as flags."""
    p = Path(path)
    _cancelled(cancelled, "asset-inspection", p)
    try:
        size = p.stat().st_size
    except OSError:
        size = -1
    rec = FileRecord(path=str(p), size_bytes=size)
    rec.unsafe_name = is_unsafe_name(p.name)
    rec.empty = size == 0
    rec.oversized = size > int(opts.oversized_mb * 1024 * 1024)

    if size > 0:
        try:
            rec.sha256 = sha256_file(p, cancelled=cancelled)  # exact-dup key
        except CommandCancelled:
            raise
        except OSError as ex:
            rec.reason = f"hash failed: {ex}"

    img = check_image(
        p, compute_health=opts.check_health, dhash_size=opts.dhash_size,
        cancelled=cancelled,
    )
    rec.corrupt = img["corrupt"]
    rec.reason = rec.reason or img["reason"]
    rec.width, rec.height = img["width"], img["height"]
    rec.mode, rec.has_alpha = img["mode"], img["has_alpha"]
    rec.brightness, rec.contrast = img["brightness"], img["contrast"]
    rec.dhash = img["dhash"]
    rec.flags = img["flags"]
    if not rec.corrupt and rec.width and rec.height:
        rec.tiny = min(rec.width, rec.height) < opts.min_dimension
    return rec


# --- options + aggregate report ----------------------------------------------

@dataclass
class AuditOptions:
    out_root: Path | None = None
    scan_root: Path | None = None      # for empty-folder detection (read-only walk)
    near_dup_hamming: int = 8          # dHash distance (of 64 bits) to call a near-dup
    oversized_mb: float = 25.0         # files larger than this are flagged
    min_dimension: int = 32            # images with a side below this are "tiny"
    check_health: bool = True          # compute dark/bright/low-contrast flags
    dhash_size: int = 8                # dHash grid → dhash_size**2 bits


def normalized_options(opts: AuditOptions) -> tuple[AuditOptions | None, str]:
    """Validate public resource/quality settings before scanning any source."""
    try:
        hamming_limit = int(opts.near_dup_hamming)
        oversized_mb = float(opts.oversized_mb)
        min_dimension = int(opts.min_dimension)
        dhash_size = int(opts.dhash_size)
        out_root = Path(opts.out_root) if opts.out_root is not None else None
        scan_root = Path(opts.scan_root) if opts.scan_root is not None else None
    except (TypeError, ValueError, OSError) as ex:
        return None, f"invalid audit options: {ex}"
    if isinstance(opts.near_dup_hamming, bool) or hamming_limit != opts.near_dup_hamming:
        return None, "near-duplicate Hamming threshold must be a whole number"
    if isinstance(opts.min_dimension, bool) or min_dimension != opts.min_dimension:
        return None, "minimum dimension must be a whole number"
    if isinstance(opts.dhash_size, bool) or dhash_size != opts.dhash_size:
        return None, "dHash size must be a whole number"
    if not 4 <= dhash_size <= 32:
        return None, "dHash size must be between 4 and 32"
    if not 0 <= hamming_limit <= dhash_size * dhash_size:
        return None, f"near-duplicate Hamming threshold must be 0–{dhash_size * dhash_size}"
    if not math.isfinite(oversized_mb) or not 0 < oversized_mb <= 1_000_000:
        return None, "oversized threshold must be greater than 0 and at most 1,000,000 MB"
    if not 1 <= min_dimension <= 1_000_000:
        return None, "minimum dimension must be between 1 and 1,000,000 pixels"
    return replace(
        opts,
        out_root=out_root,
        scan_root=scan_root,
        near_dup_hamming=hamming_limit,
        oversized_mb=oversized_mb,
        min_dimension=min_dimension,
        check_health=bool(opts.check_health),
        dhash_size=dhash_size,
    ), ""


@dataclass
class AuditReport:
    scanned: int
    records: list[FileRecord]
    exact_dups: list[list[str]]        # groups of byte-identical paths
    near_dups: list[list[str]]         # groups within the Hamming threshold
    corrupt: list[dict]                # {path, reason}
    unsafe_names: list[str]
    empty_files: list[str]
    oversized: list[dict]              # {path, size_mb}
    empty_folders: list[str]
    tiny_images: list[dict]            # {path, dims}
    health_flags: list[dict]          # {path, flags, brightness, contrast}
    resolution_histogram: dict[str, int]
    scan_warnings: list[str]
    options: dict

    def issue_count(self) -> int:
        return (sum(len(g) for g in self.exact_dups) + sum(len(g) for g in self.near_dups)
                + len(self.corrupt) + len(self.unsafe_names) + len(self.empty_files)
                + len(self.oversized) + len(self.empty_folders) + len(self.tiny_images)
                + len(self.health_flags))

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "exact_dups": self.exact_dups,
            "near_dups": self.near_dups,
            "corrupt": self.corrupt,
            "unsafe_names": self.unsafe_names,
            "empty_files": self.empty_files,
            "oversized": self.oversized,
            "empty_folders": self.empty_folders,
            "tiny_images": self.tiny_images,
            "health_flags": self.health_flags,
            "resolution_histogram": self.resolution_histogram,
            "scan_warnings": self.scan_warnings,
            "options": self.options,
            "records": [r.to_dict() for r in self.records],
        }


def _report_counts(report: AuditReport) -> dict[str, int]:
    return {
        "exact_duplicate_groups": len(report.exact_dups),
        "near_duplicate_groups": len(report.near_dups),
        "corrupt": len(report.corrupt),
        "unsafe_names": len(report.unsafe_names),
        "empty_files": len(report.empty_files),
        "oversized": len(report.oversized),
        "empty_folders": len(report.empty_folders),
        "tiny_images": len(report.tiny_images),
        "health_flags": len(report.health_flags),
    }


def _payload_counts(payload: dict) -> dict[str, int] | None:
    keys = {
        "exact_duplicate_groups": "exact_dups",
        "near_duplicate_groups": "near_dups",
        "corrupt": "corrupt",
        "unsafe_names": "unsafe_names",
        "empty_files": "empty_files",
        "oversized": "oversized",
        "empty_folders": "empty_folders",
        "tiny_images": "tiny_images",
        "health_flags": "health_flags",
    }
    if any(not isinstance(payload.get(source), list) for source in keys.values()):
        return None
    return {label: len(payload[source]) for label, source in keys.items()}


@dataclass
class AuditResult:
    action: str                       # audited | failed
    reason: str
    scanned: int = 0
    issues: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    resolution_histogram: dict[str, int] = field(default_factory=dict)
    out_path: str | None = None       # headline HTML report
    artifacts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detail: str = ""
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _group_by_sha(records: list[FileRecord]) -> list[list[str]]:
    """Paths sharing a sha256 (non-empty hash), as groups of size >= 2, sorted."""
    buckets: dict[str, list[str]] = {}
    for r in records:
        if r.sha256:
            buckets.setdefault(r.sha256, []).append(r.path)
    return sorted((sorted(g) for g in buckets.values() if len(g) > 1))


def _group_near_dups(records: list[FileRecord], threshold: int,
                     cancelled: Cancelled = None) -> list[list[str]]:
    """Exact union-find grouping over a BK-tree Hamming-metric index.

    This returns the same transitive groups as an all-pairs comparison, but a
    normal sparse dHash collection does not pay for every unrelated pair. The
    metric index can still approach quadratic work on adversarial dense data;
    the threshold-wide case is handled directly in linear time.
    """
    cand = [r for r in records if r.dhash and not r.corrupt]
    n = len(cand)
    if n < 2 or threshold < 0:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[left_root] = right_root

    lengths = {len(record.dhash) for record in cand}
    valid_bits = all(set(record.dhash) <= {"0", "1"} for record in cand)
    if len(lengths) != 1 or not valid_bits:
        # Defensive compatibility for externally constructed FileRecords. Real
        # audit records always carry equal-length binary dHashes.
        for i in range(n):
            _cancelled(cancelled, "near-duplicate-grouping")
            for j in range(i + 1, n):
                if hamming(cand[i].dhash, cand[j].dhash) <= threshold:
                    union(i, j)
    else:
        bit_length = next(iter(lengths))
        if threshold >= bit_length:
            for i in range(1, n):
                union(0, i)
        else:
            # Node = [hash integer, representative record index, children by
            # exact distance]. Equal hashes share the existing representative.
            root: list | None = None
            for index, record in enumerate(cand):
                _cancelled(cancelled, "near-duplicate-grouping")
                value = int(record.dhash, 2)
                if root is None:
                    root = [value, index, {}]
                    continue

                stack = [root]
                visited = 0
                while stack:
                    if visited % 256 == 0:
                        _cancelled(cancelled, "near-duplicate-grouping")
                    visited += 1
                    node = stack.pop()
                    distance = (value ^ node[0]).bit_count()
                    if distance <= threshold:
                        union(index, node[1])
                    low, high = max(0, distance - threshold), distance + threshold
                    stack.extend(
                        child for edge, child in node[2].items()
                        if low <= edge <= high
                    )

                node = root
                while True:
                    distance = (value ^ node[0]).bit_count()
                    if distance == 0:
                        break
                    child = node[2].get(distance)
                    if child is None:
                        node[2][distance] = [value, index, {}]
                        break
                    node = child

    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(cand[i].path)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1))


def _find_empty_folders(root: Path | None,
                        cancelled: Cancelled = None) -> tuple[list[str], list[str]]:
    """Directories under `root` that contain no entries at all (read-only walk).
    Best-effort — unreadable dirs are skipped, not silently swallowed as empty."""
    if not root:
        return [], []
    root = Path(root)
    if not root.is_dir():
        return [], []
    empties: list[str] = []
    warnings: list[str] = []
    for d in root.rglob("*"):
        _cancelled(cancelled, "empty-folder-scan", d)
        try:
            if d.is_dir() and not any(d.iterdir()):
                empties.append(str(d))
        except OSError as ex:
            if len(warnings) < _MAX_REPORT_WARNINGS:
                warnings.append(f"folder inspection skipped for {d}: {ex}")
            elif len(warnings) == _MAX_REPORT_WARNINGS:
                warnings.append("additional folder-inspection warnings omitted")
    return sorted(empties), warnings


def audit(paths: Iterable[str | Path], opts: AuditOptions,
          progress: Callable[[int, int, str], None] | None = None,
          cancelled: Cancelled = None) -> AuditReport:
    """Inspect every path and aggregate the findings into an AuditReport.

    `paths` are the files to inspect (the panel pre-filters to image extensions);
    `opts.scan_root`, if set, is walked read-only for empty folders. `progress`
    is called `(index, total, name)` per file so the UI can show a bar. Pure
    aggregate — no writes happen here; see write_reports."""
    paths = [Path(p) for p in paths]
    total = len(paths)
    records: list[FileRecord] = []
    for i, p in enumerate(paths, 1):
        _cancelled(cancelled, "asset-audit", p)
        records.append(inspect_file(p, opts, cancelled))
        if progress:
            progress(i, total, p.name)

    resolution = Counter(
        f"{r.width}x{r.height}" for r in records if not r.corrupt and r.width and r.height
    )
    empty_folders, scan_warnings = _find_empty_folders(opts.scan_root, cancelled)
    return AuditReport(
        scanned=total,
        records=records,
        exact_dups=_group_by_sha(records),
        near_dups=_group_near_dups(records, opts.near_dup_hamming, cancelled),
        corrupt=[{"path": r.path, "reason": r.reason} for r in records if r.corrupt],
        unsafe_names=[r.path for r in records if r.unsafe_name],
        empty_files=[r.path for r in records if r.empty],
        oversized=[{"path": r.path, "size_mb": round(r.size_bytes / 1048576, 2)}
                   for r in records if r.oversized],
        empty_folders=empty_folders,
        tiny_images=[{"path": r.path, "dims": f"{r.width}x{r.height}"}
                     for r in records if r.tiny],
        health_flags=[{"path": r.path, "flags": r.flags,
                       "brightness": r.brightness, "contrast": r.contrast}
                      for r in records if not r.corrupt and _health_flags(r.flags)],
        resolution_histogram=dict(sorted(resolution.items(), key=lambda kv: (-kv[1], kv[0]))),
        scan_warnings=scan_warnings,
        options={"near_dup_hamming": opts.near_dup_hamming, "oversized_mb": opts.oversized_mb,
                 "min_dimension": opts.min_dimension, "check_health": opts.check_health,
                 "dhash_size": opts.dhash_size},
    )


def _health_flags(flags: list[str]) -> list[str]:
    return [f for f in flags if f in ("very_dark", "very_bright", "low_contrast")]


# --- report writing (the tool's only output; atomic, non-destructive) ---------

def _artifact(actual: Path, logical: Path, kind: str,
              cancelled: Cancelled = None) -> dict:
    size = actual.stat().st_size
    if size <= 0:
        raise OSError(f"empty {kind} report: {actual}")
    return {
        "path": str(logical),
        "kind": kind,
        "bytes": size,
        "sha256": sha256_file(actual, cancelled=cancelled),
    }


def _remove_parts(paths: Iterable[Path]) -> str | None:
    failures: list[str] = []
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as ex:
            failures.append(f"{path}: {ex}")
    return "; ".join(failures) or None


def _thumb_data_uri(path: str, box: int = 96,
                    cancelled: Cancelled = None) -> tuple[str, str]:
    """A small inline PNG data-URI thumbnail, or "" if the image can't be read
    (so the report stays self-contained — no external image requests). Failure is
    announced by the thumbnail's absence, never by a wrong/placeholder image."""
    try:
        _cancelled(cancelled, "report-thumbnail", path)
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((box, box), Image.BILINEAR)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        _cancelled(cancelled, "report-thumbnail", path)
        return (
            "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
            "",
        )
    except CommandCancelled:
        raise
    except Exception as ex:  # Pillow surfaces format-specific exception types
        return "", f"thumbnail omitted for {path}: {type(ex).__name__}: {ex}"


def write_reports(report: AuditReport, out_dir: str | Path,
                  cancelled: Cancelled = None) -> dict:
    """Write audit.html + audit.json + audit_issues.csv to `out_dir`, atomically.
    Returns the standard envelope; data is {json, csv, html} paths. Read-only with
    respect to sources — the only files touched are the three reports."""
    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return err("output.dir", f"cannot create output folder {out}: {ex}")

    json_path, csv_path, html_path = (
        out / "audit.json", out / "audit_issues.csv", out / "audit.html"
    )
    json_part, csv_part, html_part = (
        path.with_name(path.name + ".part")
        for path in (json_path, csv_path, html_path)
    )
    parts = (json_part, csv_part, html_part)
    try:
        _cancelled(cancelled, "report-render", out)
        csv_part.write_text(_render_csv(report, cancelled), encoding="utf-8", newline="")
        html, thumbnail_warnings = _render_html(report, cancelled)
        warnings = [*report.scan_warnings, *thumbnail_warnings]
        if len(warnings) > _MAX_REPORT_WARNINGS + 1:
            warnings = warnings[:_MAX_REPORT_WARNINGS] + ["additional audit warnings omitted"]
        html_part.write_text(html, encoding="utf-8", newline="")
        csv_record = _artifact(csv_part, csv_path, "csv", cancelled)
        html_record = _artifact(html_part, html_path, "html", cancelled)

        payload = report.to_dict()
        payload["report_schema"] = _REPORT_SCHEMA
        payload["report_artifacts"] = {
            "csv": {key: csv_record[key] for key in ("path", "bytes", "sha256")},
            "html": {key: html_record[key] for key in ("path", "bytes", "sha256")},
        }
        payload["warnings"] = warnings
        json_part.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline=""
        )
        json_record = _artifact(json_part, json_path, "json", cancelled)
        _cancelled(cancelled, "report-commit", out)
        html_part.replace(html_path)
        csv_part.replace(csv_path)
        # JSON is the completion marker and therefore always publishes last.
        json_part.replace(json_path)
    except CommandCancelled as ex:
        cleanup_error = _remove_parts(parts)
        if cleanup_error:
            raise OSError(f"report cancellation cleanup failed: {cleanup_error}") from ex
        raise
    except (OSError, TypeError, ValueError, UnicodeError) as ex:
        cleanup_error = _remove_parts(parts)
        details = f"failed writing reports to {out}: {ex}"
        if cleanup_error:
            details += f"; staged cleanup failed: {cleanup_error}"
        return err("report.write", details, retryable=True)

    return ok(
        {
            "json": str(json_path), "csv": str(csv_path), "html": str(html_path),
            "artifacts": [json_record, csv_record, html_record], "warnings": warnings,
        },
        details=f"{report.issue_count()} issue(s) across {report.scanned} file(s)",
        degraded=bool(warnings),
    )


def process(paths: Iterable[str | Path], opts: AuditOptions,
            progress: Callable[[int, int, str], None] | None = None,
            cancelled: Cancelled = None) -> AuditResult:
    """Audit one selected collection and publish its validated report set."""
    sources = sorted(
        (Path(path) for path in paths),
        key=lambda path: str(path).casefold(),
    )
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        return AuditResult("failed", options_error, detail="bad.options")
    if not sources:
        return AuditResult("failed", "the audit has no input files", detail="input.empty")
    if len(sources) > MAX_AUDIT_FILES:
        return AuditResult(
            "failed",
            f"audit has {len(sources)} files; maximum is {MAX_AUDIT_FILES}",
            detail="resource.limit",
        )
    if normalized.out_root is None:
        return AuditResult(
            "failed", "a report folder is required for durable audit results",
            detail="output.required",
        )
    try:
        report = audit(sources, normalized, progress=progress, cancelled=cancelled)
        written = write_reports(report, normalized.out_root, cancelled=cancelled)
    except CommandCancelled:
        raise
    except ImportError as ex:
        return AuditResult(
            "failed", f"asset audit dependency is unavailable: {ex}",
            detail="dep.missing",
        )
    except OSError as ex:
        return AuditResult(
            "failed", f"asset audit I/O failed: {ex}",
            detail="io.failed", retryable=True,
        )
    if written["error"]:
        return AuditResult(
            "failed", written["details"], detail=written["error_type"],
            retryable=written["retryable"],
        )
    data = written["data"]
    warnings = list(data.get("warnings") or [])
    return AuditResult(
        "audited",
        f"{report.issue_count()} issue(s) across {report.scanned} file(s)",
        scanned=report.scanned,
        issues=report.issue_count(),
        counts=_report_counts(report),
        resolution_histogram=report.resolution_histogram,
        out_path=data["html"],
        artifacts=list(data["artifacts"]),
        warnings=warnings,
        detail="degraded" if warnings else "",
    )


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False


def _payload_issue_count(payload: dict) -> int | None:
    counts = _payload_counts(payload)
    exact = payload.get("exact_dups")
    near = payload.get("near_dups")
    if counts is None or any(not isinstance(group, list) for group in exact + near):
        return None
    return (
        sum(len(group) for group in exact)
        + sum(len(group) for group in near)
        + sum(
            counts[key] for key in counts
            if key not in {"exact_duplicate_groups", "near_duplicate_groups"}
        )
    )


def validate_result(result: AuditResult, opts: AuditOptions,
                    cancelled: Cancelled = None) -> bool:
    """Validate the exact report set and JSON completion-marker provenance."""
    normalized, _ = normalized_options(opts)
    if (
        normalized is None or normalized.out_root is None
        or result.action != "audited" or result.retryable
        or not isinstance(result.artifacts, list) or len(result.artifacts) != 3
        or not isinstance(result.warnings, list)
    ):
        return False
    root = normalized.out_root
    finals = (
        (root / "audit.json", "json"),
        (root / "audit_issues.csv", "csv"),
        (root / "audit.html", "html"),
    )
    try:
        actual = [
            _artifact(path, path, kind, cancelled)
            for path, kind in finals
        ]
        if actual != result.artifacts or not _same_path(result.out_path, finals[2][0]):
            return False
        with finals[0][0].open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except CommandCancelled:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or payload.get("report_schema") != _REPORT_SCHEMA:
        return False
    counts = _payload_counts(payload)
    issues = _payload_issue_count(payload)
    if (
        counts is None or issues is None
        or payload.get("scanned") != result.scanned
        or not isinstance(payload.get("records"), list)
        or len(payload["records"]) != result.scanned
        or issues != result.issues
        or counts != result.counts
        or payload.get("resolution_histogram") != result.resolution_histogram
        or payload.get("warnings") != result.warnings
        or result.reason != f"{issues} issue(s) across {result.scanned} file(s)"
        or result.detail != ("degraded" if result.warnings else "")
    ):
        return False
    provenance = payload.get("report_artifacts")
    if not isinstance(provenance, dict):
        return False
    for record in actual[1:]:
        expected = provenance.get(record["kind"])
        if not isinstance(expected, dict):
            return False
        if any(expected.get(key) != record[key] for key in ("path", "bytes", "sha256")):
            return False
    expected_options = {
        "near_dup_hamming": normalized.near_dup_hamming,
        "oversized_mb": normalized.oversized_mb,
        "min_dimension": normalized.min_dimension,
        "check_health": normalized.check_health,
        "dhash_size": normalized.dhash_size,
    }
    return payload.get("options") == expected_options


def _render_csv(report: AuditReport, cancelled: Cancelled = None) -> str:
    """One row per issue: issue_type, path, detail. The flat, greppable view."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["issue_type", "path", "detail"])
    for i, group in enumerate(report.exact_dups, 1):
        _cancelled(cancelled, "report-csv")
        for p in group:
            w.writerow(["exact_duplicate", p, f"group {i}"])
    for i, group in enumerate(report.near_dups, 1):
        _cancelled(cancelled, "report-csv")
        for p in group:
            w.writerow(["near_duplicate", p, f"group {i}"])
    for c in report.corrupt:
        w.writerow(["corrupt", c["path"], c["reason"]])
    for p in report.unsafe_names:
        w.writerow(["unsafe_name", p, ""])
    for p in report.empty_files:
        w.writerow(["empty_file", p, "0 bytes"])
    for o in report.oversized:
        w.writerow(["oversized", o["path"], f"{o['size_mb']} MB"])
    for d in report.empty_folders:
        w.writerow(["empty_folder", d, ""])
    for timg in report.tiny_images:
        w.writerow(["tiny_image", timg["path"], timg["dims"]])
    for hf in report.health_flags:
        w.writerow(["health", hf["path"], ", ".join(hf["flags"])])
    return buf.getvalue()


# --- self-contained HTML report -----------------------------------------------

_HTML_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 32px; background: #0B0F19; color: #F3F4F6;
       font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 12px; padding-bottom: 6px;
     border-bottom: 1px solid #1F2937; }
.sub { color: #9CA3AF; margin: 0 0 24px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; }
.card { background: #111827; border: 1px solid #1F2937; border-radius: 8px;
        padding: 12px 16px; min-width: 120px; }
.card .n { font-size: 22px; font-weight: 700; }
.card .l { color: #9CA3AF; font-size: 12px; }
.card.warn .n { color: #FBBF24; }
.card.bad .n { color: #FCA5A5; }
.card.ok .n { color: #34D399; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 8px;
        font-size: 13px; overflow-x: auto; display: block; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #1F2937;
         white-space: nowrap; }
th { color: #9CA3AF; font-weight: 600; }
td.path { white-space: normal; word-break: break-all; color: #F3F4F6; }
.group { background: #111827; border: 1px solid #1F2937; border-radius: 8px;
         padding: 10px; margin-bottom: 10px; display: flex; flex-wrap: wrap;
         gap: 12px; align-items: flex-start; }
.thumb { width: 96px; height: 96px; object-fit: contain; background: #0B0F19;
         border: 1px solid #1F2937; border-radius: 4px; }
.g-item { display: flex; flex-direction: column; align-items: center;
          max-width: 140px; }
.g-item .cap { font-size: 11px; color: #9CA3AF; word-break: break-all;
               margin-top: 4px; text-align: center; }
.tag { color: #FBBF24; }
.none { color: #34D399; }
.foot { color: #6B7280; font-size: 12px; margin-top: 32px; }
"""


def _esc(text: str) -> str:
    return html.escape(str(text))


def _render_html(report: AuditReport,
                 cancelled: Cancelled = None) -> tuple[str, list[str]]:
    """A single self-contained HTML page (inline CSS, inline base64 thumbnails).
    No external requests — it opens the same anywhere, offline."""
    r = report
    warnings: list[str] = []
    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Asset Audit</title><style>", _HTML_CSS, "</style></head><body>",
        "<h1>Asset Audit</h1>",
        f"<p class='sub'>{r.scanned} file(s) scanned · {r.issue_count()} issue(s) found</p>",
    ]

    # summary cards
    parts.append("<div class='cards'>")
    summary = [
        ("Exact dups", sum(len(g) for g in r.exact_dups), "warn"),
        ("Near dups", sum(len(g) for g in r.near_dups), "warn"),
        ("Corrupt", len(r.corrupt), "bad"),
        ("Unsafe names", len(r.unsafe_names), "bad"),
        ("Empty files", len(r.empty_files), "warn"),
        ("Oversized", len(r.oversized), "warn"),
        ("Empty folders", len(r.empty_folders), "warn"),
        ("Tiny images", len(r.tiny_images), "warn"),
        ("Health flags", len(r.health_flags), "warn"),
    ]
    for label, n, kind in summary:
        cls = kind if n else "ok"
        parts.append(f"<div class='card {cls}'><div class='n'>{n}</div>"
                     f"<div class='l'>{_esc(label)}</div></div>")
    parts.append("</div>")

    _dup_section(parts, "Exact duplicates", r.exact_dups,
                 "Byte-identical files (same sha256).", warnings, cancelled)
    _dup_section(parts, "Near duplicates", r.near_dups,
                 f"dHash Hamming distance &le; {r.options['near_dup_hamming']}.",
                 warnings, cancelled)

    _table_section(parts, "Corrupt / unreadable", ["Path", "Reason"],
                   [[c["path"], c["reason"]] for c in r.corrupt])
    _table_section(parts, "Unsafe filenames", ["Path"], [[p] for p in r.unsafe_names])
    _table_section(parts, "Empty files (0 bytes)", ["Path"], [[p] for p in r.empty_files])
    _table_section(parts, "Oversized files", ["Path", "Size"],
                   [[o["path"], f"{o['size_mb']} MB"] for o in r.oversized])
    _table_section(parts, "Empty folders", ["Path"], [[d] for d in r.empty_folders])
    _table_section(parts, "Tiny images", ["Path", "Dimensions"],
                   [[t["path"], t["dims"]] for t in r.tiny_images])
    _table_section(parts, "Image health flags", ["Path", "Flags", "Brightness", "Contrast"],
                   [[h["path"], ", ".join(h["flags"]),
                     "" if h["brightness"] is None else f"{h['brightness']:.2f}",
                     "" if h["contrast"] is None else f"{h['contrast']:.2f}"]
                    for h in r.health_flags])
    _table_section(parts, "Scan warnings", ["Detail"], [[item] for item in r.scan_warnings])

    # resolution histogram
    parts.append("<h2>Resolution histogram</h2>")
    if r.resolution_histogram:
        rows = "".join(f"<tr><td>{_esc(k)}</td><td>{v}</td></tr>"
                       for k, v in r.resolution_histogram.items())
        parts.append(f"<table><tr><th>Resolution</th><th>Count</th></tr>{rows}</table>")
    else:
        parts.append("<p class='none'>No readable images.</p>")

    parts.append("<p class='foot'>Generated by KS ToolBox · Asset Auditor · "
                 "deterministic (no AI, no network) · read-only audit.</p>")
    parts.append("</body></html>")
    return "".join(parts), warnings


def _dup_section(parts: list[str], title: str, groups: list[list[str]], desc: str,
                 warnings: list[str], cancelled: Cancelled = None) -> None:
    parts.append(f"<h2>{_esc(title)}</h2><p class='sub'>{desc}</p>")
    if not groups:
        parts.append("<p class='none'>None found.</p>")
        return
    for i, group in enumerate(groups, 1):
        _cancelled(cancelled, "report-html")
        parts.append(f"<div class='group'><div class='g-item cap'>group {i}</div>")
        for path in group:
            uri, warning = _thumb_data_uri(path, cancelled=cancelled)
            if warning:
                if len(warnings) < _MAX_REPORT_WARNINGS:
                    warnings.append(warning)
                elif len(warnings) == _MAX_REPORT_WARNINGS:
                    warnings.append("additional thumbnail warnings omitted from the bounded report")
            img = f"<img class='thumb' src='{uri}' alt=''>" if uri else \
                  "<div class='thumb'></div>"
            parts.append(f"<div class='g-item'>{img}"
                         f"<div class='cap'>{_esc(Path(path).name)}</div></div>")
        parts.append("</div>")


def _table_section(parts: list[str], title: str, headers: list[str],
                   rows: list[list[str]]) -> None:
    parts.append(f"<h2>{_esc(title)}</h2>")
    if not rows:
        parts.append("<p class='none'>None found.</p>")
        return
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join(
            f"<td class='path'>{_esc(v)}</td>" if j == 0 else f"<td>{_esc(v)}</td>"
            for j, v in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    parts.append(f"<table><tr>{head}</tr>{''.join(body)}</table>")


# --- HDD-gentle 3-stage exact duplicate scanner (all file types) -------------

DEFAULT_SKIP_DIRS = frozenset({
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash-1000",
})


@dataclass
class DupScanOptions:
    out_root: Path | None = None
    include_exts: frozenset[str] | None = None   # lowercase '.ext' filter, or None = all files
    min_size_bytes: int = 1                      # ignore 0-byte files by default (reported separately)
    sample_bytes: int = 64 * 1024                # head/mid/tail partial hash window
    chunk_bytes: int = 4 * 1024 * 1024           # large sequential read buffer for HDD friendliness
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS


@dataclass
class DupGroup:
    sha256: str
    size_bytes: int
    paths: list[str]

    @property
    def reclaimable_bytes(self) -> int:
        return max(0, len(self.paths) - 1) * self.size_bytes

    def to_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "count": len(self.paths),
            "reclaimable_bytes": self.reclaimable_bytes,
            "paths": self.paths,
        }


@dataclass
class DupScanReport:
    roots: list[str]
    total_files: int
    total_bytes: int
    empty_files: list[str]
    unique_size_skipped: int
    size_candidates: int
    partial_candidates: int
    bytes_read_partial: int
    bytes_read_full: int
    groups: list[DupGroup]
    scan_warnings: list[str]
    hardlinks_skipped: int = 0

    @property
    def total_duplicate_files(self) -> int:
        return sum(max(0, len(g.paths) - 1) for g in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.reclaimable_bytes for g in self.groups)

    def to_dict(self) -> dict:
        return {
            "roots": self.roots,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "empty_files_count": len(self.empty_files),
            "empty_files": self.empty_files[:1000],
            "unique_size_skipped": self.unique_size_skipped,
            "hardlinks_skipped": self.hardlinks_skipped,
            "size_candidates": self.size_candidates,
            "partial_candidates": self.partial_candidates,
            "bytes_read_partial": self.bytes_read_partial,
            "bytes_read_full": self.bytes_read_full,
            "duplicate_groups": len(self.groups),
            "total_duplicate_files": self.total_duplicate_files,
            "reclaimable_bytes": self.reclaimable_bytes,
            "reclaimable_gb": round(self.reclaimable_bytes / 1e9, 3),
            "scan_warnings": self.scan_warnings,
            "groups": [g.to_dict() for g in self.groups],
        }


def partial_fingerprint(
    path: str | Path,
    size_bytes: int,
    *,
    sample_bytes: int = 64 * 1024,
    cancelled: Cancelled = None,
) -> tuple[str, bool, int]:
    """Read head+mid+tail windows (or full file when small) in ascending offset order.

    Returns ``(hex_digest, is_full_sha256, bytes_read)``. When ``size_bytes <=
    sample_bytes * 3``, the entire file is hashed in one sequential pass so
    Stage 3 does not need to reopen it.
    """
    _cancelled(cancelled, "dup-partial", path)
    p = Path(path)
    if size_bytes <= sample_bytes * 3:
        return sha256_file(p, chunk_size=max(sample_bytes, 256 * 1024), cancelled=cancelled), True, size_bytes

    digest = hashlib.sha256()
    bytes_read = 0
    offsets = (0, max(0, (size_bytes - sample_bytes) // 2), max(0, size_bytes - sample_bytes))
    with p.open("rb") as fh:
        last_end = -1
        for off in offsets:
            _cancelled(cancelled, "dup-partial", p)
            if off != last_end:
                fh.seek(off)
            chunk = fh.read(sample_bytes)
            bytes_read += len(chunk)
            last_end = off + len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), False, bytes_read


def _scan_size_buckets(
    roots: Iterable[str | Path],
    opts: DupScanOptions,
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Cancelled = None,
) -> tuple[int, int, dict[int, list[str]], list[str], list[str], dict[str, tuple[int, int]]]:
    """Single-threaded os.scandir walk collecting size buckets with zero file opens."""
    by_size: dict[int, list[str]] = {}
    inodes: dict[str, tuple[int, int]] = {}
    empty_files: list[str] = []
    warnings: list[str] = []
    total_files = 0
    total_bytes = 0
    exts = opts.include_exts

    def _record_stat(fpath: str, st: os.stat_result) -> None:
        nonlocal total_files, total_bytes
        sz = st.st_size
        total_files += 1
        total_bytes += sz
        if sz == 0:
            empty_files.append(fpath)
        elif sz >= opts.min_size_bytes:
            by_size.setdefault(sz, []).append(fpath)
            if st.st_ino:
                inodes[fpath] = (st.st_dev, st.st_ino)
        if progress is not None and total_files % 5000 == 0:
            progress("walk", total_files, 0)

    def walk(dir_path: str) -> None:
        subdirs: list[str] = []
        try:
            with os.scandir(dir_path) as it:
                entries = sorted(it, key=lambda en: en.name.lower())
        except OSError as ex:
            if len(warnings) < _MAX_REPORT_WARNINGS:
                warnings.append(f"directory skipped ({dir_path}): {ex}")
            return
        for entry in entries:
            _cancelled(cancelled, "dup-scan", entry.path)
            try:
                is_junc = getattr(entry, "is_junction", None)
                if entry.is_dir(follow_symlinks=False) and not (is_junc and is_junc()):
                    if entry.name not in opts.skip_dirs:
                        subdirs.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    if exts is not None:
                        _, ext = os.path.splitext(entry.name.lower())
                        if ext not in exts:
                            continue
                    _record_stat(entry.path, entry.stat(follow_symlinks=False))
            except OSError as ex:
                if len(warnings) < _MAX_REPORT_WARNINGS:
                    warnings.append(f"entry skipped ({entry.path}): {ex}")
        for sd in subdirs:
            walk(sd)

    for root in roots:
        rp = Path(root)
        if rp.is_file():
            _record_stat(str(rp), rp.stat())
        elif rp.is_dir():
            walk(str(rp))
    return total_files, total_bytes, by_size, empty_files, warnings, inodes


def _dedupe_hardlinks(
    paths: list[str],
    inodes: dict[str, tuple[int, int]],
) -> tuple[list[str], int]:
    """Keep one representative path per physical (st_dev, st_ino); return (kept, skipped_count)."""
    seen: set[tuple[int, int]] = set()
    kept: list[str] = []
    skipped = 0
    for p in sorted(paths):
        key = inodes.get(p)
        if key is None:
            try:
                st = os.stat(p, follow_symlinks=False)
                if st.st_ino:
                    key = (st.st_dev, st.st_ino)
            except OSError:
                key = None
        if key is not None:
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
        kept.append(p)
    return kept, skipped


def find_exact_duplicates(
    roots: Iterable[str | Path],
    opts: DupScanOptions | None = None,
    *,
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Cancelled = None,
) -> DupScanReport:
    """HDD-gentle 3-stage exact duplicate finder: size -> partial hash -> full SHA-256."""
    cfg = opts or DupScanOptions()
    root_list = [str(Path(r)) for r in roots]
    total_files, total_bytes, by_size, empty_files, warnings, inodes = _scan_size_buckets(
        root_list, cfg, progress=progress, cancelled=cancelled
    )

    unique_size_skipped = 0
    partial_work: list[tuple[str, int]] = []
    for sz, paths in by_size.items():
        if len(paths) == 1:
            unique_size_skipped += 1
            continue
        for p in paths:
            partial_work.append((p, sz))
    # Global path order preserves directory/MFT locality on spinning HDDs.
    partial_work.sort(key=lambda item: item[0])
    size_candidates = len(partial_work)

    # Stage 2: partial fingerprint (or direct full sha256 for tiny files)
    partial_buckets: dict[tuple[int, str, bool], list[str]] = {}
    bytes_read_partial = 0
    for done_partial, (p, sz) in enumerate(partial_work, 1):
        _cancelled(cancelled, "dup-partial", p)
        try:
            fp, is_full, read_n = partial_fingerprint(
                p, sz, sample_bytes=cfg.sample_bytes, cancelled=cancelled
            )
            bytes_read_partial += read_n
            partial_buckets.setdefault((sz, fp, is_full), []).append(p)
        except CommandCancelled:
            raise
        except OSError as ex:
            if len(warnings) < _MAX_REPORT_WARNINGS:
                warnings.append(f"partial read failed ({p}): {ex}")
        if progress is not None and (done_partial % 200 == 0 or done_partial == size_candidates):
            progress("partial", done_partial, size_candidates)

    # Dedupe hardlinks only after Stage 2 (avoids millions of random CreateFile/stat calls on Windows).
    groups: list[DupGroup] = []
    full_needed: list[tuple[int, list[str]]] = []
    hardlinks_skipped = 0
    for (sz, fp, is_full), paths in sorted(partial_buckets.items(), key=lambda kv: kv[1][0]):
        if len(paths) < 2:
            continue
        physical, hl_skip = _dedupe_hardlinks(paths, inodes)
        hardlinks_skipped += hl_skip
        if len(physical) < 2:
            continue
        if is_full:
            groups.append(DupGroup(sha256=fp, size_bytes=sz, paths=physical))
        else:
            full_needed.append((sz, physical))

    # Stage 3: full SHA-256 only on multi-file partial-hash collisions (also path-ordered for HDD locality)
    full_work: list[tuple[str, int, int]] = []
    for bucket_id, (sz, paths) in enumerate(full_needed):
        for p in paths:
            full_work.append((p, sz, bucket_id))
    full_work.sort(key=lambda item: item[0])
    partial_candidates = len(full_work)
    bytes_read_full = 0
    sha_by_bucket: dict[int, dict[str, list[str]]] = {}
    for done_full, (p, sz, bucket_id) in enumerate(full_work, 1):
        _cancelled(cancelled, "dup-sha256", p)
        try:
            digest = sha256_file(p, chunk_size=cfg.chunk_bytes, cancelled=cancelled)
            bytes_read_full += sz
            sha_by_bucket.setdefault(bucket_id, {}).setdefault(digest, []).append(p)
        except CommandCancelled:
            raise
        except OSError as ex:
            if len(warnings) < _MAX_REPORT_WARNINGS:
                warnings.append(f"full hash failed ({p}): {ex}")
        if progress is not None and (done_full % 50 == 0 or done_full == partial_candidates):
            progress("full", done_full, partial_candidates)

    for bucket_id, sha_buckets in sha_by_bucket.items():
        sz = full_needed[bucket_id][0]
        for digest, gpaths in sha_buckets.items():
            if len(gpaths) > 1:
                groups.append(DupGroup(sha256=digest, size_bytes=sz, paths=sorted(gpaths)))

    groups.sort(key=lambda g: (-g.reclaimable_bytes, -g.size_bytes, g.paths[0]))
    report = DupScanReport(
        roots=root_list,
        total_files=total_files,
        total_bytes=total_bytes,
        empty_files=sorted(empty_files),
        unique_size_skipped=unique_size_skipped,
        size_candidates=size_candidates,
        partial_candidates=partial_candidates,
        bytes_read_partial=bytes_read_partial,
        bytes_read_full=bytes_read_full,
        groups=groups,
        scan_warnings=warnings,
        hardlinks_skipped=hardlinks_skipped,
    )
    if cfg.out_root is not None:
        write_dup_reports(report, cfg.out_root, cancelled=cancelled)
    return report


def write_dup_reports(
    report: DupScanReport,
    out_dir: str | Path,
    *,
    cancelled: Cancelled = None,
) -> dict:
    """Atomically write dupes.csv, dupes.html, and dupes.json to *out_dir*."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path, html_path, json_path = out / "dupes.csv", out / "dupes.html", out / "dupes.json"
    csv_part, html_part, json_part = (
        p.with_name(p.name + ".part") for p in (csv_path, html_path, json_path)
    )
    parts = (csv_part, html_part, json_part)
    try:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["group_id", "size_bytes", "reclaimable_bytes", "sha256", "path"])
        for gid, grp in enumerate(report.groups, 1):
            _cancelled(cancelled, "dup-report-csv")
            for p in grp.paths:
                w.writerow([gid, grp.size_bytes, grp.reclaimable_bytes, grp.sha256, p])
        csv_part.write_text(buf.getvalue(), encoding="utf-8", newline="")

        rows_html = []
        for gid, grp in enumerate(report.groups[:2000], 1):
            _cancelled(cancelled, "dup-report-html")
            mb = grp.size_bytes / 1e6
            rec_mb = grp.reclaimable_bytes / 1e6
            paths_html = "<br>".join(_esc(p) for p in grp.paths)
            rows_html.append(
                f"<tr><td>{gid}</td><td>{mb:.2f} MB</td><td>{rec_mb:.2f} MB</td>"
                f"<td class='path'>{paths_html}</td></tr>"
            )
        html_doc = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>Exact Duplicates Report</title><style>{_HTML_CSS}</style></head><body>"
            "<h1>Exact Duplicates Report</h1>"
            f"<p class='sub'>{report.total_files:,} files scanned ({report.total_bytes/1e9:.2f} GB) · "
            f"{len(report.groups):,} duplicate groups · {report.total_duplicate_files:,} extra files · "
            f"{report.reclaimable_bytes/1e9:.2f} GB reclaimable</p>"
            f"<table><tr><th>Group</th><th>File Size</th><th>Reclaimable</th><th>Paths</th></tr>"
            f"{''.join(rows_html)}</table>"
            "</body></html>"
        )
        html_part.write_text(html_doc, encoding="utf-8", newline="")
        json_part.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8", newline="")
        csv_part.replace(csv_path)
        html_part.replace(html_path)
        json_part.replace(json_path)
        return ok({"json": str(json_path), "csv": str(csv_path), "html": str(html_path)})
    except CommandCancelled:
        _remove_parts(parts)
        raise
    except OSError as ex:
        _remove_parts(parts)
        return err("report.write", f"failed writing dup reports: {ex}", retryable=True)


@dataclass
class DupCleanItem:
    group_sha256: str
    keep_path: str
    dup_path: str
    size_bytes: int
    status: str       # dry_run_ok | removed | skipped | rejected
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _pick_keep_path(
    paths: list[str],
    strategy: str,
    *,
    prefer_prefixes: tuple[str, ...] = (),
    avoid_prefixes: tuple[str, ...] = (),
) -> str:
    pref = tuple(str(Path(p)).casefold() for p in prefer_prefixes)
    avoid = tuple(str(Path(p)).casefold() for p in avoid_prefixes)

    def rank(p: str) -> tuple:
        low = str(Path(p)).casefold()
        p_tier = 0 if (pref and any(low.startswith(x) for x in pref)) else 1
        a_tier = 1 if (avoid and any(low.startswith(x) for x in avoid)) else 0
        mtime = os.stat(p).st_mtime
        if strategy == "oldest":
            return (p_tier, a_tier, mtime, len(p), low)
        return (p_tier, a_tier, len(p), mtime, low)

    return min(paths, key=rank)


def cleanup_exact_duplicates(
    report_or_json: DupScanReport | str | Path,
    *,
    keep_strategy: str = "shortest_path",
    prefer_prefixes: tuple[str, ...] = (),
    avoid_prefixes: tuple[str, ...] = (),
    verify_sha: bool = True,
    min_size_bytes: int = 1,
    permanent_delete: bool = False,
    dry_run: bool = True,
    chunk_bytes: int = 4 * 1024 * 1024,
    progress: Callable[[int, int, DupCleanItem], None] | None = None,
    cancelled: Cancelled = None,
) -> list[DupCleanItem]:
    """Re-verify exact duplicate groups and safely remove extra copies (keeping 1)."""
    if isinstance(report_or_json, DupScanReport):
        groups = report_or_json.groups
    else:
        payload = json.loads(Path(report_or_json).read_text(encoding="utf-8"))
        groups = [
            DupGroup(
                sha256=str(g["sha256"]),
                size_bytes=int(g["size_bytes"]),
                paths=[str(p) for p in g["paths"]],
            )
            for g in payload.get("groups", [])
        ]

    results: list[DupCleanItem] = []
    total = len(groups)
    for idx, grp in enumerate(groups, 1):
        _cancelled(cancelled, "dup-clean", grp.paths[0] if grp.paths else "")
        if grp.size_bytes < min_size_bytes:
            continue
        live: list[tuple[str, tuple[int, int] | None]] = []
        for p in grp.paths:
            try:
                st = Path(p).stat()
            except OSError:
                continue
            if Path(p).is_file() and st.st_size == grp.size_bytes:
                ino_key = (st.st_dev, st.st_ino) if st.st_ino else None
                live.append((p, ino_key))

        # Collapse hardlinks so we never count or delete hardlink-only groups.
        seen_inos: set[tuple[int, int]] = set()
        distinct: list[str] = []
        ino_by_path: dict[str, tuple[int, int] | None] = {}
        for p, ikey in live:
            if ikey is not None:
                if ikey in seen_inos:
                    continue
                seen_inos.add(ikey)
            distinct.append(p)
            ino_by_path[p] = ikey

        if len(distinct) < 2:
            continue

        keep = _pick_keep_path(
            distinct,
            keep_strategy,
            prefer_prefixes=prefer_prefixes,
            avoid_prefixes=avoid_prefixes,
        )
        extras = [p for p in distinct if p != keep]

        if verify_sha:
            try:
                keep_sha = sha256_file(keep, chunk_size=chunk_bytes, cancelled=cancelled)
            except CommandCancelled:
                raise
            except OSError as ex:
                for ep in extras:
                    item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "rejected", f"keep unreadable: {ex}")
                    results.append(item)
                continue
            if keep_sha != grp.sha256:
                for ep in extras:
                    item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "rejected", "keep SHA-256 mismatch")
                    results.append(item)
                continue

        for ep in extras:
            _cancelled(cancelled, "dup-clean-file", ep)
            if ino_by_path.get(keep) is not None and ino_by_path.get(ep) == ino_by_path.get(keep):
                item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "skipped", "hardlink to kept file")
                results.append(item)
                continue
            if verify_sha:
                try:
                    ep_sha = sha256_file(ep, chunk_size=chunk_bytes, cancelled=cancelled)
                except CommandCancelled:
                    raise
                except OSError as ex:
                    results.append(DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "rejected", f"unreadable: {ex}"))
                    continue
                if ep_sha != grp.sha256:
                    results.append(DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "rejected", "SHA-256 mismatch"))
                    continue
            if dry_run:
                item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "dry_run_ok", "verified duplicate ready to remove")
            elif _safe_remove_dup(Path(ep), permanent=permanent_delete):
                item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "removed", "duplicate removed")
            else:
                item = DupCleanItem(grp.sha256, keep, ep, grp.size_bytes, "rejected", "failed to remove duplicate")
            results.append(item)
            if progress is not None:
                progress(idx, total, item)
    return results


def _atomic_write_text(dst: Path, text: str) -> None:
    tmp = dst.with_name(f"{dst.name}.part")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _render_drive_index_html(root: str, entries: list[dict[str, Any]], max_depth: int) -> str:
    sections = sorted({e["section"] for e in entries if e["section"] != "/"})
    data_json = json.dumps(entries, ensure_ascii=False).replace("</", "<\\/")
    sec_json = json.dumps(sections, ensure_ascii=False).replace("</", "<\\/")
    esc_root = html.escape(root)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Drive Finder — {esc_root}</title>
<style>
  :root {{ --bg:#111317; --card:#1a1e25; --fg:#eef1f6; --muted:#9aa3b2; --acc:#4ea1ff; --bd:#2b313c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); padding:24px; }}
  .wrap {{ max-width:1280px; margin:0 auto; }}
  h1 {{ margin:0 0 6px; font-size:22px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:16px; }}
  .bar {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }}
  input {{ flex:1 1 320px; padding:11px 14px; border-radius:8px; border:1px solid var(--bd); background:var(--card); color:var(--fg); font-size:15px; }}
  select, button {{ padding:9px 12px; border-radius:8px; border:1px solid var(--bd); background:var(--card); color:var(--fg); cursor:pointer; font-size:13px; }}
  button:hover, select:hover {{ border-color:var(--acc); }}
  .pills {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }}
  .pill {{ padding:5px 10px; border-radius:999px; background:var(--card); border:1px solid var(--bd); color:var(--muted); font-size:12px; cursor:pointer; }}
  .pill.active {{ background:var(--acc); color:#0b111a; border-color:var(--acc); font-weight:600; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:10px; overflow:hidden; font-size:13px; }}
  th, td {{ padding:8px 11px; border-bottom:1px solid var(--bd); text-align:left; }}
  th {{ color:var(--muted); font-weight:600; background:#15181e; }}
  a {{ color:var(--acc); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .tag {{ display:inline-block; padding:2px 7px; border-radius:5px; font-size:11px; background:#232934; color:var(--muted); }}
  .path {{ font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#cfd6e4; word-break:break-all; }}
  .cnt {{ color:var(--muted); font-size:12px; margin-bottom:8px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Drive Finder — <code>{esc_root}</code></h1>
  <div class="sub">Offline instant search across folders (depth &le; {max_depth}) and standalone packages/files — zero disk scanning when searching. Press <b>/</b> to focus search.</div>
  <div class="bar">
    <input id="q" type="search" placeholder="Search courses, assets, Blender, Unity, Substance, ZIPs, MP4s..." autofocus>
    <select id="kind">
      <option value="all">All (Folders + Files)</option>
      <option value="dir">Folders Only</option>
      <option value="file">Standalone Files Only</option>
    </select>
  </div>
  <div class="pills" id="pills"></div>
  <div class="cnt" id="cnt"></div>
  <table>
    <thead><tr><th>Type</th><th>Section</th><th>Path (click to open)</th><th>Direct Items</th><th>Action</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<script>
const ENTRIES = {data_json};
const SECTIONS = {sec_json};
let activeSec = 'ALL';
const qEl = document.getElementById('q');
const kindEl = document.getElementById('kind');
const rowsEl = document.getElementById('rows');
const cntEl = document.getElementById('cnt');
const pillsEl = document.getElementById('pills');

function buildPills() {{
  const mk = (label, val) => {{
    const b = document.createElement('span');
    b.className = 'pill' + (activeSec === val ? ' active' : '');
    b.textContent = label;
    b.onclick = () => {{ activeSec = val; buildPills(); render(); }};
    return b;
  }};
  pillsEl.replaceChildren(mk('All Sections', 'ALL'), ...SECTIONS.map(s => mk(s, s)));
}}

function copyPath(p, btn) {{
  if (navigator.clipboard) navigator.clipboard.writeText(p);
  const old = btn.textContent;
  btn.textContent = 'Copied';
  setTimeout(() => btn.textContent = old, 900);
}}

function render() {{
  const terms = qEl.value.trim().toLowerCase().split(/\\s+/).filter(Boolean);
  const k = kindEl.value;
  const hits = [];
  for (let i = 0; i < ENTRIES.length; i++) {{
    const e = ENTRIES[i];
    if (k !== 'all' && e.kind !== k) continue;
    if (activeSec !== 'ALL' && e.section !== activeSec) continue;
    if (terms.length) {{
      const hay = (e.rel + ' ' + e.section).toLowerCase();
      let ok = true;
      for (const t of terms) if (!hay.includes(t)) {{ ok = false; break; }}
      if (!ok) continue;
    }}
    hits.push(e);
  }}
  const shown = hits.slice(0, 300);
  cntEl.textContent = `Showing ${{shown.length}} of ${{hits.length}} matching items (${{ENTRIES.length}} indexed)`;
  const frag = document.createDocumentFragment();
  for (const e of shown) {{
    const tr = document.createElement('tr');
    const td1 = document.createElement('td');
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.textContent = e.kind;
    td1.appendChild(tag);
    const td2 = document.createElement('td');
    td2.textContent = e.section;
    const td3 = document.createElement('td');
    td3.className = 'path';
    const a = document.createElement('a');
    a.href = e.url;
    a.textContent = e.rel;
    td3.appendChild(a);
    const td4 = document.createElement('td');
    td4.textContent = e.kind === 'dir'
      ? `${{e.subdirs}} folders, ${{e.files}} files (${{(e.size_bytes/1e6).toFixed(1)}} MB direct)`
      : `${{(e.size_bytes/1e6).toFixed(1)}} MB`;
    const td5 = document.createElement('td');
    const btn = document.createElement('button');
    btn.textContent = 'Copy Path';
    btn.onclick = () => copyPath(e.full, btn);
    td5.appendChild(btn);
    tr.append(td1, td2, td3, td4, td5);
    frag.appendChild(tr);
  }}
  rowsEl.replaceChildren(frag);
}}

document.addEventListener('keydown', e => {{ if (e.key === '/' && document.activeElement !== qEl) {{ e.preventDefault(); qEl.focus(); }} }});
qEl.addEventListener('input', render);
kindEl.addEventListener('change', render);
buildPills();
render();
</script>
</body>
</html>"""


def _render_drive_map_md(root: str, entries: list[dict[str, Any]], max_depth: int) -> str:
    top = [e for e in entries if e["kind"] == "dir" and e["depth"] == 1]
    by_sec: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if e["kind"] == "dir" and e["depth"] == 2:
            by_sec.setdefault(e["section"], []).append(e)
    lines = [
        f"# Drive Map — `{root}`",
        "",
        f"Indexed sequentially (`max_depth={max_depth}`) without deep platter scans.",
        "Open `00_FIND_EVERYTHING.html` in any browser for instant live keyword search.",
        "",
        "## Top-Level Folders",
        "",
    ]
    for e in top:
        subs = len(by_sec.get(e["section"], []))
        lines.append(f"- **`{e['section']}`** — {subs} main subfolders, {e['files']} direct files (`{e['full']}`)")
    lines.append("")
    lines.append("## Subfolders by Section")
    lines.append("")
    for e in top:
        subs = by_sec.get(e["section"], [])
        if not subs:
            continue
        lines.append(f"### {e['section']}")
        for s in subs[:80]:
            name = Path(s["rel"]).name
            lines.append(f"- `{name}` ({s['subdirs']} subfolders, {s['files']} direct files)")
        if len(subs) > 80:
            lines.append(f"- ... ({len(subs) - 80} more in `00_FIND_EVERYTHING.html`)")
        lines.append("")
    return "\n".join(lines)


def build_drive_index(
    root: str | Path,
    out_root: str | Path | None = None,
    *,
    max_depth: int = 3,
    include_files_depth: int = 2,
    min_file_bytes: int = 1024 * 1024,
    skip_dirs: frozenset[str] = DEFAULT_SKIP_DIRS,
    cancelled: Cancelled = None,
) -> dict[str, Any]:
    """Build an HDD-gentle bounded-depth catalog (`00_FIND_EVERYTHING.html` + `00_DRIVE_MAP.md`)."""
    root_p = Path(root)
    out_dir = Path(out_root) if out_root is not None else root_p
    entries: list[dict[str, Any]] = []

    def walk_bounded(dir_path: Path, depth: int, section: str) -> None:
        if depth > max_depth:
            return
        _cancelled(cancelled, "drive-index", str(dir_path))
        try:
            with os.scandir(dir_path) as it:
                items = sorted(it, key=lambda en: en.name.lower())
        except OSError:
            return
        subdirs: list[tuple[Path, str]] = []
        direct_files = 0
        direct_bytes = 0
        file_entries: list[dict[str, Any]] = []
        for en in items:
            try:
                is_junc = getattr(en, "is_junction", None)
                if en.is_dir(follow_symlinks=False) and not (is_junc and is_junc()):
                    if en.name not in skip_dirs:
                        sub_sec = en.name if depth == 0 else section
                        subdirs.append((Path(en.path), sub_sec))
                elif en.is_file(follow_symlinks=False):
                    st = en.stat(follow_symlinks=False)
                    direct_files += 1
                    direct_bytes += st.st_size
                    if depth <= include_files_depth and st.st_size >= min_file_bytes:
                        fp = Path(en.path)
                        rel = str(fp.relative_to(root_p))
                        file_entries.append({
                            "kind": "file",
                            "section": section if depth > 0 else "/",
                            "rel": rel,
                            "full": str(fp),
                            "url": fp.as_uri(),
                            "depth": depth + 1,
                            "files": 1,
                            "subdirs": 0,
                            "size_bytes": st.st_size,
                        })
            except OSError:
                continue

        if depth > 0:
            rel = str(dir_path.relative_to(root_p))
            entries.append({
                "kind": "dir",
                "section": section,
                "rel": rel,
                "full": str(dir_path),
                "url": dir_path.as_uri(),
                "depth": depth,
                "files": direct_files,
                "subdirs": len(subdirs),
                "size_bytes": direct_bytes,
            })
        entries.extend(file_entries)
        if depth < max_depth:
            for sd, sub_sec in subdirs:
                walk_bounded(sd, depth + 1, sub_sec)

    walk_bounded(root_p, 0, "/")
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "00_FIND_EVERYTHING.html"
    md_path = out_dir / "00_DRIVE_MAP.md"
    _atomic_write_text(html_path, _render_drive_index_html(str(root_p), entries, max_depth))
    _atomic_write_text(md_path, _render_drive_map_md(str(root_p), entries, max_depth))
    return {
        "root": str(root_p),
        "entries": len(entries),
        "dirs": sum(1 for e in entries if e["kind"] == "dir"),
        "files": sum(1 for e in entries if e["kind"] == "file"),
        "html_path": str(html_path),
        "md_path": str(md_path),
    }

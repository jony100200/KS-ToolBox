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
sources (see README credits): ChobiEngine's `image_fingerprint` (dHash bits),
`analysis_cv.collect_preflight_metrics` (corrupt/resolution/alpha health), and
RupayanFlow's `pack_core` (sha256_file, reserved-name/illegal-char validation).
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
import html
import io
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from toolbox.engine_common import CommandCancelled, IMAGE_EXTS, err, ok, sha256_file

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

# --- filename safety (lifted from RupayanFlow pack_core) ----------------------

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
    from ChobiEngine image_fingerprint, dropping its imagehash branch.)"""
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
    of RupayanFlow's ImageHeuristicsAnalyzer / ChobiEngine preflight.)"""
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

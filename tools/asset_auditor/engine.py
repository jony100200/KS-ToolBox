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
    audit(paths, opts, progress=None) -> AuditReport         (aggregate)
    write_reports(report, out_dir)    -> envelope{json,csv,html}
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

from toolbox.engine_common import IMAGE_EXTS, err, ok, sha256_file

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
                compute_dhash: bool = True, dhash_size: int = 8) -> dict:
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
        with p.open("rb") as fh:
            header = fh.read(16)
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
        with Image.open(p) as im:
            im.verify()
    except Exception as ex:                         # PIL raises many types
        rec["corrupt"] = True
        detail = "unrecognised header" if not rec["magic"] else str(ex)
        rec["reason"] = f"decode failed: {detail}"
        return rec

    try:
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


def inspect_file(path: str | Path, opts: "AuditOptions") -> FileRecord:
    """Gather every per-file signal for one path — size, hash, image check,
    name safety — into a FileRecord. Never raises; IO failures land as flags."""
    p = Path(path)
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
            rec.sha256 = sha256_file(p)             # exact-dup grouping key
        except OSError as ex:
            rec.reason = f"hash failed: {ex}"

    img = check_image(p, compute_health=opts.check_health, dhash_size=opts.dhash_size)
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
            "options": self.options,
            "records": [r.to_dict() for r in self.records],
        }


def _group_by_sha(records: list[FileRecord]) -> list[list[str]]:
    """Paths sharing a sha256 (non-empty hash), as groups of size >= 2, sorted."""
    buckets: dict[str, list[str]] = {}
    for r in records:
        if r.sha256:
            buckets.setdefault(r.sha256, []).append(r.path)
    return sorted((sorted(g) for g in buckets.values() if len(g) > 1))


def _group_near_dups(records: list[FileRecord], threshold: int) -> list[list[str]]:
    """Union-find over valid dHashes: any two within `threshold` bits join a
    group. O(n^2) pairwise — fine for the hundreds-to-thousands a folder holds;
    a folder in the tens of thousands would want an LSH index. (dHash + union-find
    approach from RupayanFlow's ks_image_similarity_audit.)"""
    cand = [r for r in records if r.dhash and not r.corrupt]
    n = len(cand)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if hamming(cand[i].dhash, cand[j].dhash) <= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(cand[i].path)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1))


def _find_empty_folders(root: Path | None) -> list[str]:
    """Directories under `root` that contain no entries at all (read-only walk).
    Best-effort — unreadable dirs are skipped, not silently swallowed as empty."""
    if not root:
        return []
    root = Path(root)
    if not root.is_dir():
        return []
    empties: list[str] = []
    for d in root.rglob("*"):
        try:
            if d.is_dir() and not any(d.iterdir()):
                empties.append(str(d))
        except OSError:
            continue
    return sorted(empties)


def audit(paths: Iterable[str | Path], opts: AuditOptions,
          progress: Callable[[int, int, str], None] | None = None) -> AuditReport:
    """Inspect every path and aggregate the findings into an AuditReport.

    `paths` are the files to inspect (the panel pre-filters to image extensions);
    `opts.scan_root`, if set, is walked read-only for empty folders. `progress`
    is called `(index, total, name)` per file so the UI can show a bar. Pure
    aggregate — no writes happen here; see write_reports."""
    paths = [Path(p) for p in paths]
    total = len(paths)
    records: list[FileRecord] = []
    for i, p in enumerate(paths, 1):
        records.append(inspect_file(p, opts))
        if progress:
            progress(i, total, p.name)

    resolution = Counter(
        f"{r.width}x{r.height}" for r in records if not r.corrupt and r.width and r.height
    )
    return AuditReport(
        scanned=total,
        records=records,
        exact_dups=_group_by_sha(records),
        near_dups=_group_near_dups(records, opts.near_dup_hamming),
        corrupt=[{"path": r.path, "reason": r.reason} for r in records if r.corrupt],
        unsafe_names=[r.path for r in records if r.unsafe_name],
        empty_files=[r.path for r in records if r.empty],
        oversized=[{"path": r.path, "size_mb": round(r.size_bytes / 1048576, 2)}
                   for r in records if r.oversized],
        empty_folders=_find_empty_folders(opts.scan_root),
        tiny_images=[{"path": r.path, "dims": f"{r.width}x{r.height}"}
                     for r in records if r.tiny],
        health_flags=[{"path": r.path, "flags": r.flags,
                       "brightness": r.brightness, "contrast": r.contrast}
                      for r in records if not r.corrupt and _health_flags(r.flags)],
        resolution_histogram=dict(sorted(resolution.items(), key=lambda kv: (-kv[1], kv[0]))),
        options={"near_dup_hamming": opts.near_dup_hamming, "oversized_mb": opts.oversized_mb,
                 "min_dimension": opts.min_dimension, "check_health": opts.check_health,
                 "dhash_size": opts.dhash_size},
    )


def _health_flags(flags: list[str]) -> list[str]:
    return [f for f in flags if f in ("very_dark", "very_bright", "low_contrast")]


# --- report writing (the tool's only output; atomic, non-destructive) ---------

def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a `.part` temp then os.replace — a killed write never leaves a
    half-written report in place (AGENTS.md §atomic writes)."""
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _thumb_data_uri(path: str, box: int = 96) -> str:
    """A small inline PNG data-URI thumbnail, or "" if the image can't be read
    (so the report stays self-contained — no external image requests). Failure is
    announced by the thumbnail's absence, never by a wrong/placeholder image."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((box, box), Image.BILINEAR)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def write_reports(report: AuditReport, out_dir: str | Path) -> dict:
    """Write audit.html + audit.json + audit_issues.csv to `out_dir`, atomically.
    Returns the standard envelope; data is {json, csv, html} paths. Read-only with
    respect to sources — the only files touched are the three reports."""
    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return err("output.dir", f"cannot create output folder {out}: {ex}")

    json_path = out / "audit.json"
    csv_path = out / "audit_issues.csv"
    html_path = out / "audit.html"
    try:
        _atomic_write(json_path,
                      json.dumps(report.to_dict(), indent=2, ensure_ascii=False).encode("utf-8"))
        _atomic_write(csv_path, _render_csv(report).encode("utf-8"))
        _atomic_write(html_path, _render_html(report).encode("utf-8"))
    except OSError as ex:
        return err("report.write", f"failed writing reports to {out}: {ex}")

    return ok({"json": str(json_path), "csv": str(csv_path), "html": str(html_path)},
              details=f"{report.issue_count()} issue(s) across {report.scanned} file(s)")


def _render_csv(report: AuditReport) -> str:
    """One row per issue: issue_type, path, detail. The flat, greppable view."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["issue_type", "path", "detail"])
    for i, group in enumerate(report.exact_dups, 1):
        for p in group:
            w.writerow(["exact_duplicate", p, f"group {i}"])
    for i, group in enumerate(report.near_dups, 1):
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


def _render_html(report: AuditReport) -> str:
    """A single self-contained HTML page (inline CSS, inline base64 thumbnails).
    No external requests — it opens the same anywhere, offline."""
    r = report
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
                 "Byte-identical files (same sha256).")
    _dup_section(parts, "Near duplicates", r.near_dups,
                 f"dHash Hamming distance &le; {r.options['near_dup_hamming']}.")

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
    return "".join(parts)


def _dup_section(parts: list[str], title: str, groups: list[list[str]], desc: str) -> None:
    parts.append(f"<h2>{_esc(title)}</h2><p class='sub'>{desc}</p>")
    if not groups:
        parts.append("<p class='none'>None found.</p>")
        return
    for i, group in enumerate(groups, 1):
        parts.append(f"<div class='group'><div class='g-item cap'>group {i}</div>")
        for path in group:
            uri = _thumb_data_uri(path)
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

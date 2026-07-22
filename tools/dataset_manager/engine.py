"""Dataset Manager engine — deterministic file management for image/caption datasets.

Pure logic, no UI, no global state, no network, no AI. Operates on a folder of
images plus optional sidecar caption files (`.txt` / `.caption`, matched by stem).

Design guarantees (the release bar):
    * NON-DESTRUCTIVE — sources are only ever read. Every write lands under a
      single output root via copies (`shutil.copy2`) or freshly written text.
      Nothing is moved or deleted, and originals are never edited in place.
    * DETERMINISTIC — the train/val/test split is a pure index-threshold over a
      sorted name list (no randomness), so the same inputs always split the same
      way. Bucketing and pairing are equally reproducible.
    * Preview-first — `dry_run` (the default) lists what WOULD be copied/changed
      without touching disk.

Pillow is imported lazily and only for reading image dimensions; a machine
without Pillow still pairs/splits/replaces (dimensions come back blank and the
report is flagged `degraded` — announced, never silently swallowed). sha256 uses
hashlib; all writes are atomic (temp `.part` then `os.replace`).

Public interface:
    pair_files(paths, caption_exts)      -> {"pairs","missing","orphans"}   (pure)
    bucket_for(w, h, mode)               -> str                              (pure)
    split_assign(sorted_names, ratios)   -> {name: split}                    (pure)
    apply_replace(text, find, replace, regex) -> str                         (pure)
    run(paths, opts, log, progress, should_stop) -> Report
    write_manifest(report, out_dir)      -> envelope
"""
from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
from dataclasses import dataclass, field, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS, err, ok, sha256_file

OPERATIONS = ("pair_report", "replace", "bucket", "split")
BUCKET_MODES = ("dimensions", "aspect")
SPLITS = ("train", "val", "test")

_DEFAULT_CAPTION_EXTS = (".txt", ".caption")


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------

def _norm_exts(exts) -> set[str]:
    """Normalise an ext iterable to a lowercase set with leading dots."""
    out: set[str] = set()
    for e in exts or ():
        e = str(e).strip().lower()
        if not e:
            continue
        out.add(e if e.startswith(".") else "." + e)
    return out


def _natural_key(value: str) -> list:
    """Sort key that orders `img2` before `img10` (digits compared numerically)."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]


def _pillow_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


def image_dims(path: str | Path) -> tuple[int, int] | None:
    """(width, height) via Pillow, or None if Pillow is absent / the image is
    unreadable. A `with` block releases the handle on every path (matters for
    big batches on Windows, where a held handle locks the file)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return (im.width, im.height)
    except Exception:                      # PIL raises many types on a bad image
        return None                        # value, not silence — caller records blank dims


# ---------------------------------------------------------------------------
# pure classification / assignment
# ---------------------------------------------------------------------------

def pair_files(paths, caption_exts=_DEFAULT_CAPTION_EXTS) -> dict:
    """Classify a mixed list of image + caption paths by matching stems within
    the same folder. Pure — no disk access.

    Returns {"pairs": [(image, caption)], "missing": [image], "orphans": [caption]}
    where `missing` are images with no caption and `orphans` are captions with no
    image. Paths are returned as `Path` objects, preserving input order.
    """
    cap_exts = _norm_exts(caption_exts)
    images: list[Path] = []
    captions: list[Path] = []
    for p in paths:
        p = Path(p)
        suf = p.suffix.lower()
        if suf in IMAGE_EXTS:
            images.append(p)
        elif suf in cap_exts:
            captions.append(p)

    # first caption wins for a given (folder, stem) — deterministic
    cap_index: dict[tuple[str, str], Path] = {}
    for cap in captions:
        cap_index.setdefault((str(cap.parent), cap.stem), cap)
    image_keys = {(str(img.parent), img.stem) for img in images}

    pairs: list[tuple[Path, Path]] = []
    missing: list[Path] = []
    for img in images:
        cap = cap_index.get((str(img.parent), img.stem))
        if cap is not None:
            pairs.append((img, cap))
        else:
            missing.append(img)
    orphans = [cap for cap in captions
               if (str(cap.parent), cap.stem) not in image_keys]
    return {"pairs": pairs, "missing": missing, "orphans": orphans}


def bucket_for(w: int | None, h: int | None, mode: str = "dimensions") -> str:
    """Bucket name for an image of size (w, h). Pure.

    mode == "dimensions" -> "WxH"
    mode == "aspect"     -> "portrait" | "landscape" | "square" (5% tolerance band)
    Unknown dims (Pillow absent / bad image) bucket as "unknown".
    """
    if not w or not h:
        return "unknown"
    if mode == "aspect":
        r = w / h
        if r >= 1.05:
            return "landscape"
        if r <= 0.95:
            return "portrait"
        return "square"
    return f"{int(w)}x{int(h)}"


def split_assign(sorted_names, ratios=(0.8, 0.1, 0.1)) -> dict:
    """Assign each name to train/val/test by a deterministic index threshold.

    Names are consumed in the given order (the caller sorts first, so the result
    is reproducible with no randomness). Ratios are normalised, then cumulative
    rounded boundaries carve the index range — e.g. 80/10/10 over 10 names gives
    exactly 8 train / 1 val / 1 test, every run.
    """
    names = list(sorted_names)
    n = len(names)
    if n == 0:
        return {}
    total = float(sum(ratios)) or 1.0
    r_train = ratios[0] / total
    r_val = ratios[1] / total
    train_end = round(n * r_train)
    val_end = round(n * (r_train + r_val))
    train_end = min(train_end, n)
    val_end = min(max(val_end, train_end), n)
    out: dict[str, str] = {}
    for i, name in enumerate(names):
        if i < train_end:
            out[name] = "train"
        elif i < val_end:
            out[name] = "val"
        else:
            out[name] = "test"
    return out


def apply_replace(text: str, find: str, replace: str, regex: bool = False) -> str:
    """Literal or regex find/replace over caption text. Pure."""
    if not find:
        return text
    if regex:
        return re.sub(find, replace, text)
    return text.replace(find, replace)


def _count_replacements(text: str, find: str, regex: bool) -> int:
    if not find:
        return 0
    if regex:
        return len(re.findall(find, text))
    return text.count(find)


# ---------------------------------------------------------------------------
# options / result records
# ---------------------------------------------------------------------------

@dataclass
class DatasetOptions:
    operation: str = "pair_report"          # pair_report | replace | bucket | split
    caption_exts: tuple = _DEFAULT_CAPTION_EXTS
    find: str = ""
    replace: str = ""
    regex: bool = False
    bucket_mode: str = "dimensions"         # dimensions | aspect
    ratios: tuple = (0.8, 0.1, 0.1)         # train / val / test
    out_root: Path | None = None
    dry_run: bool = True


@dataclass
class ManifestRow:
    filename: str
    has_caption: bool
    width: int | None
    height: int | None
    split: str
    bucket: str
    sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    operation: str
    rows: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    pairs: int = 0
    missing: int = 0
    orphans: int = 0
    copied: int = 0
    changed: int = 0
    failed: int = 0
    degraded: bool = False
    manifest: dict | None = None
    pair_report: dict | None = None


# ---------------------------------------------------------------------------
# atomic write helpers (temp .part then os.replace — same-name temps self-heal)
# ---------------------------------------------------------------------------

def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    shutil.copy2(src, tmp)
    tmp.replace(dst)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_csv(path: Path, header: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# manifest + pair report writers
# ---------------------------------------------------------------------------

def write_manifest(report: Report, out_dir: str | Path) -> dict:
    """Write dataset_manifest.{csv,json} for a report. Returns an ok/err envelope
    whose data is {"csv": path, "json": path}. Fallible IO is enveloped, not raised."""
    out_dir = Path(out_dir)
    csv_path = out_dir / "dataset_manifest.csv"
    json_path = out_dir / "dataset_manifest.json"
    header = ["filename", "has_caption", "width", "height", "split", "bucket", "sha256"]
    try:
        _write_csv(csv_path, header, [
            [r.filename, r.has_caption, r.width, r.height, r.split, r.bucket, r.sha256]
            for r in report.rows])
        payload = {
            "operation": report.operation,
            "counts": {
                "images": len(report.rows), "pairs": report.pairs,
                "missing_captions": report.missing, "orphan_captions": report.orphans,
                "copied": report.copied, "changed": report.changed, "failed": report.failed,
            },
            "rows": [r.to_dict() for r in report.rows],
        }
        _atomic_write_text(json_path, json.dumps(payload, indent=2, ensure_ascii=False))
    except OSError as ex:
        return err("io.manifest_write", f"manifest write failed: {ex}")
    return ok({"csv": str(csv_path), "json": str(json_path)})


def _write_pair_report(out_dir: Path, pairing: dict) -> dict:
    csv_path = out_dir / "pair_report.csv"
    json_path = out_dir / "pair_report.json"
    rows = []
    for img, cap in pairing["pairs"]:
        rows.append([Path(img).name, "paired", Path(cap).name])
    for img in pairing["missing"]:
        rows.append([Path(img).name, "missing_caption", ""])
    for cap in pairing["orphans"]:
        rows.append([Path(cap).name, "orphan_caption", ""])
    _write_csv(csv_path, ["file", "status", "counterpart"], rows)
    _atomic_write_text(json_path, json.dumps({
        "pairs": [[Path(i).name, Path(c).name] for i, c in pairing["pairs"]],
        "missing_captions": [Path(i).name for i in pairing["missing"]],
        "orphan_captions": [Path(c).name for c in pairing["orphans"]],
    }, indent=2, ensure_ascii=False))
    return {"csv": str(csv_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# caption discovery (fallible IO — scans source dirs only, reads nothing else)
# ---------------------------------------------------------------------------

def _discover_captions(images: list[Path], cap_exts: set[str], report: Report) -> list[Path]:
    """Find sidecar caption files sitting beside the images (deterministic order)."""
    dirs = {img.parent for img in images}
    captions: list[Path] = []
    seen: set[Path] = set()
    for d in sorted(dirs, key=lambda p: str(p).lower()):
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError as ex:
            report.messages.append(f"could not scan {d}: {ex}")
            continue
        for entry in entries:
            if entry in seen:
                continue
            if entry.is_file() and entry.suffix.lower() in cap_exts:
                captions.append(entry)
                seen.add(entry)
    return captions


# ---------------------------------------------------------------------------
# per-operation copy work (writable=False => preview only, no disk writes)
# ---------------------------------------------------------------------------

def _copy_pair(img: Path, cap: Path | None, dest_dir: Path,
               report: Report, log, writable: bool) -> None:
    img_dst = dest_dir / img.name
    if not writable:
        log(f"  would copy -> {img_dst}")
        return
    try:
        _atomic_copy(img, img_dst)
        report.copied += 1
    except OSError as ex:
        report.failed += 1
        report.messages.append(f"copy failed {img.name}: {ex}")
        return
    if cap is not None:
        try:
            _atomic_copy(cap, dest_dir / cap.name)
        except OSError as ex:
            report.messages.append(f"caption copy failed {cap.name}: {ex}")
    log(f"  copied -> {img_dst}")


def _replace_pair(img: Path, cap: Path | None, out_root: Path, opts: DatasetOptions,
                  report: Report, log, writable: bool) -> None:
    n = 0
    new_text: str | None = None
    if cap is not None:
        try:
            text = cap.read_text(encoding="utf-8", errors="replace")
        except OSError as ex:
            report.messages.append(f"read failed {cap.name}: {ex}")
            text = None
        if text is not None:
            n = _count_replacements(text, opts.find, opts.regex)
            new_text = apply_replace(text, opts.find, opts.replace, opts.regex)
            report.changed += n
    if not writable:
        log(f"  {img.name}: would apply {n} replacement(s)")
        return
    try:
        _atomic_copy(img, out_root / img.name)        # image copied unchanged
        report.copied += 1
    except OSError as ex:
        report.failed += 1
        report.messages.append(f"copy failed {img.name}: {ex}")
    if new_text is not None and cap is not None:
        try:
            _atomic_write_text(out_root / cap.name, new_text)
        except OSError as ex:
            report.messages.append(f"caption write failed {cap.name}: {ex}")
    log(f"  {img.name}: {n} replacement(s)")


# ---------------------------------------------------------------------------
# run — orchestrates one operation over a set of images
# ---------------------------------------------------------------------------

def run(paths, opts: DatasetOptions, log=None, progress=None, should_stop=None) -> Report:
    """Execute `opts.operation` over `paths` (image files). Sources are only read;
    all output lands under `opts.out_root`. Returns a Report with counts, per-image
    manifest rows, and any messages. `log(str)`, `progress(done,total)` and
    `should_stop()->bool` are optional UI hooks (called from the caller's thread)."""
    log = log or (lambda *a, **k: None)
    progress = progress or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)

    report = Report(operation=opts.operation)
    if opts.operation not in OPERATIONS:
        report.messages.append(f"unknown operation: {opts.operation}")
        return report

    images = [Path(p) for p in paths if Path(p).suffix.lower() in IMAGE_EXTS]
    if not images:
        report.messages.append("No images to process.")
        return report

    # validate a regex up front so a bad pattern fails loudly, not per-file
    if opts.operation == "replace" and opts.regex and opts.find:
        try:
            re.compile(opts.find)
        except re.error as ex:
            report.messages.append(f"invalid regex: {ex}")
            return report

    cap_exts = _norm_exts(opts.caption_exts) or _norm_exts(_DEFAULT_CAPTION_EXTS)
    captions = _discover_captions(images, cap_exts, report)
    pairing = pair_files([*images, *captions], cap_exts)
    caption_of = {img: cap for img, cap in pairing["pairs"]}
    report.pairs = len(pairing["pairs"])
    report.missing = len(pairing["missing"])
    report.orphans = len(pairing["orphans"])
    log(f"paired {report.pairs} · missing captions {report.missing} · orphan captions {report.orphans}")

    pil_ok = _pillow_available()
    if not pil_ok:
        report.degraded = True
        report.messages.append("Pillow not installed — width/height unavailable in manifest.")

    ordered = sorted(images, key=lambda p: _natural_key(p.name.lower()))
    split_map = split_assign([p.name for p in ordered], opts.ratios) if opts.operation == "split" else {}

    out_root = Path(opts.out_root) if opts.out_root else None
    writable = (not opts.dry_run) and out_root is not None
    if not opts.dry_run and out_root is None and opts.operation != "pair_report":
        report.messages.append("No output folder set — nothing copied (preview only).")

    total = len(ordered)
    for i, img in enumerate(ordered, 1):
        if should_stop():
            report.messages.append("— stopped —")
            break
        cap = caption_of.get(img)
        dims = image_dims(img) if pil_ok else None
        w, h = dims if dims else (None, None)
        try:
            digest = sha256_file(img)
        except OSError as ex:
            digest = ""
            report.failed += 1
            report.messages.append(f"sha256 failed {img.name}: {ex}")

        bucket = ""
        split = ""
        log(f"[{i}/{total}] {img.name}")
        if opts.operation == "bucket":
            bucket = bucket_for(w, h, opts.bucket_mode)
            if out_root is not None:
                _copy_pair(img, cap, out_root / bucket, report, log, writable)
        elif opts.operation == "split":
            split = split_map.get(img.name, "train")
            if out_root is not None:
                _copy_pair(img, cap, out_root / split, report, log, writable)
        elif opts.operation == "replace":
            if out_root is not None or opts.dry_run:
                _replace_pair(img, cap, out_root or img.parent, opts, report, log, writable)

        report.rows.append(ManifestRow(
            filename=img.name, has_caption=cap is not None,
            width=w, height=h, split=split, bucket=bucket, sha256=digest))
        progress(i, total)

    # finalize: manifest for every op; pair report for pair_report — only when writing
    if not opts.dry_run and out_root is not None:
        res = write_manifest(report, out_root)
        if res["error"]:
            report.messages.append(res["details"])
        else:
            report.manifest = res["data"]
        if opts.operation == "pair_report":
            try:
                report.pair_report = _write_pair_report(out_root, pairing)
            except OSError as ex:
                report.messages.append(f"pair report write failed: {ex}")

    return report

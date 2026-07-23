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
import hashlib
import importlib.util
import json
import math
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    IMAGE_EXTS,
    err,
    find_output_collisions as _find_collisions,
    ok,
    sha256_file,
)

OPERATIONS = ("pair_report", "replace", "bucket", "split")
BUCKET_MODES = ("dimensions", "aspect")
SPLITS = ("train", "val", "test")

_DEFAULT_CAPTION_EXTS = (".txt", ".caption")
MAX_DATASET_FILES = 100_000
MAX_REGEX_CHARS = 4_096
MAX_REPLACEMENT_BYTES = 1024 * 1024
MAX_CAPTION_BYTES = 16 * 1024 * 1024
MAX_RESULT_MESSAGES = 100
_PROVENANCE_SCHEMA = 1
Cancelled = Callable[[], bool] | None


def _cancelled(cancelled: Cancelled, stage: str, path: str | Path = "") -> None:
    if cancelled is not None and cancelled():
        command = [stage]
        if path:
            command.append(str(path))
        raise CommandCancelled(command)


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


def image_dims(path: str | Path, cancelled: Cancelled = None) -> tuple[int, int] | None:
    """(width, height) via Pillow, or None if Pillow is absent / the image is
    unreadable. A `with` block releases the handle on every path (matters for
    big batches on Windows, where a held handle locks the file)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        _cancelled(cancelled, "image-dimensions", path)
        with Image.open(path) as im:
            dimensions = (im.width, im.height)
        _cancelled(cancelled, "image-dimensions", path)
        return dimensions
    except CommandCancelled:
        raise
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


def normalized_options(opts: DatasetOptions) -> tuple[DatasetOptions | None, str]:
    """Validate settings and resource ceilings before discovery or decoding."""
    operation = str(opts.operation).strip().lower()
    if operation not in OPERATIONS:
        return None, f"unknown operation: {operation or '?'}"
    bucket_mode = str(opts.bucket_mode).strip().lower()
    if bucket_mode not in BUCKET_MODES:
        return None, f"unknown bucket mode: {bucket_mode or '?'}"
    try:
        raw_extensions = tuple(opts.caption_exts)
    except TypeError:
        return None, "caption extensions must be a list of suffixes"
    if not 1 <= len(raw_extensions) <= 16:
        return None, "provide between 1 and 16 caption extensions"
    extensions = sorted(_norm_exts(raw_extensions))
    if len(extensions) != len(raw_extensions) or any(
        re.fullmatch(r"\.[a-z0-9][a-z0-9_-]{0,15}", extension) is None
        for extension in extensions
    ):
        return None, "caption extensions must be unique safe suffixes such as .txt"
    try:
        ratios = tuple(float(value) for value in opts.ratios)
    except (TypeError, ValueError):
        return None, "split ratios must contain exactly three numbers"
    if (
        len(ratios) != 3 or any(not math.isfinite(value) or value < 0 for value in ratios)
        or sum(ratios) <= 0
    ):
        return None, "split ratios must be three finite non-negative values with a positive sum"
    if not isinstance(opts.find, str) or not isinstance(opts.replace, str):
        return None, "find and replacement values must be text"
    if operation == "replace" and not opts.find:
        return None, "replace requires non-empty text or a regex to find"
    if len(opts.find) > MAX_REGEX_CHARS:
        return None, f"find/regex text is limited to {MAX_REGEX_CHARS:,} characters"
    try:
        replacement_bytes = len(opts.replace.encode("utf-8"))
    except UnicodeEncodeError:
        return None, "replacement is not valid Unicode text"
    if replacement_bytes > MAX_REPLACEMENT_BYTES:
        return None, "replacement text is limited to 1 MiB"
    if bool(opts.regex) and operation == "replace":
        try:
            re.compile(opts.find)
        except re.error as ex:
            return None, f"invalid regex: {ex}"
    try:
        out_root = Path(opts.out_root) if opts.out_root is not None else None
    except (TypeError, ValueError, OSError) as ex:
        return None, f"invalid output folder: {ex}"
    dry_run = bool(opts.dry_run)
    if not dry_run and out_root is None:
        return None, "a real run requires an output folder"
    return replace(
        opts,
        operation=operation,
        caption_exts=tuple(extensions),
        bucket_mode=bucket_mode,
        ratios=ratios,
        out_root=out_root,
        regex=bool(opts.regex),
        dry_run=dry_run,
    ), ""


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
    output_artifacts: list[dict] = field(default_factory=list)


@dataclass
class DatasetResult:
    action: str                       # processed | preview | failed
    reason: str
    operation: str
    images: int = 0
    pairs: int = 0
    missing: int = 0
    orphans: int = 0
    copied: int = 0
    changed: int = 0
    failed: int = 0
    degraded: bool = False
    messages: list[str] = field(default_factory=list)
    out_path: str | None = None       # dataset_provenance.json
    artifacts: list[dict] = field(default_factory=list)
    detail: str = ""
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanItem:
    image: Path
    caption: Path | None
    width: int | None
    height: int | None
    split: str = ""
    bucket: str = ""
    image_output: Path | None = None
    caption_output: Path | None = None


@dataclass
class DatasetPlan:
    items: list[PlanItem]
    pairing: dict
    captions: list[Path]
    collisions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    degraded: bool = False
    messages: list[str] = field(default_factory=list)


def identity_dependencies(paths, opts: DatasetOptions) -> list[Path]:
    """Selected images plus every caption in their source directories."""
    images = sorted(
        {Path(path) for path in paths if Path(path).suffix.lower() in IMAGE_EXTS},
        key=lambda path: str(path).casefold(),
    )
    cap_exts = _norm_exts(opts.caption_exts) or _norm_exts(_DEFAULT_CAPTION_EXTS)
    dependencies = list(images)
    seen = set(images)
    for directory in sorted({image.parent for image in images}, key=lambda path: str(path).casefold()):
        for entry in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
            if entry not in seen and entry.is_file() and entry.suffix.lower() in cap_exts:
                seen.add(entry)
                dependencies.append(entry)
    return dependencies


def build_plan(paths, opts: DatasetOptions,
               cancelled: Cancelled = None) -> DatasetPlan:
    """Resolve every source/output before execution; read each dimension once."""
    images = sorted(
        {Path(path) for path in paths if Path(path).suffix.lower() in IMAGE_EXTS},
        key=lambda path: (_natural_key(path.name.lower()), str(path.parent).lower()),
    )
    scratch = Report(operation=opts.operation)
    cap_exts = _norm_exts(opts.caption_exts) or _norm_exts(_DEFAULT_CAPTION_EXTS)
    captions = _discover_captions(images, cap_exts, scratch, cancelled)
    pairing = pair_files([*images, *captions], cap_exts)
    caption_of = {image: caption for image, caption in pairing["pairs"]}
    pil_ok = _pillow_available()
    dimensions: dict[Path, tuple[int, int] | None] = {}
    if pil_ok:
        for image in images:
            _cancelled(cancelled, "dataset-planning", image)
            dimensions[image] = image_dims(image, cancelled)
            if dimensions[image] is None:
                scratch.messages.append(f"dimensions unavailable for {image.name}")
    else:
        scratch.messages.append("Pillow not installed — width/height unavailable in manifest.")
    split_map = (
        split_assign([str(path) for path in images], opts.ratios)
        if opts.operation == "split" else {}
    )
    out_root = Path(opts.out_root) if opts.out_root is not None else None
    items: list[PlanItem] = []
    outputs_by_source: dict[str, list[Path]] = {}

    def own(source: Path, output: Path) -> None:
        outputs_by_source.setdefault(str(source.resolve(strict=False)), []).append(output)

    for image in images:
        caption = caption_of.get(image)
        dims = dimensions.get(image)
        width, height = dims if dims else (None, None)
        split = split_map.get(str(image), "train") if opts.operation == "split" else ""
        bucket = bucket_for(width, height, opts.bucket_mode) if opts.operation == "bucket" else ""
        image_output = caption_output = None
        if out_root is not None and opts.operation in {"replace", "bucket", "split"}:
            destination = out_root
            if opts.operation == "split":
                destination /= split
            elif opts.operation == "bucket":
                destination /= bucket
            image_output = destination / image.name
            own(image, image_output)
            if caption is not None:
                caption_output = destination / caption.name
                own(caption, caption_output)
        items.append(PlanItem(
            image, caption, width, height, split, bucket,
            image_output, caption_output,
        ))

    if out_root is not None and images:
        fixed = [
            out_root / "dataset_manifest.csv", out_root / "dataset_manifest.json",
            out_root / "dataset_provenance.json",
        ]
        if opts.operation == "pair_report":
            fixed.extend([out_root / "pair_report.csv", out_root / "pair_report.json"])
        outputs_by_source.setdefault(str(images[0].resolve(strict=False)), []).extend(fixed)

    sources = [*images, *captions]
    collisions = _find_collisions(
        sources,
        lambda source: outputs_by_source.get(str(source.resolve(strict=False)), ()),
    )
    return DatasetPlan(
        items=items,
        pairing=pairing,
        captions=captions,
        collisions=collisions,
        degraded=(not pil_ok) or any(value is None for value in dimensions.values()),
        messages=scratch.messages,
    )


# ---------------------------------------------------------------------------
# atomic write helpers (temp .part then os.replace — same-name temps self-heal)
# ---------------------------------------------------------------------------

def _atomic_copy(src: Path, dst: Path, cancelled: Cancelled = None) -> dict:
    if _same_path(src, dst):
        raise OSError(f"refusing to overwrite source file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    try:
        digest = hashlib.sha256()
        size = 0
        with src.open("rb") as source, tmp.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                _cancelled(cancelled, "dataset-copy", src)
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        shutil.copystat(src, tmp)
        _cancelled(cancelled, "dataset-copy-commit", dst)
        tmp.replace(dst)
        if dst.stat().st_size != size:
            raise OSError(f"copy size validation failed: {dst}")
        return {"path": str(dst), "bytes": size, "sha256": digest.hexdigest()}
    except BaseException as ex:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            raise OSError(f"could not clean staged copy {tmp}: {cleanup}") from ex
        raise


def _atomic_write_text(path: Path, text: str, cancelled: Cancelled = None) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    encoded = text.encode("utf-8")
    try:
        with tmp.open("wb") as output:
            for offset in range(0, len(encoded), 1024 * 1024):
                _cancelled(cancelled, "dataset-text-write", path)
                output.write(encoded[offset:offset + 1024 * 1024])
        _cancelled(cancelled, "dataset-text-commit", path)
        tmp.replace(path)
        if path.stat().st_size != len(encoded):
            raise OSError(f"text size validation failed: {path}")
        return {
            "path": str(path), "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    except BaseException as ex:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            raise OSError(f"could not clean staged text {tmp}: {cleanup}") from ex
        raise


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False


def _write_csv(path: Path, header: list, rows: list,
               cancelled: Cancelled = None) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            for row in rows:
                _cancelled(cancelled, "dataset-csv", path)
                w.writerow(row)
        _cancelled(cancelled, "dataset-csv-commit", path)
        tmp.replace(path)
        return _artifact(path, cancelled)
    except BaseException as ex:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            raise OSError(f"could not clean staged CSV {tmp}: {cleanup}") from ex
        raise


# ---------------------------------------------------------------------------
# manifest + pair report writers
# ---------------------------------------------------------------------------

def write_manifest(report: Report, out_dir: str | Path,
                   cancelled: Cancelled = None) -> dict:
    """Write dataset_manifest.{csv,json} for a report. Returns an ok/err envelope
    whose data is {"csv": path, "json": path}. Fallible IO is enveloped, not raised."""
    out_dir = Path(out_dir)
    csv_path = out_dir / "dataset_manifest.csv"
    json_path = out_dir / "dataset_manifest.json"
    header = ["filename", "has_caption", "width", "height", "split", "bucket", "sha256"]
    try:
        csv_artifact = _write_csv(csv_path, header, [
            [r.filename, r.has_caption, r.width, r.height, r.split, r.bucket, r.sha256]
            for r in report.rows], cancelled)
        payload = {
            "operation": report.operation,
            "counts": {
                "images": len(report.rows), "pairs": report.pairs,
                "missing_captions": report.missing, "orphan_captions": report.orphans,
                "copied": report.copied, "changed": report.changed, "failed": report.failed,
            },
            "rows": [r.to_dict() for r in report.rows],
        }
        json_artifact = _atomic_write_text(
            json_path, json.dumps(payload, indent=2, ensure_ascii=False), cancelled
        )
    except CommandCancelled:
        raise
    except OSError as ex:
        return err("io.manifest_write", f"manifest write failed: {ex}")
    report.output_artifacts.extend([csv_artifact, json_artifact])
    return ok({"csv": str(csv_path), "json": str(json_path)})


def _write_pair_report(out_dir: Path, pairing: dict,
                       cancelled: Cancelled = None) -> dict:
    csv_path = out_dir / "pair_report.csv"
    json_path = out_dir / "pair_report.json"
    rows = []
    for img, cap in pairing["pairs"]:
        rows.append([Path(img).name, "paired", Path(cap).name])
    for img in pairing["missing"]:
        rows.append([Path(img).name, "missing_caption", ""])
    for cap in pairing["orphans"]:
        rows.append([Path(cap).name, "orphan_caption", ""])
    csv_artifact = _write_csv(
        csv_path, ["file", "status", "counterpart"], rows, cancelled
    )
    json_artifact = _atomic_write_text(json_path, json.dumps({
        "pairs": [[Path(i).name, Path(c).name] for i, c in pairing["pairs"]],
        "missing_captions": [Path(i).name for i in pairing["missing"]],
        "orphan_captions": [Path(c).name for c in pairing["orphans"]],
    }, indent=2, ensure_ascii=False), cancelled)
    return {
        "csv": str(csv_path), "json": str(json_path),
        "artifacts": [csv_artifact, json_artifact],
    }


# ---------------------------------------------------------------------------
# caption discovery (fallible IO — scans source dirs only, reads nothing else)
# ---------------------------------------------------------------------------

def _discover_captions(images: list[Path], cap_exts: set[str], report: Report,
                       cancelled: Cancelled = None) -> list[Path]:
    """Find sidecar caption files sitting beside the images (deterministic order)."""
    dirs = {img.parent for img in images}
    captions: list[Path] = []
    seen: set[Path] = set()
    for d in sorted(dirs, key=lambda p: str(p).lower()):
        _cancelled(cancelled, "caption-discovery", d)
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError as ex:
            report.messages.append(f"could not scan {d}: {ex}")
            continue
        for entry in entries:
            _cancelled(cancelled, "caption-discovery", entry)
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
               report: Report, log, writable: bool,
               cancelled: Cancelled = None) -> str:
    img_dst = dest_dir / img.name
    if not writable:
        log(f"  would copy -> {img_dst}")
        return ""
    try:
        image_artifact = _atomic_copy(img, img_dst, cancelled)
        report.output_artifacts.append(image_artifact)
        report.copied += 1
    except OSError as ex:
        report.failed += 1
        report.messages.append(f"copy failed {img.name}: {ex}")
        return ""
    if cap is not None:
        try:
            report.output_artifacts.append(
                _atomic_copy(cap, dest_dir / cap.name, cancelled)
            )
        except OSError as ex:
            report.failed += 1
            report.messages.append(f"caption copy failed {cap.name}: {ex}")
    log(f"  copied -> {img_dst}")
    return image_artifact["sha256"]


def _replace_pair(img: Path, cap: Path | None, out_root: Path, opts: DatasetOptions,
                  report: Report, log, writable: bool,
                  cancelled: Cancelled = None) -> str:
    n = 0
    new_text: str | None = None
    if cap is not None:
        try:
            size = cap.stat().st_size
            if size > MAX_CAPTION_BYTES:
                raise OSError(
                    f"caption is {size} bytes; limit is {MAX_CAPTION_BYTES}"
                )
            _cancelled(cancelled, "caption-read", cap)
            text = cap.read_bytes().decode("utf-8", errors="strict")
            _cancelled(cancelled, "caption-read", cap)
        except CommandCancelled:
            raise
        except (OSError, UnicodeError) as ex:
            report.failed += 1
            report.messages.append(f"read failed {cap.name}: {ex}")
            text = None
        if text is not None:
            n = _count_replacements(text, opts.find, opts.regex)
            new_text = apply_replace(text, opts.find, opts.replace, opts.regex)
            report.changed += n
    if not writable:
        log(f"  {img.name}: would apply {n} replacement(s)")
        return ""
    image_digest = ""
    try:
        image_artifact = _atomic_copy(
            img, out_root / img.name, cancelled
        )  # image copied unchanged
        report.output_artifacts.append(image_artifact)
        image_digest = image_artifact["sha256"]
        report.copied += 1
    except OSError as ex:
        report.failed += 1
        report.messages.append(f"copy failed {img.name}: {ex}")
    if new_text is not None and cap is not None:
        try:
            report.output_artifacts.append(
                _atomic_write_text(out_root / cap.name, new_text, cancelled)
            )
        except OSError as ex:
            report.failed += 1
            report.messages.append(f"caption write failed {cap.name}: {ex}")
    log(f"  {img.name}: {n} replacement(s)")
    return image_digest


# ---------------------------------------------------------------------------
# run — orchestrates one operation over a set of images
# ---------------------------------------------------------------------------

def run(paths, opts: DatasetOptions, log=None, progress=None, should_stop=None,
        cancelled: Cancelled = None, plan: DatasetPlan | None = None) -> Report:
    """Execute `opts.operation` over `paths` (image files). Sources are only read;
    all output lands under `opts.out_root`. Returns a Report with counts, per-image
    manifest rows, and any messages. `log(str)`, `progress(done,total)` and
    `should_stop()->bool` are optional UI hooks (called from the caller's thread)."""
    log = log or (lambda *a, **k: None)
    progress = progress or (lambda *a, **k: None)
    should_stop = should_stop or (lambda: False)

    report = Report(operation=str(opts.operation))
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        report.failed = 1
        report.messages.append(options_error)
        return report
    opts = normalized
    report.operation = opts.operation

    images = [Path(p) for p in paths if Path(p).suffix.lower() in IMAGE_EXTS]
    if not images:
        report.messages.append("No images to process.")
        return report
    if len(images) > MAX_DATASET_FILES:
        report.failed = 1
        report.messages.append(
            f"dataset has {len(images)} images; maximum is {MAX_DATASET_FILES}"
        )
        return report

    out_root = Path(opts.out_root) if opts.out_root else None
    writable = (not opts.dry_run) and out_root is not None
    if writable and any(_same_path(out_root, image.parent) for image in images):
        report.failed = 1
        report.messages.append(
            "Output folder must not be a source folder; refusing to modify source files."
        )
        return report

    _cancelled(cancelled, "dataset-planning")
    plan = plan or build_plan(images, opts, cancelled)
    report.messages.extend(plan.messages)
    report.degraded = plan.degraded
    if plan.collisions:
        report.failed = len(plan.collisions)
        report.messages.append(
            f"unsafe output plan: {len(plan.collisions)} destination collision(s)"
        )
        for output, owners in list(plan.collisions.items())[:10]:
            report.messages.append(f"collision: {output} ← {', '.join(owners)}")
        return report
    pairing = plan.pairing
    report.pairs = len(pairing["pairs"])
    report.missing = len(pairing["missing"])
    report.orphans = len(pairing["orphans"])
    log(f"paired {report.pairs} · missing captions {report.missing} · orphan captions {report.orphans}")

    if not opts.dry_run and out_root is None and opts.operation != "pair_report":
        report.messages.append("No output folder set — nothing copied (preview only).")

    total = len(plan.items)
    for i, item in enumerate(plan.items, 1):
        _cancelled(cancelled, "dataset-item", item.image)
        if should_stop():
            report.messages.append("— stopped —")
            break
        img, cap = item.image, item.caption
        w, h = item.width, item.height
        digest = ""
        bucket = item.bucket
        split = item.split
        log(f"[{i}/{total}] {img.name}")
        if opts.operation == "bucket":
            if item.image_output is not None:
                digest = _copy_pair(
                    img, cap, item.image_output.parent, report, log, writable, cancelled
                )
        elif opts.operation == "split":
            if item.image_output is not None:
                digest = _copy_pair(
                    img, cap, item.image_output.parent, report, log, writable, cancelled
                )
        elif opts.operation == "replace":
            if out_root is not None or opts.dry_run:
                digest = _replace_pair(
                    img, cap, out_root or img.parent, opts, report, log, writable,
                    cancelled,
                )
        if not digest:
            try:
                digest = sha256_file(img, cancelled=cancelled)
            except CommandCancelled:
                raise
            except OSError as ex:
                report.failed += 1
                report.messages.append(f"sha256 failed {img.name}: {ex}")

        report.rows.append(ManifestRow(
            filename=img.name, has_caption=cap is not None,
            width=w, height=h, split=split, bucket=bucket, sha256=digest))
        progress(i, total)

    # finalize: manifest for every op; pair report for pair_report — only when writing
    if not opts.dry_run and out_root is not None:
        res = write_manifest(report, out_root, cancelled)
        if res["error"]:
            report.failed += 1
            report.messages.append(res["details"])
        else:
            report.manifest = res["data"]
        if opts.operation == "pair_report":
            try:
                report.pair_report = _write_pair_report(
                    out_root, plan.pairing, cancelled
                )
                report.output_artifacts.extend(
                    report.pair_report.pop("artifacts")
                )
            except CommandCancelled:
                raise
            except OSError as ex:
                report.failed += 1
                report.messages.append(f"pair report write failed: {ex}")

    return report


def _bounded_messages(messages: list[str]) -> list[str]:
    if len(messages) <= MAX_RESULT_MESSAGES:
        return list(messages)
    return [*messages[:MAX_RESULT_MESSAGES], "additional dataset messages omitted"]


def _result_from_report(report: Report, action: str, reason: str, *,
                        out_path: str | None = None,
                        artifacts: list[dict] | None = None,
                        detail: str = "", retryable: bool = False) -> DatasetResult:
    return DatasetResult(
        action=action,
        reason=reason,
        operation=report.operation,
        images=len(report.rows),
        pairs=report.pairs,
        missing=report.missing,
        orphans=report.orphans,
        copied=report.copied,
        changed=report.changed,
        failed=report.failed,
        degraded=report.degraded,
        messages=_bounded_messages(report.messages),
        out_path=out_path,
        artifacts=list(artifacts or []),
        detail=detail,
        retryable=retryable,
    )


def _artifact(path: Path, cancelled: Cancelled = None) -> dict:
    size = path.stat().st_size
    if size < 0:
        raise OSError(f"invalid output size: {path}")
    return {
        "path": str(path),
        "bytes": size,
        "sha256": sha256_file(path, cancelled=cancelled),
    }


def _planned_output_paths(plan: DatasetPlan, opts: DatasetOptions) -> list[Path]:
    paths: set[Path] = set()
    for item in plan.items:
        if item.image_output is not None:
            paths.add(item.image_output)
        if item.caption_output is not None:
            paths.add(item.caption_output)
    if opts.out_root is not None:
        paths.update({
            opts.out_root / "dataset_manifest.csv",
            opts.out_root / "dataset_manifest.json",
        })
        if opts.operation == "pair_report":
            paths.update({
                opts.out_root / "pair_report.csv",
                opts.out_root / "pair_report.json",
            })
    return sorted(paths, key=lambda path: str(path).casefold())


def _settings_payload(opts: DatasetOptions) -> dict:
    return {
        "operation": opts.operation,
        "caption_exts": list(opts.caption_exts),
        "find": opts.find,
        "replace": opts.replace,
        "regex": opts.regex,
        "bucket_mode": opts.bucket_mode,
        "ratios": list(opts.ratios),
        "out_root": str(opts.out_root) if opts.out_root is not None else None,
        "dry_run": opts.dry_run,
    }


def process(paths, opts: DatasetOptions, log=None, progress=None,
            cancelled: Cancelled = None) -> DatasetResult:
    """Plan once, execute safely, and publish a JSON-last provenance marker."""
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        return DatasetResult("failed", options_error, str(opts.operation), detail="bad.options")
    opts = normalized
    images = [Path(path) for path in paths if Path(path).suffix.lower() in IMAGE_EXTS]
    if not images:
        return DatasetResult("failed", "No images to process.", opts.operation, detail="input.empty")
    if len(images) > MAX_DATASET_FILES:
        return DatasetResult(
            "failed", f"dataset has {len(images)} images; maximum is {MAX_DATASET_FILES}",
            opts.operation, detail="resource.limit",
        )
    if not opts.dry_run and opts.out_root is not None and any(
        _same_path(opts.out_root, image.parent) for image in images
    ):
        return DatasetResult(
            "failed", "Output folder must not be a source folder.", opts.operation,
            detail="output.source_collision",
        )
    try:
        plan = build_plan(images, opts, cancelled)
    except CommandCancelled:
        raise
    except OSError as ex:
        return DatasetResult(
            "failed", f"dataset planning failed: {ex}", opts.operation,
            detail="io.plan", retryable=True,
        )
    if plan.collisions:
        return DatasetResult(
            "failed", f"unsafe output plan: {len(plan.collisions)} destination collision(s)",
            opts.operation, failed=len(plan.collisions),
            messages=[
                f"collision: {output}"
                for output in list(plan.collisions)[:MAX_RESULT_MESSAGES]
            ],
            detail="output.collision",
        )
    marker = opts.out_root / "dataset_provenance.json" if opts.out_root else None
    if marker is not None and not opts.dry_run:
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        except OSError as ex:
            return DatasetResult(
                "failed", f"could not invalidate prior provenance: {ex}",
                opts.operation, detail="io.marker", retryable=True,
            )
    report = run(
        images, opts, log=log, progress=progress, cancelled=cancelled, plan=plan
    )
    if report.failed:
        return _result_from_report(
            report, "failed", f"dataset run completed with {report.failed} failure(s)",
            detail="item.failed", retryable=not opts.dry_run,
        )
    if opts.dry_run:
        return _result_from_report(
            report, "preview", f"previewed {len(report.rows)} image(s)", detail="preview"
        )
    assert marker is not None
    marker_part = marker.with_name(f"{marker.stem}.part{marker.suffix}")
    try:
        output_artifacts = sorted(
            report.output_artifacts,
            key=lambda artifact: str(artifact["path"]).casefold(),
        )
        expected_paths = {
            str(path.resolve(strict=False))
            for path in _planned_output_paths(plan, opts)
        }
        actual_paths = {
            str(Path(artifact["path"]).resolve(strict=False))
            for artifact in output_artifacts
        }
        if len(output_artifacts) != len(actual_paths) or actual_paths != expected_paths:
            raise OSError("completed outputs do not match the validated output plan")
        payload = {
            "schema": _PROVENANCE_SCHEMA,
            "settings": _settings_payload(opts),
            "summary": {
                "images": len(report.rows), "pairs": report.pairs,
                "missing": report.missing, "orphans": report.orphans,
                "copied": report.copied, "changed": report.changed,
                "failed": report.failed, "degraded": report.degraded,
            },
            "messages": _bounded_messages(report.messages),
            "outputs": output_artifacts,
        }
        _atomic_write_text(
            marker, json.dumps(payload, indent=2, ensure_ascii=False), cancelled
        )
        marker_record = _artifact(marker, cancelled)
    except CommandCancelled:
        try:
            marker_part.unlink()
        except FileNotFoundError:
            pass
        raise
    except (OSError, TypeError, ValueError, UnicodeError) as ex:
        try:
            marker_part.unlink()
        except FileNotFoundError:
            pass
        return _result_from_report(
            report, "failed", f"provenance validation failed: {ex}",
            detail="output.invalid", retryable=True,
        )
    return _result_from_report(
        report, "processed", f"processed {len(report.rows)} image(s)",
        out_path=str(marker), artifacts=[marker_record],
        detail="degraded" if report.degraded else "",
    )


def validate_result(result: DatasetResult, opts: DatasetOptions,
                    cancelled: Cancelled = None) -> bool:
    """Validate preview identity metadata or every output named by provenance."""
    normalized, _ = normalized_options(opts)
    if normalized is None or result.operation != normalized.operation or result.retryable:
        return False
    if result.action == "preview":
        return (
            normalized.dry_run and result.out_path is None and not result.artifacts
            and result.failed == 0 and result.detail == "preview"
        )
    if (
        result.action != "processed" or normalized.dry_run or normalized.out_root is None
        or len(result.artifacts) != 1
    ):
        return False
    marker = normalized.out_root / "dataset_provenance.json"
    try:
        _cancelled(cancelled, "dataset-marker-validation", marker)
        marker_bytes = marker.read_bytes()
        _cancelled(cancelled, "dataset-marker-validation", marker)
        marker_record = {
            "path": str(marker),
            "bytes": len(marker_bytes),
            "sha256": hashlib.sha256(marker_bytes).hexdigest(),
        }
        if result.artifacts != [marker_record] or not _same_path(result.out_path, marker):
            return False
        payload = json.loads(marker_bytes.decode("utf-8"))
    except CommandCancelled:
        raise
    except (OSError, UnicodeError, ValueError, TypeError):
        return False
    if (
        not isinstance(payload, dict) or payload.get("schema") != _PROVENANCE_SCHEMA
        or payload.get("settings") != _settings_payload(normalized)
        or payload.get("messages") != result.messages
    ):
        return False
    summary = payload.get("summary")
    expected_summary = {
        "images": result.images, "pairs": result.pairs,
        "missing": result.missing, "orphans": result.orphans,
        "copied": result.copied, "changed": result.changed,
        "failed": result.failed, "degraded": result.degraded,
    }
    if summary != expected_summary or result.failed:
        return False
    outputs = payload.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return False
    try:
        output_root = normalized.out_root.resolve(strict=False)
        output_paths = [Path(record["path"]).resolve(strict=False) for record in outputs]
        if (
            len(output_paths) != len(set(output_paths))
            or any(not path.is_relative_to(output_root) for path in output_paths)
            or marker.resolve(strict=False) in output_paths
        ):
            return False
        actual = [_artifact(Path(record["path"]), cancelled) for record in outputs]
    except (KeyError, TypeError, OSError):
        return False
    return actual == outputs and result.detail == ("degraded" if result.degraded else "")

"""Package Extractor engine — deterministic, headless batch archive extraction.

Pure logic, no UI, no global state, Python stdlib only (`zipfile`, `tarfile`,
`gzip`, `pathlib`, `hashlib`). No AI, no network, no subprocess. Never executes
archive contents.

Handled formats:
    .unitypackage  - a gzipped tar; each asset is a `<GUID>/` entry holding
                     `asset` (bytes), `asset.meta`, and `pathname` (the original
                     Unity project path). Extraction reconstructs the original
                     `Assets/...` tree by reading each GUID's `pathname` and
                     writing its `asset` bytes there.
    .zip                       - via zipfile.
    .tar / .tar.gz / .tgz      - via tarfile (transparent compression).

Security (this is untrusted, user-supplied input — see AGENTS.md):
    * zip-slip / tar-slip: every target path (including a reconstructed Unity
      `pathname`) is sanitised and asserted to stay within the output root;
      escaping entries are REJECTED and recorded (announced, never silent).
    * symlink / hardlink tar members are skipped (never followed or created).
    * decompression-bomb guard: a per-file and a per-archive byte cap; exceeding
      either stops that archive with a clear error (partial report preserved).
    * metadata-flood guard: a bounded per-archive member count.
    * cancellation rollback: remove only artifacts committed by that attempt.

Errors are values: the shared envelope (`ok`/`err`) wraps every fallible IO;
`is_within` / `safe_name` / the sizing checks are pure. See CodingPrinciples.md.

Public interface:
    is_within(root, target)            -> bool        (pure safe-path check)
    safe_name(name)                    -> str         (pure component sanitiser)
    extract_unitypackage(path, out, o) -> envelope(report)
    extract_zip(path, out, o)          -> envelope(report)
    extract_tar(path, out, o)          -> envelope(report)
    extract(path, out, o)              -> envelope(report)   (dispatch)
    process(path, o, cancelled)        -> Result      (extract one archive)
    validate_result(result, o)         -> bool        (artifact reuse gate)
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, asdict
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    err,
    find_output_collisions as _find_collisions,
    ok,
    sha256_file,
)

# Extensions the tool recognises (panel mirrors this).
ARCHIVE_EXTS = {
    ".unitypackage", ".zip", ".tar", ".gz", ".tgz",
    ".bz2", ".tbz2", ".xz", ".txz",
}

# Decompression-bomb caps (bytes). Deliberately generous defaults; configurable.
DEFAULT_MAX_BYTES = 5 * 1024 ** 3        # per-archive total (5 GiB)
DEFAULT_MAX_FILE_BYTES = 2 * 1024 ** 3   # per single member (2 GiB)
DEFAULT_MAX_ENTRIES = 100_000             # report/metadata growth guard

_MAX_NESTED = 2                          # hard bound on nested-archive depth
_MAX_ENTRY_LIMIT = 1_000_000              # hard configuration ceiling
_CHUNK = 1 << 20                         # 1 MiB streaming chunks

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------------------
# safe-path primitives — pure, no I/O beyond realpath resolution
# ---------------------------------------------------------------------------

def is_within(root: str | Path, target: str | Path) -> bool:
    """True iff `target` resolves to `root` itself or a path strictly beneath it.

    Uses realpath (resolves symlinks + `..`) and normcase (case/sep-insensitive
    on Windows) so a crafted `../escape` or a symlink can't slip out. The
    `+ os.sep` boundary stops `/rootevil` from matching `/root`."""
    root_r = os.path.normcase(os.path.realpath(str(root)))
    target_r = os.path.normcase(os.path.realpath(str(target)))
    return target_r == root_r or target_r.startswith(root_r + os.sep)


def safe_name(name: str) -> str:
    """Sanitise ONE path component into a portable, safe filename.

    Strips path separators and Windows-reserved characters, trailing dots/spaces,
    and guards the reserved device names (CON, NUL, COM1…). Never returns empty."""
    n = _BAD_CHARS.sub("_", str(name))
    n = n.strip().rstrip(". ")
    if not n:
        return "_"
    if n.upper().split(".", 1)[0] in _WINDOWS_RESERVED:
        n = "_" + n
    return n


def _safe_relpath(name: str) -> Path | None:
    """Turn an archive entry name into a safe relative Path, or None to REJECT.

    Rejects (returns None) anything that tries to escape: absolute paths, drive
    or UNC prefixes, `~`, or any `..` component. Remaining components are each
    passed through `safe_name`. The is_within check is a second, belt-and-braces
    guard on top of this."""
    s = str(name).replace("\\", "/").strip()
    if not s or s.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", s):
        return None
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return Path(*[safe_name(p) for p in parts])


def norm_exts(values) -> frozenset[str]:
    """Normalise a user extension filter to a lowercased, dot-prefixed set."""
    out: set[str] = set()
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    for tok in values or ():
        tok = str(tok).strip().lower()
        if not tok:
            continue
        out.add(tok if tok.startswith(".") else "." + tok)
    return frozenset(out)


# ---------------------------------------------------------------------------
# options + result
# ---------------------------------------------------------------------------

@dataclass
class ExtractOptions:
    out_root: Path | None = None
    filter_exts: frozenset = field(default_factory=frozenset)   # empty = extract all
    collision: str = "rename"          # skip | rename  (never overwrite blindly)
    nested_depth: int = 0              # extract archives found inside (bounded <= 2)
    max_bytes: int = DEFAULT_MAX_BYTES         # per-archive total cap
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES   # per-member cap
    max_entries: int = DEFAULT_MAX_ENTRIES     # per-archive member count cap
    dry_run: bool = True               # list contents, write nothing


@dataclass
class Result:
    src: str
    action: str                        # extracted | dry-run | failed
    reason: str
    written: int = 0
    skipped: int = 0
    rejected: int = 0                  # security: entries that tried to escape
    errors: int = 0
    listed: int = 0                    # dry-run count
    out_path: str | None = None
    report_path: str | None = None
    report_bytes: int = 0
    report_sha256: str = ""
    csv_report_path: str | None = None
    csv_report_bytes: int = 0
    csv_report_sha256: str = ""
    degraded: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# decompression-bomb budget + streaming copy (atomic, hashed)
# ---------------------------------------------------------------------------

class _BombError(Exception):
    """Raised when a per-file or per-archive byte cap is exceeded."""


class _Budget:
    def __init__(self, max_total: int, max_file: int, max_entries: int):
        self.max_total = int(max_total) if max_total else 0
        self.max_file = int(max_file) if max_file else 0
        self.max_entries = int(max_entries)
        self.total = 0
        self.declared_total = 0
        self.entries = 0

    def add_entry(self) -> None:
        self.entries += 1
        if self.entries > self.max_entries:
            raise _BombError(
                f"archive exceeds member-count cap {self.max_entries}"
            )

    def check_declared(self, size) -> None:
        size = int(size or 0)
        if size < 0:
            raise _BombError(f"member declares invalid negative size {size} B")
        if self.max_file and size > self.max_file:
            raise _BombError(f"member declares {size} B > per-file cap {self.max_file} B")
        self.declared_total += size
        if self.max_total and self.declared_total > self.max_total:
            raise _BombError(f"archive would exceed total cap {self.max_total} B")

    def add(self, n: int) -> None:
        self.total += n
        if self.max_total and self.total > self.max_total:
            raise _BombError(f"archive exceeded total cap {self.max_total} B")


def _remove_staged(path: Path) -> str | None:
    try:
        path.unlink()
        return None
    except FileNotFoundError:
        return None
    except OSError as ex:
        return f"could not remove staged file {path}: {ex}"


def _stream_to(
    fileobj,
    target: Path,
    budget: _Budget,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Copy `fileobj` -> `target` atomically (`.part` then replace), counting bytes
    against the budget and hashing. Caps enforced on the ACTUAL bytes read, not
    just the declared size (a lying header can't get past the running total)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    written = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                if cancelled is not None and cancelled():
                    raise CommandCancelled(["archive-extract", str(target)])
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if budget.max_file and written > budget.max_file:
                    raise _BombError(f"member exceeded per-file cap {budget.max_file} B")
                budget.add(len(chunk))
                h.update(chunk)
                out.write(chunk)
        if cancelled is not None and cancelled():
            raise CommandCancelled(["archive-commit", str(target)])
        os.replace(tmp, target)
    except BaseException as ex:
        cleanup_error = _remove_staged(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    return written, h.hexdigest()


def _resolve_collision(target: Path, mode: str) -> Path | None:
    """Never overwrite blindly. `skip` -> None (caller records skip); `rename` ->
    a free `name_NNN.ext` beside it (or None if 10k are taken)."""
    if not target.exists():
        return target
    if mode == "skip":
        return None
    stem, suf, parent = target.stem, target.suffix, target.parent
    for i in range(1, 10000):
        cand = parent / f"{stem}_{i:03d}{suf}"
        if not cand.exists():
            return cand
    return None


# ---------------------------------------------------------------------------
# per-entry handling — shared by every format
# ---------------------------------------------------------------------------

def _new_report(path, out_dir, kind: str) -> dict:
    return {
        "archive": str(path), "kind": kind, "out_dir": str(out_dir),
        "written": [], "skipped": [], "rejected": [], "errors": [],
        "listed": [], "aborted": "",
    }


def _attach_cancel_report(ex: CommandCancelled, report: dict, out_dir: Path) -> None:
    ex.archive_report = report
    ex.archive_out_dir = str(out_dir)


def _handle_entry(
    name: str,
    size,
    open_fn,
    out_dir: Path,
    opts: ExtractOptions,
    budget: _Budget,
    report: dict,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Vet one entry (sanitise -> in-root -> filter -> collision) and, unless
    dry-running, stream it to disk. Security rejections are recorded, never
    silent. Raises _BombError to abort the whole archive."""
    rel = _safe_relpath(name)
    if rel is None:
        report["rejected"].append({"name": name, "reason": "unsafe path (traversal/absolute), rejected"})
        return
    target = out_dir / rel
    if not is_within(out_dir, target):
        report["rejected"].append({"name": name, "reason": "path escapes output root, rejected"})
        return
    if opts.filter_exts and target.suffix.lower() not in opts.filter_exts:
        report["skipped"].append({"name": name, "reason": "excluded by extension filter"})
        return
    if opts.dry_run:
        budget.check_declared(size)
        report["listed"].append({"path": rel.as_posix(), "bytes": int(size or 0)})
        return
    final = _resolve_collision(target, opts.collision)
    if final is None:
        report["skipped"].append({"name": name, "reason": "collision (already exists), skipped"})
        return
    budget.check_declared(size)
    try:
        fo = open_fn()
    except (OSError, EOFError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile, tarfile.TarError) as ex:
        report["errors"].append({"name": name, "reason": f"member open failed: {ex}"})
        return
    if fo is None:
        report["errors"].append({"name": name, "reason": "member could not be opened"})
        return
    try:
        with fo:
            written, sha = _stream_to(fo, final, budget, cancelled=cancelled)
        report["written"].append({"path": final.relative_to(out_dir).as_posix(),
                                  "bytes": written, "sha256": sha})
    except _BombError:
        raise
    except (OSError, EOFError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile, tarfile.TarError) as ex:
        report["errors"].append({"name": name, "reason": f"extract failed: {ex}"})


# ---------------------------------------------------------------------------
# per-format extractors — each returns an envelope wrapping the report
# ---------------------------------------------------------------------------

def _degraded(report: dict) -> bool:
    return bool(report["aborted"] or report["errors"] or report["rejected"])


def extract_zip(
    path,
    out_dir: Path,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "zip")
    if not zipfile.is_zipfile(path):
        return err("archive.bad", f"not a valid zip: {path.name}")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes, opts.max_entries)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            try:
                for info in zf.infolist():
                    budget.add_entry()
                    if cancelled is not None and cancelled():
                        raise CommandCancelled(["archive-extract", str(path)])
                    if info.is_dir():
                        continue
                    _handle_entry(info.filename, info.file_size,
                                  lambda info=info: zf.open(info), out_dir, opts,
                                  budget, report, cancelled)
            except _BombError as ex:
                report["aborted"] = str(ex)
    except CommandCancelled as ex:
        _attach_cancel_report(ex, report, out_dir)
        raise
    except (OSError, zipfile.BadZipFile) as ex:
        return err("archive.read", f"failed reading zip {path.name}: {ex}")
    return ok(report, degraded=_degraded(report))


def extract_tar(
    path,
    out_dir: Path,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "tar")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes, opts.max_entries)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "r:*") as tf:          # transparent gz/bz2/xz
            try:
                for m in tf:
                    budget.add_entry()
                    if cancelled is not None and cancelled():
                        raise CommandCancelled(["archive-extract", str(path)])
                    if m.issym() or m.islnk():
                        report["skipped"].append({"name": m.name,
                                                  "reason": "symlink/hardlink skipped (not followed)"})
                        continue
                    if not m.isfile():
                        continue
                    _handle_entry(m.name, m.size,
                                  lambda m=m: tf.extractfile(m), out_dir, opts,
                                  budget, report, cancelled)
            except _BombError as ex:
                report["aborted"] = str(ex)
    except CommandCancelled as ex:
        _attach_cancel_report(ex, report, out_dir)
        raise
    except (tarfile.TarError, OSError, EOFError) as ex:
        return err("archive.read", f"failed reading tar {path.name}: {ex}")
    return ok(report, degraded=_degraded(report))


def extract_unitypackage(
    path,
    out_dir: Path,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Reconstruct the original Unity folder tree from a gzipped-tar .unitypackage.

    Each `<GUID>/` entry carries `pathname` (the original project path) and
    `asset` (the file bytes). We read the pathname, sanitise + in-root-check the
    reconstructed path exactly like any other entry, then write the asset bytes."""
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "unitypackage")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes, opts.max_entries)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "r:gz") as tf:
            entries: dict[str, dict] = {}
            for m in tf:
                budget.add_entry()
                if cancelled is not None and cancelled():
                    raise CommandCancelled(["archive-extract", str(path)])
                if m.issym() or m.islnk():
                    report["skipped"].append({"name": m.name,
                                              "reason": "symlink/hardlink skipped (not followed)"})
                    continue
                if not m.isfile():
                    continue
                n = m.name[2:] if m.name.startswith("./") else m.name
                parts = n.split("/")
                if len(parts) < 2:
                    continue
                guid, leaf = parts[-2], parts[-1]
                if leaf in ("pathname", "asset"):
                    fields = entries.setdefault(guid, {})
                    if leaf in fields:
                        report["errors"].append({
                            "name": m.name,
                            "reason": f"duplicate Unity {leaf} entry ignored",
                        })
                        continue
                    fields[leaf] = m
            try:
                for guid, d in entries.items():
                    if cancelled is not None and cancelled():
                        raise CommandCancelled(["archive-extract", str(path)])
                    pm, am = d.get("pathname"), d.get("asset")
                    if pm is None or am is None:        # folder/meta-only entry
                        continue
                    f = tf.extractfile(pm)
                    if f is None:
                        report["errors"].append({"name": guid, "reason": "unreadable pathname"})
                        continue
                    with f:
                        raw = f.read(8192)
                    lines = raw.decode("utf-8", "replace").splitlines()
                    pathname = lines[0].strip() if lines else ""
                    if not pathname:
                        report["skipped"].append({"name": guid, "reason": "empty pathname"})
                        continue
                    _handle_entry(pathname, am.size,
                                  lambda am=am: tf.extractfile(am), out_dir, opts,
                                  budget, report, cancelled)
            except _BombError as ex:
                report["aborted"] = str(ex)
    except _BombError as ex:
        report["aborted"] = str(ex)
    except CommandCancelled as ex:
        _attach_cancel_report(ex, report, out_dir)
        raise
    except (tarfile.TarError, OSError, EOFError) as ex:
        return err("archive.read", f"failed reading unitypackage {path.name}: {ex}")
    return ok(report, degraded=_degraded(report))


def _kind(path: Path) -> str | None:
    n = path.name.lower()
    if n.endswith(".unitypackage"):
        return "unity"
    if n.endswith(".zip"):
        return "zip"
    if n.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz")):
        return "tar"
    return None


def extract(
    path,
    out_dir: Path,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Dispatch to the right per-format extractor by extension."""
    kind = _kind(Path(path))
    if kind == "unity":
        return extract_unitypackage(path, out_dir, opts, cancelled=cancelled)
    if kind == "zip":
        return extract_zip(path, out_dir, opts, cancelled=cancelled)
    if kind == "tar":
        return extract_tar(path, out_dir, opts, cancelled=cancelled)
    return err("archive.unsupported", f"unsupported archive type: {Path(path).name}")


# ---------------------------------------------------------------------------
# reports (JSON + CSV) + nested extraction
# ---------------------------------------------------------------------------

def _report_record(path: Path, cancelled=None) -> dict:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path, cancelled=cancelled),
    }


def _write_reports(
    dest: Path,
    report: dict,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Atomically publish CSV then JSON, with JSON as the completion marker."""
    jpath = dest / "_extract_report.json"
    cpath = dest / "_extract_report.csv"
    jtmp = dest / "_extract_report.part.json"
    ctmp = dest / "_extract_report.part.csv"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with open(jtmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        with open(ctmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["status", "name", "bytes", "sha256", "reason"])
            for e in report["written"]:
                w.writerow(["written", e["path"], e.get("bytes", ""), e.get("sha256", ""), ""])
            for e in report["skipped"]:
                w.writerow(["skipped", e["name"], "", "", e["reason"]])
            for e in report["rejected"]:
                w.writerow(["rejected", e["name"], "", "", e["reason"]])
            for e in report["errors"]:
                w.writerow(["error", e["name"], "", "", e["reason"]])
            if report["aborted"]:
                w.writerow(["aborted", "", "", "", report["aborted"]])
            f.flush()
            os.fsync(f.fileno())
        json_record = _report_record(jtmp, cancelled)
        csv_record = _report_record(ctmp, cancelled)
        if cancelled is not None and cancelled():
            raise CommandCancelled(["archive-report", str(dest)])
        os.replace(ctmp, cpath)
        os.replace(jtmp, jpath)
        json_record["path"] = str(jpath)
        csv_record["path"] = str(cpath)
        return ok({"json": json_record, "csv": csv_record})
    except CommandCancelled as ex:
        cleanup_errors: list[str] = []
        for staged in (jtmp, ctmp):
            cleanup_error = _remove_staged(staged)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            raise OSError("; ".join(cleanup_errors)) from ex
        raise
    except OSError as ex:
        cleanup_errors = [
            message for staged in (jtmp, ctmp)
            if (message := _remove_staged(staged)) is not None
        ]
        details = f"could not write extraction report: {ex}"
        if cleanup_errors:
            details += "; " + "; ".join(cleanup_errors)
        return err("report.write", details)


def _rollback_report(report: dict, dest: Path) -> list[str]:
    """Remove only artifacts recorded as created by one interrupted attempt."""
    errors: list[str] = []
    nested = report.get("nested", []) if isinstance(report, dict) else []
    if isinstance(nested, list):
        for entry in reversed(nested):
            if not isinstance(entry, dict) or not isinstance(entry.get("report"), dict):
                continue
            sub_dest = Path(entry.get("out_dir", ""))
            if is_within(dest, sub_dest):
                errors.extend(_rollback_report(entry["report"], sub_dest))
            report_files = entry.get("report_files")
            if isinstance(report_files, dict):
                for record in report_files.values():
                    if not isinstance(record, dict):
                        continue
                    path = Path(record.get("path", ""))
                    if is_within(dest, path):
                        cleanup_error = _remove_staged(path)
                        if cleanup_error:
                            errors.append(cleanup_error)
    written = report.get("written", []) if isinstance(report, dict) else []
    if isinstance(written, list):
        for entry in reversed(written):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            rel = _safe_relpath(entry["path"])
            if rel is None:
                continue
            path = dest / rel
            if is_within(dest, path):
                cleanup_error = _remove_staged(path)
                if cleanup_error:
                    errors.append(cleanup_error)
    return errors


def _extract_nested(
    report: dict,
    dest: Path,
    opts: ExtractOptions,
    depth: int,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """After extracting, unpack any extracted file that is itself a supported
    archive. Bounded by opts.nested_depth AND the hard _MAX_NESTED ceiling."""
    if depth > min(opts.nested_depth, _MAX_NESTED):
        return
    for entry in list(report.get("written", [])):
        if cancelled is not None and cancelled():
            raise CommandCancelled(["archive-nested", str(dest)])
        child = dest / Path(entry["path"])
        if _kind(child) is None or not child.is_file():
            continue
        sub = child.parent / (safe_name(child.stem) + "_unpacked")
        try:
            env = extract(child, sub, opts, cancelled=cancelled)
        except CommandCancelled as ex:
            child_report = getattr(ex, "archive_report", None)
            child_out = Path(getattr(ex, "archive_out_dir", sub))
            if isinstance(child_report, dict) and is_within(sub, child_out):
                cleanup_errors = _rollback_report(child_report, child_out)
                if cleanup_errors:
                    raise OSError("; ".join(cleanup_errors)) from ex
            raise
        if env["error"]:
            report["errors"].append({"name": entry["path"],
                                     "reason": f"nested extract failed: {env['details']}"})
            continue
        sub_report = env["data"]
        try:
            _extract_nested(sub_report, sub, opts, depth + 1, cancelled=cancelled)
            report_files = _write_reports(sub, sub_report, cancelled=cancelled)
        except CommandCancelled as ex:
            cleanup_errors = _rollback_report(sub_report, sub)
            if cleanup_errors:
                raise OSError("; ".join(cleanup_errors)) from ex
            raise
        nested = {
            "archive": entry["path"],
            "out_dir": str(sub),
            "written": len(sub_report["written"]),
            "report": sub_report,
            "report_files": None if report_files["error"] else report_files["data"],
        }
        if report_files["error"]:
            message = report_files["details"]
            nested["report_error"] = message
            report["errors"].append({
                "name": entry["path"], "reason": f"nested {message}"
            })
        report.setdefault("nested", []).append(nested)


def _dest_for(src: Path, opts: ExtractOptions) -> Path:
    """Each archive extracts into its own `<stem>/` folder under out_root (or
    beside the source when no out_root is given)."""
    root = Path(opts.out_root) if opts.out_root else src.parent
    return root / safe_name(src.stem)


def find_output_collisions(paths, opts: ExtractOptions) -> dict[str, tuple[str, ...]]:
    """Detect selected archives that would share one extraction directory."""
    return _find_collisions(
        paths,
        lambda source: (_dest_for(source, opts) / ".ks_package_extractor_owner",),
    )


def _normalized_options(opts: ExtractOptions) -> tuple[ExtractOptions | None, str]:
    integer_values = (
        opts.nested_depth, opts.max_bytes, opts.max_file_bytes, opts.max_entries
    )
    if any(
        isinstance(value, bool)
        or (isinstance(value, float) and not value.is_integer())
        for value in integer_values
    ):
        return None, "depth and resource limits must be whole numbers"
    try:
        nested_depth = int(opts.nested_depth)
        max_bytes = int(opts.max_bytes)
        max_file_bytes = int(opts.max_file_bytes)
        max_entries = int(opts.max_entries)
        out_root = Path(opts.out_root) if opts.out_root is not None else None
    except (TypeError, ValueError, OSError) as ex:
        return None, f"invalid extraction options: {ex}"
    if isinstance(opts.nested_depth, bool) or not 0 <= nested_depth <= _MAX_NESTED:
        return None, f"nested depth must be between 0 and {_MAX_NESTED}"
    if max_bytes <= 0 or max_file_bytes <= 0:
        return None, "archive and member byte limits must be positive"
    if max_file_bytes > max_bytes:
        return None, "per-member byte limit cannot exceed the archive byte limit"
    if not 1 <= max_entries <= _MAX_ENTRY_LIMIT:
        return None, f"member-count limit must be between 1 and {_MAX_ENTRY_LIMIT}"
    if opts.collision not in {"rename", "skip"}:
        return None, "collision policy must be 'rename' or 'skip'"
    return ExtractOptions(
        out_root=out_root,
        filter_exts=norm_exts(opts.filter_exts),
        collision=opts.collision,
        nested_depth=nested_depth,
        max_bytes=max_bytes,
        max_file_bytes=max_file_bytes,
        max_entries=max_entries,
        dry_run=bool(opts.dry_run),
    ), ""


def _summarize(report: dict, dry_run: bool) -> tuple[str, str]:
    w, s = len(report["written"]), len(report["skipped"])
    rej, errs = len(report["rejected"]), len(report["errors"])
    if report["aborted"]:
        return "failed", f"aborted (bomb guard): {report['aborted']} — {w} written before stop"
    if dry_run:
        return "dry-run", f"{len(report['listed'])} entries would extract"
    parts = [f"wrote {w}"]
    if s:
        parts.append(f"skipped {s}")
    if rej:
        parts.append(f"rejected {rej}")
    if errs:
        parts.append(f"errors {errs}")
    return "extracted", ", ".join(parts)


def process(
    path: str | Path,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """Extract one archive into its own subfolder; write per-archive reports."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if _kind(src) is None:
        return Result(str(src), "failed", f"unsupported archive type: {src.name}", detail="archive.unsupported")

    normalized, options_error = _normalized_options(opts)
    if normalized is None:
        return Result(str(src), "failed", options_error, detail="bad.options")
    opts = normalized
    if cancelled is not None and cancelled():
        raise CommandCancelled(["archive-extract", str(src)])

    dest = _dest_for(src, opts)
    try:
        env = extract(src, dest, opts, cancelled=cancelled)
    except CommandCancelled as ex:
        partial = getattr(ex, "archive_report", None)
        partial_dest = Path(getattr(ex, "archive_out_dir", dest))
        if isinstance(partial, dict) and is_within(dest, partial_dest):
            cleanup_errors = _rollback_report(partial, partial_dest)
            if cleanup_errors:
                raise OSError("; ".join(cleanup_errors)) from ex
        raise
    if env["error"]:
        return Result(str(src), "failed", env["details"], detail=env["error_type"], out_path=str(dest))

    report = env["data"]
    try:
        if not opts.dry_run and opts.nested_depth > 0:
            _extract_nested(report, dest, opts, depth=1, cancelled=cancelled)

        action, reason = _summarize(report, opts.dry_run)
        report_files = None
        if not opts.dry_run:
            written_report = _write_reports(dest, report, cancelled=cancelled)
        else:
            written_report = None
    except CommandCancelled as ex:
        cleanup_errors = _rollback_report(report, dest)
        if cleanup_errors:
            raise OSError("; ".join(cleanup_errors)) from ex
        raise
    if written_report is not None:
        if written_report["error"]:
            return Result(
                str(src), "failed", written_report["details"],
                written=len(report["written"]), skipped=len(report["skipped"]),
                rejected=len(report["rejected"]), errors=len(report["errors"]) + 1,
                out_path=str(dest), degraded=True, detail=written_report["error_type"],
            )
        report_files = written_report["data"]
    json_record = report_files["json"] if report_files else None
    csv_record = report_files["csv"] if report_files else None
    return Result(
        str(src), action, reason,
        written=len(report["written"]), skipped=len(report["skipped"]),
        rejected=len(report["rejected"]),
        errors=len(report["errors"]) + (1 if report["aborted"] else 0),
        listed=len(report["listed"]), out_path=str(dest),
        report_path=json_record["path"] if json_record else None,
        report_bytes=json_record["bytes"] if json_record else 0,
        report_sha256=json_record["sha256"] if json_record else "",
        csv_report_path=csv_record["path"] if csv_record else None,
        csv_report_bytes=csv_record["bytes"] if csv_record else 0,
        csv_report_sha256=csv_record["sha256"] if csv_record else "",
        degraded=_degraded(report), detail=report["kind"],
    )


def _same_path(left, right) -> bool:
    try:
        return (
            os.path.normcase(str(Path(left).resolve(strict=False)))
            == os.path.normcase(str(Path(right).resolve(strict=False)))
        )
    except (OSError, TypeError, ValueError):
        return False


def _valid_digest(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _record_matches(record, expected: Path, cancelled=None) -> bool:
    if not isinstance(record, dict) or not _same_path(record.get("path"), expected):
        return False
    size = record.get("bytes")
    digest = record.get("sha256")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not _valid_digest(digest)
    ):
        return False
    try:
        return (
            expected.is_file()
            and expected.stat().st_size == size
            and sha256_file(expected, cancelled=cancelled) == digest
        )
    except OSError:
        return False


def _validate_report_tree(
    report: dict,
    dest: Path,
    archive: Path,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    list_fields = ("written", "skipped", "rejected", "errors", "listed")
    if (
        not isinstance(report, dict)
        or not all(isinstance(report.get(key), list) for key in list_fields)
        or not isinstance(report.get("aborted"), str)
        or not isinstance(report.get("kind"), str)
        or not _same_path(report.get("archive"), archive)
        or not _same_path(report.get("out_dir"), dest)
    ):
        return False

    seen: set[str] = set()
    for entry in report["written"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return False
        rel = _safe_relpath(entry["path"])
        size = entry.get("bytes")
        digest = entry.get("sha256")
        if (
            rel is None
            or rel.as_posix() != entry["path"]
            or entry["path"] in seen
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not _valid_digest(digest)
        ):
            return False
        seen.add(entry["path"])
        target = dest / rel
        try:
            if (
                not is_within(dest, target)
                or not target.is_file()
                or target.stat().st_size != size
                or sha256_file(target, cancelled=cancelled) != digest
            ):
                return False
        except OSError:
            return False

    nested = report.get("nested", [])
    if not isinstance(nested, list):
        return False
    for entry in nested:
        if not isinstance(entry, dict) or not isinstance(entry.get("archive"), str):
            return False
        rel = _safe_relpath(entry["archive"])
        sub_report = entry.get("report")
        report_files = entry.get("report_files")
        if rel is None or rel.as_posix() != entry["archive"] or not isinstance(sub_report, dict):
            return False
        child = dest / rel
        expected_dest = child.parent / (safe_name(child.stem) + "_unpacked")
        sub_written = sub_report.get("written")
        if (
            not is_within(dest, expected_dest)
            or not _same_path(entry.get("out_dir"), expected_dest)
            or not isinstance(sub_written, list)
            or not isinstance(entry.get("written"), int)
            or isinstance(entry.get("written"), bool)
            or entry["written"] != len(sub_written)
            or not isinstance(report_files, dict)
            or not _record_matches(
                report_files.get("json"), expected_dest / "_extract_report.json",
                cancelled,
            )
            or not _record_matches(
                report_files.get("csv"), expected_dest / "_extract_report.csv",
                cancelled,
            )
            or not _validate_report_tree(
                sub_report, expected_dest, child, cancelled=cancelled
            )
        ):
            return False
    return True


def validate_result(
    result: Result,
    opts: ExtractOptions,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Validate preview metadata or every exact artifact recorded by reports."""
    normalized, _ = _normalized_options(opts)
    if normalized is None:
        return False
    src = Path(result.src)
    dest = _dest_for(src, normalized)
    counters = (
        result.written, result.skipped, result.rejected, result.errors, result.listed,
        result.report_bytes, result.csv_report_bytes,
    )
    if (
        any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counters)
        or not isinstance(result.degraded, bool)
        or not _same_path(result.out_path, dest)
    ):
        return False
    if result.action == "dry-run":
        kind = _kind(src)
        expected_detail = "unitypackage" if kind == "unity" else kind
        return (
            normalized.dry_run
            and result.written == 0
            and result.reason == f"{result.listed} entries would extract"
            and result.detail == expected_detail
            and result.degraded == bool(result.rejected or result.errors)
            and result.report_path is None
            and result.csv_report_path is None
            and result.report_bytes == 0
            and result.csv_report_bytes == 0
            and not result.report_sha256
            and not result.csv_report_sha256
        )
    if result.action != "extracted" or normalized.dry_run:
        return False
    json_path = dest / "_extract_report.json"
    csv_path = dest / "_extract_report.csv"
    root_json = {
        "path": result.report_path,
        "bytes": result.report_bytes,
        "sha256": result.report_sha256,
    }
    root_csv = {
        "path": result.csv_report_path,
        "bytes": result.csv_report_bytes,
        "sha256": result.csv_report_sha256,
    }
    if (
        not _record_matches(root_json, json_path, cancelled)
        or not _record_matches(root_csv, csv_path, cancelled)
    ):
        return False
    try:
        report = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    expected_action, expected_reason = _summarize(report, False)
    return (
        expected_action == "extracted"
        and result.reason == expected_reason
        and result.written == len(report.get("written", []))
        and result.skipped == len(report.get("skipped", []))
        and result.rejected == len(report.get("rejected", []))
        and result.errors
        == len(report.get("errors", [])) + (1 if report.get("aborted") else 0)
        and result.listed == len(report.get("listed", []))
        and result.degraded == _degraded(report)
        and result.detail == report.get("kind")
        and _validate_report_tree(report, dest, src, cancelled=cancelled)
    )

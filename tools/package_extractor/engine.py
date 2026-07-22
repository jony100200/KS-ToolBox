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

Errors are values: the shared envelope (`ok`/`err`) wraps every fallible IO;
`is_within` / `safe_name` / the sizing checks are pure. See CodingPrinciples.md.

Public interface:
    is_within(root, target)            -> bool        (pure safe-path check)
    safe_name(name)                    -> str         (pure component sanitiser)
    extract_unitypackage(path, out, o) -> envelope(report)
    extract_zip(path, out, o)          -> envelope(report)
    extract_tar(path, out, o)          -> envelope(report)
    extract(path, out, o)              -> envelope(report)   (dispatch)
    process(path, o)                   -> Result      (extract one archive)
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tarfile
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

from toolbox.engine_common import ok, err

# Extensions the tool recognises (panel mirrors this).
ARCHIVE_EXTS = {".unitypackage", ".zip", ".tar", ".gz", ".tgz"}

# Decompression-bomb caps (bytes). Deliberately generous defaults; configurable.
DEFAULT_MAX_BYTES = 5 * 1024 ** 3        # per-archive total (5 GiB)
DEFAULT_MAX_FILE_BYTES = 2 * 1024 ** 3   # per single member (2 GiB)

_MAX_NESTED = 2                          # hard bound on nested-archive depth
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
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# decompression-bomb budget + streaming copy (atomic, hashed)
# ---------------------------------------------------------------------------

class _BombError(Exception):
    """Raised when a per-file or per-archive byte cap is exceeded."""


class _Budget:
    def __init__(self, max_total: int, max_file: int):
        self.max_total = int(max_total) if max_total else 0
        self.max_file = int(max_file) if max_file else 0
        self.total = 0

    def check_declared(self, size) -> None:
        size = int(size or 0)
        if self.max_file and size > self.max_file:
            raise _BombError(f"member declares {size} B > per-file cap {self.max_file} B")
        if self.max_total and self.total + size > self.max_total:
            raise _BombError(f"archive would exceed total cap {self.max_total} B")

    def add(self, n: int) -> None:
        self.total += n
        if self.max_total and self.total > self.max_total:
            raise _BombError(f"archive exceeded total cap {self.max_total} B")


def _stream_to(fileobj, target: Path, declared, budget: _Budget) -> tuple[int, str]:
    """Copy `fileobj` -> `target` atomically (`.part` then replace), counting bytes
    against the budget and hashing. Caps enforced on the ACTUAL bytes read, not
    just the declared size (a lying header can't get past the running total)."""
    budget.check_declared(declared)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    written = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if budget.max_file and written > budget.max_file:
                    raise _BombError(f"member exceeded per-file cap {budget.max_file} B")
                budget.add(len(chunk))
                h.update(chunk)
                out.write(chunk)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
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


def _handle_entry(name: str, size, open_fn, out_dir: Path,
                  opts: ExtractOptions, budget: _Budget, report: dict) -> None:
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
        report["listed"].append({"path": rel.as_posix(), "bytes": int(size or 0)})
        return
    final = _resolve_collision(target, opts.collision)
    if final is None:
        report["skipped"].append({"name": name, "reason": "collision (already exists), skipped"})
        return
    fo = open_fn()
    if fo is None:
        report["errors"].append({"name": name, "reason": "member could not be opened"})
        return
    try:
        with fo:
            written, sha = _stream_to(fo, final, size, budget)
        report["written"].append({"path": final.relative_to(out_dir).as_posix(),
                                  "bytes": written, "sha256": sha})
    except _BombError:
        raise
    except OSError as ex:
        report["errors"].append({"name": name, "reason": f"write failed: {ex}"})


# ---------------------------------------------------------------------------
# per-format extractors — each returns an envelope wrapping the report
# ---------------------------------------------------------------------------

def _degraded(report: dict) -> bool:
    return bool(report["aborted"] or report["errors"] or report["rejected"])


def extract_zip(path, out_dir: Path, opts: ExtractOptions) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "zip")
    if not zipfile.is_zipfile(path):
        return err("archive.bad", f"not a valid zip: {path.name}")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            try:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    _handle_entry(info.filename, info.file_size,
                                  lambda info=info: zf.open(info), out_dir, opts, budget, report)
            except _BombError as ex:
                report["aborted"] = str(ex)
    except (OSError, zipfile.BadZipFile) as ex:
        return err("archive.read", f"failed reading zip {path.name}: {ex}")
    return ok(report, degraded=_degraded(report))


def extract_tar(path, out_dir: Path, opts: ExtractOptions) -> dict:
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "tar")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "r:*") as tf:          # transparent gz/bz2/xz
            try:
                for m in tf:
                    if m.issym() or m.islnk():
                        report["skipped"].append({"name": m.name,
                                                  "reason": "symlink/hardlink skipped (not followed)"})
                        continue
                    if not m.isfile():
                        continue
                    _handle_entry(m.name, m.size,
                                  lambda m=m: tf.extractfile(m), out_dir, opts, budget, report)
            except _BombError as ex:
                report["aborted"] = str(ex)
    except (tarfile.TarError, OSError, EOFError) as ex:
        return err("archive.read", f"failed reading tar {path.name}: {ex}")
    return ok(report, degraded=_degraded(report))


def extract_unitypackage(path, out_dir: Path, opts: ExtractOptions) -> dict:
    """Reconstruct the original Unity folder tree from a gzipped-tar .unitypackage.

    Each `<GUID>/` entry carries `pathname` (the original project path) and
    `asset` (the file bytes). We read the pathname, sanitise + in-root-check the
    reconstructed path exactly like any other entry, then write the asset bytes."""
    path, out_dir = Path(path), Path(out_dir)
    report = _new_report(path, out_dir, "unitypackage")
    try:
        budget = _Budget(opts.max_bytes, opts.max_file_bytes)
        if not opts.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "r:gz") as tf:
            entries: dict[str, dict] = {}
            for m in tf.getmembers():
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
                    entries.setdefault(guid, {})[leaf] = m
            try:
                for guid, d in entries.items():
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
                                  lambda am=am: tf.extractfile(am), out_dir, opts, budget, report)
            except _BombError as ex:
                report["aborted"] = str(ex)
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


def extract(path, out_dir: Path, opts: ExtractOptions) -> dict:
    """Dispatch to the right per-format extractor by extension."""
    kind = _kind(Path(path))
    if kind == "unity":
        return extract_unitypackage(path, out_dir, opts)
    if kind == "zip":
        return extract_zip(path, out_dir, opts)
    if kind == "tar":
        return extract_tar(path, out_dir, opts)
    return err("archive.unsupported", f"unsupported archive type: {Path(path).name}")


# ---------------------------------------------------------------------------
# reports (JSON + CSV) + nested extraction
# ---------------------------------------------------------------------------

def _write_reports(dest: Path, report: dict) -> str | None:
    """Per-archive extraction report as JSON + CSV in the archive's own folder."""
    try:
        dest.mkdir(parents=True, exist_ok=True)
        jpath = dest / "_extract_report.json"
        with open(jpath, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
        cpath = dest / "_extract_report.csv"
        with open(cpath, "w", encoding="utf-8", newline="") as f:
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
        return str(jpath)
    except OSError:
        return None


def _extract_nested(report: dict, dest: Path, opts: ExtractOptions, depth: int) -> None:
    """After extracting, unpack any extracted file that is itself a supported
    archive. Bounded by opts.nested_depth AND the hard _MAX_NESTED ceiling."""
    if depth > min(opts.nested_depth, _MAX_NESTED):
        return
    for entry in list(report.get("written", [])):
        child = dest / Path(entry["path"])
        if _kind(child) is None or not child.is_file():
            continue
        sub = child.parent / (safe_name(child.stem) + "_unpacked")
        env = extract(child, sub, opts)
        if env["error"]:
            report["errors"].append({"name": entry["path"],
                                     "reason": f"nested extract failed: {env['details']}"})
            continue
        sub_report = env["data"]
        _write_reports(sub, sub_report)
        report.setdefault("nested", []).append(
            {"archive": entry["path"], "out_dir": str(sub), "written": len(sub_report["written"])})
        _extract_nested(sub_report, sub, opts, depth + 1)


def _dest_for(src: Path, opts: ExtractOptions) -> Path:
    """Each archive extracts into its own `<stem>/` folder under out_root (or
    beside the source when no out_root is given)."""
    root = Path(opts.out_root) if opts.out_root else src.parent
    return root / safe_name(src.stem)


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


def process(path: str | Path, opts: ExtractOptions) -> Result:
    """Extract one archive into its own subfolder; write per-archive reports."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if _kind(src) is None:
        return Result(str(src), "failed", f"unsupported archive type: {src.name}", detail="archive.unsupported")

    dest = _dest_for(src, opts)
    env = extract(src, dest, opts)
    if env["error"]:
        return Result(str(src), "failed", env["details"], detail=env["error_type"], out_path=str(dest))

    report = env["data"]
    if not opts.dry_run and opts.nested_depth > 0:
        _extract_nested(report, dest, opts, depth=1)

    report_path = None if opts.dry_run else _write_reports(dest, report)
    action, reason = _summarize(report, opts.dry_run)
    return Result(
        str(src), action, reason,
        written=len(report["written"]), skipped=len(report["skipped"]),
        rejected=len(report["rejected"]),
        errors=len(report["errors"]) + (1 if report["aborted"] else 0),
        listed=len(report["listed"]), out_path=str(dest),
        report_path=report_path, detail=report["kind"],
    )

"""Unity Packager — pure engine (headless, stdlib only, no UI imports).

A `.unitypackage` is a gzipped tar of `<guid>/pathname`, `<guid>/asset.meta` and
(for files) `<guid>/asset`. This module builds one straight from a project folder
on disk, so Unity never has to be opened.

Pipeline: validate -> plan (dry run) -> build (stage to `.part`, atomic replace).
Every public function returns the shared error envelope from `engine_common`.
"""
from __future__ import annotations

import gzip
import io
import os
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from toolbox.engine_common import err, ok, sha256_file

GUID_RE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.MULTILINE)
COLLISIONS = ("error", "overwrite", "copy")
LEVELS = {"Fast": 1, "Balanced": 6, "Smallest": 9}
_META_HEAD_BYTES = 4096          # the guid line is always near the top of a .meta


@dataclass(frozen=True)
class PackageOptions:
    project_root: Path
    includes: tuple[str, ...]            # project-relative paths under Assets/
    output: Path
    compress_level: int = 6
    collision: str = "copy"              # error | overwrite | copy


@dataclass(frozen=True)
class Entry:
    pathname: str                        # project-relative, forward slashes
    guid: str
    meta: Path
    asset: Path | None                   # None for folders


@dataclass
class Plan:
    entries: list[Entry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)       # human-readable reasons
    raw_bytes: int = 0

    @property
    def files(self) -> int:
        return sum(1 for e in self.entries if e.asset is not None)

    @property
    def folders(self) -> int:
        return len(self.entries) - self.files


def _ignored(name: str) -> bool:
    """Unity never imports hidden items, `~` items, or temp files."""
    return name.startswith(".") or name.endswith("~") or name.lower().endswith(".tmp")


def _read_guid(meta: Path) -> str | None:
    try:
        with meta.open("rb") as handle:
            head = handle.read(_META_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    match = GUID_RE.search(head)
    return match.group(1).lower() if match else None


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate(opts: PackageOptions) -> str | None:
    """Return a user-facing problem description, or None when the options are usable."""
    assets = opts.project_root / "Assets"
    if not assets.is_dir():
        return f"'{opts.project_root}' is not a Unity project (no Assets folder)."
    if not opts.includes:
        return "Pick at least one folder to include."
    if opts.collision not in COLLISIONS:
        return f"Unknown collision policy '{opts.collision}'."
    if not 0 <= opts.compress_level <= 9:
        return "Compression level must be 0-9."
    if opts.output.suffix.lower() != ".unitypackage":
        return "Output file must end with .unitypackage."
    for rel in opts.includes:
        target = (opts.project_root / rel).resolve()
        if not _is_within(assets, target) or target == assets.resolve():
            return f"'{rel}' must be a folder or file inside Assets (not Assets itself)."
        if not target.exists():
            return f"'{rel}' does not exist."
        if _is_within(target, opts.output) and target.is_dir():
            return f"The output file cannot be inside an included folder ('{rel}')."
    return None


def _entry_for(path: Path, root: Path, plan: Plan, seen: dict[str, str]) -> None:
    meta = path.with_name(path.name + ".meta")
    pathname = path.relative_to(root).as_posix()
    if not meta.is_file():
        plan.skipped.append(f"{pathname}: no .meta file (Unity would give it a new GUID)")
        return
    guid = _read_guid(meta)
    if guid is None:
        plan.skipped.append(f"{pathname}: .meta has no readable guid")
        return
    if guid in seen:
        if seen[guid] != pathname:
            raise ValueError(f"duplicate GUID {guid}: '{seen[guid]}' and '{pathname}'")
        return
    seen[guid] = pathname
    is_file = path.is_file()
    plan.entries.append(Entry(pathname, guid, meta, path if is_file else None))
    plan.raw_bytes += meta.stat().st_size + (path.stat().st_size if is_file else 0)


def _walk(target: Path, root: Path, plan: Plan, seen: dict[str, str]) -> None:
    if target.is_file():
        _entry_for(target, root, plan, seen)
        return
    for current, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if not _ignored(d))
        _entry_for(Path(current), root, plan, seen)
        for name in sorted(files):
            if name.endswith(".meta") or _ignored(name):
                continue
            _entry_for(Path(current) / name, root, plan, seen)


def collect(opts: PackageOptions) -> Plan:
    """Walk the included paths (plus their ancestor folders) into a sorted, de-duplicated plan."""
    root = opts.project_root.resolve()
    assets = root / "Assets"
    plan, seen = Plan(), {}
    for rel in dict.fromkeys(opts.includes):
        target = (root / rel).resolve()
        for ancestor in reversed(target.parents):          # Assets/<Publisher> etc.
            if ancestor != assets and _is_within(assets, ancestor):
                _entry_for(ancestor, root, plan, seen)
        _walk(target, root, plan, seen)
    plan.entries.sort(key=lambda e: e.pathname)
    return plan


def plan(opts: PackageOptions) -> dict:
    """Dry run: what would be packaged, and what would be left out."""
    problem = validate(opts)
    if problem:
        return err("invalid-options", problem)
    try:
        built = collect(opts)
    except (OSError, ValueError) as exc:
        return err("plan-failed", str(exc))
    if not built.files:
        return err("nothing-to-package", "No files with a valid .meta were found in the selected folders.")
    return ok(built, degraded=bool(built.skipped),
              details=f"{built.files} file(s), {built.folders} folder(s), {len(built.skipped)} skipped")


def resolve_output(output: Path, collision: str) -> Path | None:
    """Apply the collision policy. None means 'refuse' (policy `error`)."""
    if not output.exists() or collision == "overwrite":
        return output
    if collision == "error":
        return None
    for n in range(2, 1000):
        candidate = output.with_name(f"{output.stem} ({n}){output.suffix}")
        if not candidate.exists():
            return candidate
    return None


class _Cancelled(Exception):
    pass


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def build(opts: PackageOptions, cancelled: Callable[[], bool] | None = None,
          progress: Callable[[int, int], None] | None = None) -> dict:
    """Build the package. Output is staged next to the target and replaced atomically."""
    planned = plan(opts)
    if planned["error"]:
        return planned
    built: Plan = planned["data"]
    target = resolve_output(opts.output, opts.collision)
    if target is None:
        return err("output-exists", f"'{opts.output.name}' already exists and the collision policy is 'error'.")

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with part.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=opts.compress_level, mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w") as tar:
            total = len(built.entries)
            for index, entry in enumerate(built.entries, start=1):
                if cancelled is not None and cancelled():
                    raise _Cancelled()
                _add_bytes(tar, f"{entry.guid}/pathname", (entry.pathname + "\n").encode("utf-8"))
                _add_bytes(tar, f"{entry.guid}/asset.meta", entry.meta.read_bytes())
                if entry.asset is not None:
                    info = tarfile.TarInfo(f"{entry.guid}/asset")
                    info.size = entry.asset.stat().st_size
                    with entry.asset.open("rb") as handle:
                        tar.addfile(info, fileobj=handle)
                if progress is not None:
                    progress(index, total)
        digest = sha256_file(part)
        os.replace(part, target)
    except _Cancelled:
        return err("cancelled", "Cancelled — no package was written.")
    except (OSError, tarfile.TarError) as exc:
        return err("build-failed", str(exc), retryable=True)
    finally:
        part.unlink(missing_ok=True)

    return ok({"path": str(target), "files": built.files, "folders": built.folders,
               "raw_bytes": built.raw_bytes, "package_bytes": target.stat().st_size,
               "sha256": digest, "skipped": built.skipped},
              degraded=bool(built.skipped),
              details=f"Wrote {target.name}")

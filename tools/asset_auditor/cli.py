"""Headless CLI runner for KS-ToolBox Asset Auditor & HDD-Gentle Duplicate Finder.

Usage:
    python -m tools.asset_auditor.cli dupes -i <root> -o <report_dir>
    python -m tools.asset_auditor.cli dupes-clean -r <report_dir>/dupes.json --execute
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.asset_auditor import engine as e  # noqa: E402
from toolbox.engine_common import VIDEO_EXTS, IMAGE_EXTS  # noqa: E402


def _cmd_dupes(args: argparse.Namespace) -> int:
    roots = [Path(p) for p in args.input]
    for r in roots:
        if not r.exists():
            print(f"Error: path does not exist: {r}", file=sys.stderr)
            return 1

    exts: frozenset[str] | None = None
    if args.scope == "images":
        exts = frozenset(IMAGE_EXTS)
    elif args.scope == "videos":
        exts = frozenset(VIDEO_EXTS)
    elif args.scope == "media":
        exts = frozenset(IMAGE_EXTS | VIDEO_EXTS | {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus"})

    skip_dirs = frozenset(set(e.DEFAULT_SKIP_DIRS) | set(args.skip_dir or ()))
    opts = e.DupScanOptions(
        out_root=Path(args.out) if args.out else None,
        include_exts=exts,
        min_size_bytes=int(args.min_size_kb * 1024),
        skip_dirs=skip_dirs,
    )

    def on_progress(stage: str, done: int, total: int) -> None:
        if total:
            print(f"[{stage}] {done}/{total}", flush=True)
        else:
            print(f"[{stage}] {done} files...", flush=True)

    report = e.find_exact_duplicates(roots, opts, progress=on_progress)
    print(
        f"Scanned {report.total_files:,} files ({report.total_bytes / 1e9:.2f} GB) | "
        f"unique-size skipped={report.unique_size_skipped:,} | "
        f"hardlinks skipped={report.hardlinks_skipped:,} | "
        f"size-candidates={report.size_candidates:,} | partial-candidates={report.partial_candidates:,}"
    )
    print(
        f"HDD I/O read: partial={report.bytes_read_partial / 1e6:.1f} MB, "
        f"full={report.bytes_read_full / 1e9:.2f} GB"
    )
    print(
        f"Duplicates: {len(report.groups):,} groups, {report.total_duplicate_files:,} extra files, "
        f"{report.reclaimable_bytes / 1e9:.2f} GB reclaimable"
    )
    for idx, grp in enumerate(report.groups[:15], 1):
        print(f"  #{idx:2d} | {len(grp.paths)}x {grp.size_bytes/1e6:7.2f} MB | reclaim {grp.reclaimable_bytes/1e6:7.2f} MB | {grp.paths[0]}")
    if args.out:
        print(f"Reports written to: {Path(args.out)}")
    return 0


def _cmd_dupes_clean(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"Error: dupes.json not found: {report_path}", file=sys.stderr)
        return 1
    dry_run = not args.execute
    min_bytes = int(args.min_size_kb * 1024)

    def on_progress(idx: int, total: int, item: e.DupCleanItem) -> None:
        print(
            f"[{idx}/{total}] {item.status.upper():10s} {item.size_bytes/1e6:7.2f} MB | "
            f"keep={item.keep_path} | dup={item.dup_path} | {item.reason}",
            flush=True,
        )

    items = e.cleanup_exact_duplicates(
        report_path,
        keep_strategy=args.keep,
        prefer_prefixes=tuple(args.prefer or ()),
        avoid_prefixes=tuple(args.avoid or ()),
        verify_sha=not args.no_verify_sha,
        min_size_bytes=min_bytes,
        permanent_delete=args.permanent_delete,
        dry_run=dry_run,
        progress=on_progress,
    )
    ready = sum(1 for x in items if x.status == "dry_run_ok")
    removed = sum(1 for x in items if x.status == "removed")
    rejected = sum(1 for x in items if x.status == "rejected")
    reclaimed = sum(x.size_bytes for x in items if x.status in {"dry_run_ok", "removed"})

    if not dry_run and items:
        mpath = report_path.parent / "dupes_cleanup_manifest.csv"
        with mpath.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["status", "size_bytes", "group_sha256", "keep_path", "dup_path", "reason"])
            for x in items:
                w.writerow([x.status, x.size_bytes, x.group_sha256, x.keep_path, x.dup_path, x.reason])

    print(
        f"Dup cleanup summary (dry_run={dry_run}): candidates={len(items)} "
        f"ready={ready} removed={removed} rejected={rejected} "
        f"reclaimable_or_reclaimed={reclaimed / 1e9:.2f} GB"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KS-ToolBox Asset Auditor & Duplicate Finder CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("dupes", help="HDD-gentle 3-stage exact duplicate finder")
    pd.add_argument("-i", "--input", nargs="+", required=True, help="Root folder(s) to scan")
    pd.add_argument("-o", "--out", default=None, help="Output folder for dupes.json / dupes.csv / dupes.html")
    pd.add_argument("--scope", default="all", choices=["all", "media", "videos", "images"])
    pd.add_argument("--min-size-kb", type=float, default=1.0, help="Ignore files smaller than N KB (default 1 KB)")
    pd.add_argument("--skip-dir", action="append", default=[], help="Extra directory names to skip")

    pc = sub.add_parser("dupes-clean", help="Re-verify dupes.json and safely remove extra duplicate copies")
    pc.add_argument("-r", "--report", required=True, help="Path to dupes.json produced by 'dupes'")
    pc.add_argument("--keep", default="shortest_path", choices=["shortest_path", "oldest"])
    pc.add_argument("--prefer", action="append", default=[], help="Path prefix to prefer keeping")
    pc.add_argument("--avoid", action="append", default=[], help="Path prefix to prefer removing (temp/backup)")
    pc.add_argument("--min-size-kb", type=float, default=1.0)
    pc.add_argument("--no-verify-sha", action="store_true", help="Skip full SHA-256 re-verification before removal")
    pc.add_argument("--permanent-delete", action="store_true", help="Unlink instead of Recycle Bin")
    pc.add_argument("--execute", action="store_true", help="Actually remove duplicates (default is dry-run)")

    px = sub.add_parser("index", help="Build HDD-gentle bounded-depth finder (00_FIND_EVERYTHING.html + 00_DRIVE_MAP.md)")
    px.add_argument("-i", "--input", required=True, help="Root folder or drive to index")
    px.add_argument("-o", "--out", default=None, help="Output folder (defaults to input root)")
    px.add_argument("--max-depth", type=int, default=3, help="Max directory depth to walk (default 3)")
    px.add_argument("--file-depth", type=int, default=2, help="Max folder depth for standalone file entries (default 2)")
    px.add_argument("--min-file-mb", type=float, default=1.0, help="Min size in MB for standalone files (default 1.0)")
    px.add_argument("--skip-dir", action="append", default=[], help="Extra directory names to skip")

    args = parser.parse_args(argv)
    if args.command == "dupes":
        return _cmd_dupes(args)
    if args.command == "dupes-clean":
        return _cmd_dupes_clean(args)
    if args.command == "index":
        skip = frozenset(set(e.DEFAULT_SKIP_DIRS) | set(args.skip_dir or ()))
        summary = e.build_drive_index(
            args.input,
            args.out,
            max_depth=max(1, args.max_depth),
            include_files_depth=max(0, args.file_depth),
            min_file_bytes=int(max(0.0, args.min_file_mb) * 1024 * 1024),
            skip_dirs=skip,
        )
        print(
            f"Indexed {summary['entries']:,} entries ({summary['dirs']:,} dirs, {summary['files']:,} files) -> "
            f"{summary['html_path']} & {summary['md_path']}"
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

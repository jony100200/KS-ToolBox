"""Headless CLI runner for KS-ToolBox Audio Compressor & Proof Cleanup."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.audio_tool import engine as e  # noqa: E402
from toolbox.engine_common import iter_files_sequential, sweep_part_files  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KS-ToolBox Audio Compressor CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    pc = sub.add_parser("compress")
    pc.add_argument("-i", "--input", required=True)
    pc.add_argument("-o", "--out", default=None)
    pc.add_argument("--no-mirror", dest="mirror", action="store_false", default=True)
    pc.add_argument("--format", default="opus", choices=["opus", "m4a", "mp3", "ogg"])
    pc.add_argument("--bitrate", default="128k")
    pc.add_argument("--min-size-kb", type=float, default=64.0)
    pc.add_argument("--include-lossy", action="store_true")
    pc.add_argument("--work-dir", default=None)
    pc.add_argument("--in-place", action="store_true")
    pc.add_argument("--skip-dir", action="append", default=[])
    pc.add_argument("--execute", action="store_true")
    pc.add_argument("--delete-original", action="store_true")

    pl = sub.add_parser("cleanup")
    pl.add_argument("-o", "--out", required=True)
    pl.add_argument("--permanent-delete", action="store_true")
    pl.add_argument("--execute", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "cleanup":
        rows = e.cleanup_verified_originals(
            args.out, permanent_delete=args.permanent_delete, dry_run=not args.execute
        )
        ready = sum(1 for r in rows if r["status"] == "dry_run_ok")
        removed = sum(1 for r in rows if r["status"] == "removed")
        saved = sum(r["saved_bytes"] for r in rows if r["status"] in {"dry_run_ok", "removed"})
        print(f"Audio cleanup (dry_run={not args.execute}): proofs={len(rows)} ready={ready} removed={removed} saved={saved/1e6:.2f} MB")
        return 0

    src_root = Path(args.input)
    out_root = Path(args.out) if args.out else None
    work_dir = Path(args.work_dir) if args.work_dir else None
    if args.execute:
        if out_root and out_root.is_dir():
            sweep_part_files(out_root)
        if work_dir and work_dir.is_dir():
            sweep_part_files(work_dir)
    files = iter_files_sequential(
        src_root,
        e.AUDIO_EXTS,
        min_bytes=int(args.min_size_kb * 1024),
        exclude_roots=(out_root,) if out_root is not None else (),
        skip_dirs=frozenset(args.skip_dir or ()),
    )
    opts = e.CompressOptions(
        target_format=args.format,
        bitrate=args.bitrate,
        out_root=out_root,
        input_root=(src_root if src_root.is_dir() else src_root.parent) if args.mirror else None,
        mirror=args.mirror,
        min_bytes=int(args.min_size_kb * 1024),
        only_bloated=not args.include_lossy,
        delete_original=args.delete_original,
        dry_run=not args.execute,
        work_dir=work_dir,
        in_place=args.in_place,
    )
    print(f"Audio Compressor CLI | files={len(files)} | target={args.format} {args.bitrate} | dry_run={opts.dry_run}")
    compressed = skipped = failed = dry = 0
    saved_kb = 0.0
    for idx, fp in enumerate(files, 1):
        res = e.compress_audio(fp, opts)
        if res.action == "compressed":
            compressed += 1
            saved_kb += max(0.0, res.before_kb - res.after_kb)
            print(f"[{idx}/{len(files)}] OK   {fp.name} | {res.before_kb:.0f}->{res.after_kb:.0f} KB (-{res.saved_pct:.1f}%)", flush=True)
        elif res.action == "failed":
            failed += 1
            print(f"[{idx}/{len(files)}] FAIL {fp.name} | {res.reason}", file=sys.stderr, flush=True)
        elif res.action == "dry-run":
            dry += 1
        else:
            skipped += 1
    print(f"Summary: compressed={compressed} skipped={skipped} dry_run={dry} failed={failed} saved={saved_kb/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

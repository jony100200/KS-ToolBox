"""Headless CLI runner for KS-ToolBox Image Compressor & Proof Cleanup."""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.image_compressor import engine as e  # noqa: E402
from toolbox.engine_common import IMAGE_EXTS, iter_files_sequential, sweep_part_files  # noqa: E402


def _cmd_compress(args: argparse.Namespace) -> int:
    src_root = Path(args.input)
    if not src_root.exists():
        print(f"Error: input does not exist: {src_root}", file=sys.stderr)
        return 1
    out_root = Path(args.out) if args.out else None
    work_dir = Path(args.work_dir) if args.work_dir else None
    if args.execute:
        if out_root and out_root.is_dir():
            sweep_part_files(out_root)
        if work_dir and work_dir.is_dir():
            sweep_part_files(work_dir)

    files = iter_files_sequential(
        src_root,
        IMAGE_EXTS,
        min_bytes=int(args.min_size_kb * 1024),
        exclude_roots=(out_root,) if out_root is not None else (),
        skip_dirs=frozenset(args.skip_dir or ()),
    )
    if args.limit > 0:
        files = files[:args.limit]
    if not files:
        print("No matching images found.")
        return 0

    opts = e.CompressOptions(
        out_root=out_root,
        input_root=(src_root if src_root.is_dir() else src_root.parent) if args.mirror else None,
        mirror=args.mirror,
        mode=args.mode,
        quality=args.quality,
        psnr_floor=args.psnr,
        min_saving=args.min_saving,
        min_bytes=int(args.min_size_kb * 1024),
        delete_original=args.delete_original,
        skip_existing=not args.no_skip_existing,
        dry_run=not args.execute,
        work_dir=work_dir,
        in_place=args.in_place,
    )
    print(f"Image Compressor CLI | files={len(files)} | mode={args.mode} | dry_run={opts.dry_run}")

    counts = {"compressed": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    saved_kb = 0.0
    rows: list[e.Result] = []
    for idx, fp in enumerate(files, 1):
        res = e.process(fp, opts)
        rows.append(res)
        counts[res.action] = counts.get(res.action, 0) + 1
        if res.action == "compressed":
            saved_kb += max(0.0, res.before_kb - res.after_kb)
            if idx <= 25 or idx % 100 == 0:
                print(f"[{idx}/{len(files)}] OK   {fp.name} | {res.before_kb:.0f}->{res.after_kb:.0f} KB (-{res.saved_pct:.1f}%) {res.reason}", flush=True)
    print(f"Summary: compressed={counts['compressed']} skipped={counts['skipped']} dry_run={counts['dry-run']} failed={counts['failed']} saved={saved_kb/1024:.2f} MB")
    if args.execute and out_root is not None and rows:
        out_root.mkdir(parents=True, exist_ok=True)
        mpath = out_root / "image_compression_manifest.csv"
        new = not mpath.exists()
        with mpath.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["src", "action", "before_kb", "after_kb", "saved_pct", "lossless_exact", "psnr", "out_path", "original_removed", "reason"])
            for r in rows:
                w.writerow([r.src, r.action, r.before_kb, r.after_kb, r.saved_pct, r.lossless_exact, r.psnr, r.out_path, r.original_removed, r.reason])
    return 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    root = Path(args.out)
    if not root.is_dir():
        print(f"Error: proof root missing: {root}", file=sys.stderr)
        return 1
    results = e.cleanup_verified_originals(
        root,
        psnr_floor=args.psnr,
        permanent_delete=args.permanent_delete,
        dry_run=not args.execute,
    )
    ready = sum(1 for r in results if r.status == "dry_run_ok")
    removed = sum(1 for r in results if r.status == "removed")
    saved = sum(r.saved_bytes for r in results if r.status in {"dry_run_ok", "removed"})
    print(f"Image cleanup (dry_run={not args.execute}): proofs={len(results)} ready={ready} removed={removed} saved={saved/1e6:.2f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KS-ToolBox Image Compressor CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    pc = sub.add_parser("compress")
    pc.add_argument("-i", "--input", required=True)
    pc.add_argument("-o", "--out", default=None)
    pc.add_argument("--no-mirror", dest="mirror", action="store_false", default=True)
    pc.add_argument("--mode", default="lossless_webp", choices=list(e.MODES))
    pc.add_argument("--quality", type=int, default=90)
    pc.add_argument("--psnr", type=float, default=42.0)
    pc.add_argument("--min-saving", type=float, default=0.05)
    pc.add_argument("--min-size-kb", type=float, default=8.0)
    pc.add_argument("--limit", type=int, default=0)
    pc.add_argument("--no-skip-existing", action="store_true")
    pc.add_argument("--work-dir", default=None)
    pc.add_argument("--in-place", action="store_true")
    pc.add_argument("--skip-dir", action="append", default=[])
    pc.add_argument("--execute", action="store_true")
    pc.add_argument("--delete-original", action="store_true")

    pl = sub.add_parser("cleanup")
    pl.add_argument("-o", "--out", required=True)
    pl.add_argument("--psnr", type=float, default=42.0)
    pl.add_argument("--permanent-delete", action="store_true")
    pl.add_argument("--execute", action="store_true")

    args = parser.parse_args(argv)
    return _cmd_compress(args) if args.command == "compress" else _cmd_cleanup(args)


if __name__ == "__main__":
    raise SystemExit(main())

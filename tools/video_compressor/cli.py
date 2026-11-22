"""Headless CLI runner for KS-ToolBox Video Compressor & Proof Cleanup.

Supports sequential, HDD-gentle batch compression with VMAF verification and
subsequent verification + removal of replaced larger originals.

Usage:
    python -m tools.video_compressor.cli compress -i <dir> -o <out_dir> --dry-run
    python -m tools.video_compressor.cli cleanup -o <out_dir> --dry-run
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.video_compressor import engine as e  # noqa: E402
from toolbox.engine_common import VIDEO_EXTS, iter_files_sequential, sweep_part_files  # noqa: E402


def _iter_videos_sequential(
    root: Path,
    *,
    min_bytes: int = 0,
    exclude_roots: tuple[Path, ...] = (),
    skip_dirs: frozenset[str] = frozenset(),
) -> list[Path]:
    """Single-threaded os.scandir walk sorted deterministically (gentle on HDDs)."""
    return iter_files_sequential(
        root, VIDEO_EXTS, min_bytes=min_bytes,
        exclude_roots=exclude_roots, skip_dirs=skip_dirs,
    )


def _cmd_compress(args: argparse.Namespace) -> int:
    src_root = Path(args.input)
    if not src_root.exists():
        print(f"Error: input does not exist: {src_root}", file=sys.stderr)
        return 1

    working = e.available_encoders(verify=True)
    if args.encoder not in working:
        print(
            f"Error: encoder {args.encoder!r} is unavailable/failed probe on this system. "
            f"Working encoders: {', '.join(working) or 'none'}",
            file=sys.stderr,
        )
        return 1

    out_root = Path(args.out) if args.out else None
    work_dir = Path(args.work_dir) if args.work_dir else None
    if args.sweep_parts and args.execute:
        swept = 0
        if out_root and out_root.is_dir():
            swept += sweep_part_files(out_root)
        if work_dir and work_dir.is_dir():
            swept += sweep_part_files(work_dir)
        if swept:
            print(f"Swept {swept} stale .part file(s) before starting.", flush=True)

    exclude_roots = (out_root,) if out_root is not None else ()
    min_bytes = int(args.min_size_mb * 1024 * 1024)
    files = _iter_videos_sequential(
        src_root,
        min_bytes=min_bytes,
        exclude_roots=exclude_roots,
        skip_dirs=frozenset(args.skip_dir or ()),
    )
    if args.limit > 0:
        files = files[:args.limit]
    if not files:
        print("No matching video files found.")
        return 0

    input_root = src_root if src_root.is_dir() else src_root.parent
    policy = e.Policy(
        crf=args.crf,
        encoder=args.encoder,
        max_height=args.max_height,
        max_fps=args.max_fps,
        audio_mode=args.audio,
        bpp_efficient_hevc=args.bpp_efficient_hevc,
        bpp_efficient_h264=args.bpp_efficient_h264,
        min_expected_saving=args.min_expected_saving,
    )
    opts = e.ProcessOptions(
        out_root=out_root,
        input_root=input_root if args.mirror else None,
        mirror=args.mirror,
        policy=policy,
        vmaf_floor=args.vmaf,
        vmaf_sample_seconds=None if args.vmaf_full else args.vmaf_sample_seconds,
        delete_original=args.delete_original,
        skip_existing=not args.no_skip_existing,
        dry_run=not args.execute,
        container=args.container,
        work_dir=work_dir,
        in_place=args.in_place,
    )

    print(
        f"Video Compressor CLI | files={len(files)} | encoder={args.encoder} crf={args.crf} "
        f"| vmaf>={args.vmaf} | dry_run={opts.dry_run} | delete_original={opts.delete_original}"
    )

    counts = {"compressed": 0, "skipped": 0, "failed": 0, "dry-run": 0}
    saved_mb = 0.0
    mpath = Path(args.manifest) if args.manifest else ((out_root / "compression_manifest.csv") if out_root is not None else None)
    mf_fh = None
    mf_writer = None
    if args.execute and mpath is not None:
        mpath.parent.mkdir(parents=True, exist_ok=True)
        is_new = not mpath.exists()
        mf_fh = mpath.open("a", newline="", encoding="utf-8")
        mf_writer = csv.writer(mf_fh)
        if is_new:
            mf_writer.writerow(["src", "action", "before_mb", "after_mb", "saved_pct", "vmaf", "out_path", "original_removed", "reason"])
            mf_fh.flush()

    try:
        for idx, fpath in enumerate(files, 1):
            res = e.process(fpath, opts)
            counts[res.action] = counts.get(res.action, 0) + 1
            if mf_writer is not None and mf_fh is not None:
                mf_writer.writerow([res.src, res.action, res.before_mb, res.after_mb, res.saved_pct, res.vmaf, res.out_path, res.original_removed, res.reason])
                mf_fh.flush()
            if res.action == "compressed":
                saved_mb += max(0.0, res.before_mb - res.after_mb)
                vmaf_s = f"{res.vmaf:.1f}" if res.vmaf is not None else "unproven"
                print(
                    f"[{idx}/{len(files)}] OK   {fpath.name} | "
                    f"{res.before_mb:.1f}->{res.after_mb:.1f} MB (-{res.saved_pct:.1f}%) VMAF={vmaf_s}",
                    flush=True,
                )
            elif res.action == "failed":
                print(f"[{idx}/{len(files)}] FAIL {fpath.name} | {res.reason}", file=sys.stderr, flush=True)
            else:
                print(f"[{idx}/{len(files)}] {res.action.upper():4s} {fpath.name} | {res.reason}", flush=True)
    finally:
        if mf_fh is not None:
            mf_fh.close()

    print(
        f"Summary: compressed={counts['compressed']} skipped={counts['skipped']} "
        f"dry_run={counts['dry-run']} failed={counts['failed']} saved={saved_mb:.1f} MB"
    )
    return 1 if counts["failed"] > 0 and counts["compressed"] == 0 else 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    root = Path(args.out)
    if not root.is_dir():
        print(f"Error: proof root does not exist: {root}", file=sys.stderr)
        return 1

    dry_run = not args.execute

    def on_progress(idx: int, total: int, vr: e.ProofVerification) -> None:
        mb_saved = vr.saved_bytes / 1e6
        print(
            f"[{idx}/{total}] {vr.status.upper():15s} {Path(vr.out_path).name} | "
            f"saved={mb_saved:.1f} MB | {vr.reason}",
            flush=True,
        )

    results = e.cleanup_verified_originals(
        root,
        vmaf_floor=args.vmaf,
        verify_source_sha=args.verify_sha,
        check_duration=not args.no_check_duration,
        permanent_delete=args.permanent_delete,
        dry_run=dry_run,
        progress=on_progress,
    )
    total_saved = sum(r.saved_bytes for r in results if r.status in {"dry_run_ok", "removed"})
    removed = sum(1 for r in results if r.status == "removed")
    verified_dry = sum(1 for r in results if r.status == "dry_run_ok")
    already = sum(1 for r in results if r.status == "already_removed")
    rejected = sum(1 for r in results if r.status == "rejected")

    if not dry_run and results:
        mpath = root / "cleanup_manifest.csv"
        with mpath.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["status", "src", "out_path", "before_bytes", "after_bytes", "saved_bytes", "vmaf", "reason"])
            for r in results:
                w.writerow([r.status, r.src, r.out_path, r.before_bytes, r.after_bytes, r.saved_bytes, r.vmaf, r.reason])

    print(
        f"Cleanup summary (dry_run={dry_run}): total_proofs={len(results)} "
        f"verified_ready={verified_dry} removed={removed} already_removed={already} "
        f"rejected={rejected} reclaimable_or_reclaimed={total_saved / 1e9:.2f} GB"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KS-ToolBox Video Compressor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("compress", help="Analyze and compress videos with VMAF verification")
    pc.add_argument("-i", "--input", required=True, help="Input file or directory")
    pc.add_argument("-o", "--out", default=None, help="Output root directory")
    pc.add_argument("--no-mirror", dest="mirror", action="store_false", default=True)
    pc.add_argument("--encoder", default="svt_av1", choices=list(e.ENCODERS))
    pc.add_argument("--crf", type=int, default=20)
    pc.add_argument("--vmaf", type=float, default=92.0)
    pc.add_argument("--vmaf-sample-seconds", type=float, default=60.0)
    pc.add_argument("--vmaf-full", action="store_true")
    pc.add_argument("--container", default="mkv", choices=["mkv", "mp4"])
    pc.add_argument("--max-height", type=int, default=None)
    pc.add_argument("--max-fps", type=float, default=None)
    pc.add_argument("--audio", default="copy", choices=["copy", "shrink_bloated"])
    pc.add_argument("--bpp-efficient-hevc", type=float, default=0.045)
    pc.add_argument("--bpp-efficient-h264", type=float, default=0.070)
    pc.add_argument("--min-expected-saving", type=float, default=0.15)
    pc.add_argument("--min-size-mb", type=float, default=0.0)
    pc.add_argument("--limit", type=int, default=0)
    pc.add_argument("--no-skip-existing", action="store_true")
    pc.add_argument("--work-dir", default=None, help="Fast SSD staging dir for encode + VMAF (gentle on HDDs)")
    pc.add_argument("--in-place", action="store_true", help="Write compressed output beside each source when --out is omitted")
    pc.add_argument("--skip-dir", action="append", default=[], help="Extra directory names to skip")
    pc.add_argument("--sweep-parts", action="store_true", help="Remove stale .part files in out/work dirs before starting")
    pc.add_argument("--manifest", default=None, help="Explicit CSV manifest path (default: <out>/compression_manifest.csv)")
    pc.add_argument("--execute", action="store_true", help="Actually write outputs (default is dry-run)")
    pc.add_argument("--delete-original", action="store_true", help="Remove source immediately after verified encode")

    pl = sub.add_parser("cleanup", help="Verify existing compression proofs and remove bigger originals")
    pl.add_argument("-o", "--out", required=True, help="Output directory containing .mkv.json / .mp4.json proofs")
    pl.add_argument("--vmaf", type=float, default=92.0)
    pl.add_argument("--verify-sha", action="store_true", help="Also verify full SHA-256 of original against proof")
    pl.add_argument("--no-check-duration", action="store_true")
    pl.add_argument("--permanent-delete", action="store_true", help="Unlink instead of Recycle Bin")
    pl.add_argument("--execute", action="store_true", help="Actually remove verified originals (default is dry-run)")

    args = parser.parse_args(argv)
    if args.command == "compress":
        return _cmd_compress(args)
    return _cmd_cleanup(args)


if __name__ == "__main__":
    raise SystemExit(main())

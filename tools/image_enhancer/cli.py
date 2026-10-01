"""Headless CLI runner for KS-ToolBox Image Enhancer.

Runs batch enhancement from a script or the command line without launching the
CustomTkinter GUI.

Usage:
    python -m tools.image_enhancer.cli --input <file_or_dir> --out <dir> --preset auto
    python -m tools.image_enhancer.cli --input <dir> --out <dir> --preset natural_skin --scale 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure root KS-ToolBox is in sys.path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.image_enhancer import engine as e
from tools.image_enhancer import filter_stack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KS-ToolBox Headless Image Enhancer CLI")
    parser.add_argument("--input", "-i", required=True, help="Path to input image or directory")
    parser.add_argument("--out", "-o", required=True, help="Destination directory for enhanced images")
    parser.add_argument("--preset", "-p", default="auto", choices=list(filter_stack.preset_names()),
                        help="Restoration preset (default: auto)")
    parser.add_argument("--mode", "-m", default="deterministic", choices=["deterministic", "hybrid", "ai"],
                        help="Execution mode (default: deterministic)")
    parser.add_argument("--scale", "-s", type=int, default=1, choices=[1, 2, 3, 4],
                        help="Super-resolution output scale (default: 1)")
    parser.add_argument("--model", default="realesrgan-x4plus", choices=list(e._ESRGAN_SELECTABLE),
                        help="Real-ESRGAN model weight name")
    parser.add_argument("--region", default="none", choices=["none", "faces", "subject_mask"],
                        help="Target region mask mode")
    parser.add_argument("--face-detail", action="store_true", help="Apply super-resolution pass to detected face boxes")
    parser.add_argument("--repair-holes", action="store_true", help="Repair enclosed transparent holes in alpha channel")
    parser.add_argument("--mirror", action="store_true", default=True, help="Preserve input directory structure in output")
    parser.add_argument("--dry-run", action="store_true", help="Profile inputs without writing outputs")
    parser.add_argument("--limit", type=int, default=0, help="Maximum images to process (0 = all)")
    args = parser.parse_args(argv)

    src_path = Path(args.input)
    if not src_path.exists():
        print(f"Error: input path does not exist: {src_path}", file=sys.stderr)
        return 1

    if src_path.is_file():
        files = [src_path]
        input_root = src_path.parent
    else:
        files = sorted(p for p in src_path.rglob("*") if p.is_file() and p.suffix.lower() in e.IMAGE_EXTS and not p.stem.startswith("_"))
        input_root = src_path

    if args.limit > 0:
        files = files[:args.limit]

    if not files:
        print(f"No valid images found in {src_path}", file=sys.stderr)
        return 1

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"KS-ToolBox Image Enhancer CLI")
    print(f"Files: {len(files)} | Preset: {args.preset} | Mode: {args.mode} | Scale: {args.scale}x")
    print(f"Output Root: {out_root}")

    opts = e.EnhanceOptions(
        out_root=out_root,
        input_root=input_root if args.mirror else None,
        mirror=args.mirror,
        preset=args.preset,
        mode=args.mode,
        auto_select_mode=(args.preset == "auto"),
        scale_factor=args.scale,
        ai_model=args.model,
        region_mode=args.region,
        face_detail=args.face_detail,
        repair_alpha_holes=args.repair_holes,
        dry_run=args.dry_run,
    )

    t0 = time.time()
    ok_count = 0
    fail_count = 0
    manifest = []

    for i, file_path in enumerate(files, 1):
        item_t0 = time.time()
        res = e.process(file_path, opts)
        item_elapsed = time.time() - item_t0

        manifest.append(res.to_dict())
        if res.action in {"enhanced", "needs-review", "dry-run"}:
            ok_count += 1
            print(f"[{i}/{len(files)}] {res.action.upper()}: {file_path.name} -> {res.reason} ({item_elapsed:.2f}s)")
        else:
            fail_count += 1
            print(f"[{i}/{len(files)}] FAILED: {file_path.name} -> {res.reason}", file=sys.stderr)

    total_elapsed = time.time() - t0
    print("-" * 60)
    print(f"Completed {len(files)} files in {total_elapsed:.2f}s ({total_elapsed / max(1, len(files)):.2f}s/img)")
    print(f"Success: {ok_count} | Failed: {fail_count}")

    # Write completion manifest
    manifest_path = out_root / "_enhance_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Saved manifest to {manifest_path}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

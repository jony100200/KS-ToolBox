"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Verifies the pure sizing math for all four modes, then runs the full pipeline
on a real image and asserts the saved file has the computed dimensions. Needs
only Pillow for the file leg; the math leg needs nothing. Skips cleanly.

Run standalone:  python -m tools.image_rescale.test_smoke
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.image_rescale import engine as e  # noqa: E402


def test_sizing_math() -> None:
    # 2000x1000 source, downscale-only (allow_upscale defaults False).
    o = e.ResizeOptions
    assert e.compute_size("longest_side", 2000, 1000, o(longest_side=1000)) == (1000, 500)
    assert e.compute_size("scale_factor", 2000, 1000, o(scale_factor=0.5)) == (1000, 500)
    assert e.compute_size("fit_inside", 2000, 1000, o(fit_w=800, fit_h=800)) == (800, 400)
    # max_mp: 1 MP target from 2 MP source -> factor ~0.72 -> ~1448x724
    w, h = e.compute_size("max_mp", 2000, 1000, o(max_mp=1.0))
    assert 1400 <= w <= 1500 and 700 <= h <= 760, f"max_mp off: {w}x{h}"
    # upscale gating: never enlarge unless allowed.
    assert e.compute_size("longest_side", 500, 500, o(longest_side=2000)) == (500, 500)
    assert e.compute_size("longest_side", 500, 500, o(longest_side=2000, allow_upscale=True)) == (2000, 2000)
    # snap: round down to a multiple.
    assert e.compute_size("scale_factor", 1000, 1000, o(scale_factor=0.53, snap=64)) == (512, 512)
    print("PASS: image_rescale sizing math — all four modes + upscale gating + snap.")


def test_full_pipeline() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — full-pipeline leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "big.png"
        Image.new("RGB", (1600, 800), (120, 60, 200)).save(src)
        out = tmp / "out"
        res = e.process(src, e.ResizeOptions(out_root=out, mode="longest_side",
                                             longest_side=800, dry_run=False))
        assert res.action == "resized", f"expected resized, got {res.action}: {res.reason}"
        with Image.open(res.out_path) as got:     # close the handle so tempdir cleanup works on Windows
            assert got.size == (800, 400), f"saved size wrong: {got.size}"
    print(f"PASS: image_rescale full pipeline — {res.before} -> {res.after}.")


def test_optional_realesrgan_pipeline() -> None:
    """Exercise the portable AI bundle only when the local bundle is present."""
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — AI pipeline leg skipped.")
        return
    if os.environ.get("KS_TOOLBOX_SKIP_GPU"):
        print("SKIP: KS_TOOLBOX_SKIP_GPU set — AI pipeline leg skipped.")
        return
    status = e.ai_runtime_status()
    if not status["ready"]:
        print("SKIP: Real-ESRGAN NCNN bundle not installed — AI pipeline leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "small.png"
        Image.new("RGB", (48, 24), (80, 100, 160)).save(src)
        res = e.process(src, e.ResizeOptions(
            out_root=tmp / "out", mode="scale_factor", scale_factor=2.0,
            allow_upscale=True, ai_upscale=True, dry_run=False,
        ))
        assert res.action == "resized", f"expected AI resize, got {res.action}: {res.reason}"
        assert res.detail == "ai.realesrgan.realesrgan-x4plus", res.detail
        with Image.open(res.out_path) as got:
            assert got.size == (96, 48), f"AI output size wrong: {got.size}"
    print("PASS: image_rescale Real-ESRGAN NCNN 2× pipeline.")


def test_durable_batch() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — durable-batch leg skipped.")
        return
    from PIL import Image
    from toolbox.batch_core import BatchRunner, ItemOutcome, ItemState, JobDefinition, JobState
    from toolbox.sqlite_job_store import SQLiteJobStore

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        good_a, bad, good_b = tmp / "a.png", tmp / "bad.png", tmp / "b.png"
        Image.new("RGB", (64, 32), (10, 20, 30)).save(good_a)
        bad.write_bytes(b"not an image")
        Image.new("RGB", (32, 64), (30, 20, 10)).save(good_b)
        opts = e.ResizeOptions(out_root=tmp / "out", mode="longest_side",
                               longest_side=16, dry_run=False)
        job = JobDefinition.create(
            tool_id="image_rescale",
            tool_version="1",
            workflow_version="resize.v1",
            inputs=[good_a, bad, good_b],
            settings={"mode": "longest_side", "longest_side": 16},
        )

        def classify(result: e.Result) -> ItemOutcome:
            if result.action == "resized":
                return ItemOutcome.completed(result.to_dict(), result.reason)
            if result.action in ("skipped", "dry-run"):
                return ItemOutcome.skipped(result.to_dict(), result.reason)
            return ItemOutcome.failed(result.reason, data=result.to_dict())

        db = tmp / "jobs.sqlite3"
        with SQLiteJobStore(db) as store:
            report = BatchRunner(store).run(job, lambda path: e.process(path, opts), classify)
        assert report.state is JobState.COMPLETED_WITH_WARNINGS
        assert report.counts[ItemState.COMPLETED.value] == 2
        assert report.counts[ItemState.QUARANTINED.value] == 1
        assert (tmp / "out" / "a.png").is_file() and (tmp / "out" / "b.png").is_file()

        with SQLiteJobStore(db) as store:
            reused = BatchRunner(store).run(
                job,
                lambda path: (_ for _ in ()).throw(AssertionError(f"reprocessed {path}")),
                classify,
                validate_stored=lambda item: e.validate_result(e.Result(**item.data)),
            )
        assert reused.reused, "completed job was not reused"

        (tmp / "out" / "a.png").unlink()
        rerun: list[str] = []
        with SQLiteJobStore(db) as store:
            repaired = BatchRunner(store).run(
                job,
                lambda path: rerun.append(path.name) or e.process(path, opts),
                classify,
                validate_stored=lambda item: e.validate_result(e.Result(**item.data)),
            )
        assert repaired.recovered and rerun == ["a.png"], f"unexpected repair set: {rerun}"
        assert (tmp / "out" / "a.png").is_file(), "missing output was not repaired"
    print("PASS: image_rescale durable batch — isolated bad input, checkpointed, reused.")


def _psnr(a, b) -> float:
    import numpy as np
    x = np.asarray(a.convert("RGB"), np.float64); y = np.asarray(b.convert("RGB"), np.float64)
    mse = float(np.mean((x - y) ** 2))
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


def _gradient(w: int, h: int):
    import numpy as np
    from PIL import Image
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([(xx * 255 // max(1, w - 1)), (yy * 255 // max(1, h - 1)),
                    ((xx + yy) * 3) % 256], axis=-1).astype(np.uint8)
    return Image.fromarray(rgb)


def test_jpeg_keeps_icc_orientation_and_quality() -> None:
    """Regression (2026-11-12 audit): ICC dropped, sideways phone photos, quality 75."""
    if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("numpy") is None:
        print("SKIP: Pillow/numpy not installed — JPEG leg skipped.")
        return
    from PIL import Image, ImageCms
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "phone.jpg"
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        exif = Image.Exif(); exif[0x0112] = 6            # stored landscape, shown portrait
        _gradient(400, 200).save(src, quality=95, icc_profile=icc, exif=exif.tobytes())
        res = e.process(src, e.ResizeOptions(out_root=tmp / "out", mode="longest_side",
                                             longest_side=200, dry_run=False))
        assert res.action == "resized", res
        with Image.open(res.out_path) as got:
            assert got.size == (100, 200), f"orientation not applied: {got.size}"
            assert got.info.get("icc_profile") == icc, "ICC profile dropped"
            assert got.quantization[0][0] <= 4, f"low JPEG quality: luma q0={got.quantization[0][0]}"
        res2 = e.process(src, e.ResizeOptions(out_root=tmp / "q50", mode="longest_side",
                                              longest_side=200, quality=50, dry_run=False))
        with Image.open(res2.out_path) as got2:
            assert got2.quantization[0][0] > 8, "user quality not applied"
    print("PASS: image_rescale JPEG — upright, ICC kept, quality 92 default and settable.")


def test_upscale_and_palette_quality() -> None:
    """Regression: auto upscale was bilinear; palette PNGs were resized nearest-neighbour."""
    if importlib.util.find_spec("PIL") is None or importlib.util.find_spec("numpy") is None:
        print("SKIP: Pillow/numpy not installed — quality leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        big = _gradient(256, 128)
        big.resize((128, 64), Image.LANCZOS).save(tmp / "small.png")
        res = e.process(tmp / "small.png", e.ResizeOptions(out_root=tmp / "up", mode="scale_factor",
                        scale_factor=2.0, allow_upscale=True, dry_run=False))
        with Image.open(res.out_path) as up:
            ref = Image.open(tmp / "small.png").resize((256, 128), Image.LANCZOS)
            assert _psnr(up, ref) >= 60, "auto upscale is not Lanczos"
        pal = big.convert("P", palette=Image.ADAPTIVE, colors=64)
        pal.save(tmp / "pal.png")
        res = e.process(tmp / "pal.png", e.ResizeOptions(out_root=tmp / "pal", mode="scale_factor",
                        scale_factor=0.5, dry_run=False))
        with Image.open(res.out_path) as po:
            assert po.mode == "RGB", po.mode
            ref = pal.convert("RGB").resize(po.size, Image.LANCZOS)
            assert _psnr(po, ref) >= 60, "palette PNG not filtered in RGB"
        res = e.process(tmp / "pal.png", e.ResizeOptions(out_root=tmp / "near", mode="scale_factor",
                        scale_factor=0.5, resample="nearest", dry_run=False))
        with Image.open(res.out_path) as pn:
            assert pn.mode == "P", "nearest should keep the exact palette"
    print("PASS: image_rescale quality — Lanczos upscale, palette PNG filtered in RGB, nearest keeps P.")


def main() -> int:
    test_sizing_math()
    test_full_pipeline()
    test_jpeg_keeps_icc_orientation_and_quality()
    test_upscale_and_palette_quality()
    test_optional_realesrgan_pipeline()
    test_durable_batch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

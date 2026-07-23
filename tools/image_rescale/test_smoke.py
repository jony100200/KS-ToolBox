"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Verifies the pure sizing math for all four modes, then runs the full pipeline
on a real image and asserts the saved file has the computed dimensions. Needs
only Pillow for the file leg; the math leg needs nothing. Skips cleanly.

Run standalone:  python -m tools.image_rescale.test_smoke
"""
from __future__ import annotations

import importlib.util
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


def main() -> int:
    test_sizing_math()
    test_full_pipeline()
    test_optional_realesrgan_pipeline()
    test_durable_batch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

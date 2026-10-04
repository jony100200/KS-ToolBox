"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Covers the three properties that make this tool trustworthy on an irreplaceable
library: metadata is actually detected, the delivered copy is actually clean, and
the master is never touched. Also asserts pixels survive byte-for-byte, since a
scrubber that silently re-compresses your art would be worse than useless.

Run standalone:  python -m tools.metadata_scrubber.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.metadata_scrubber import engine as e  # noqa: E402


def _make_png_with_recipe(path: Path):
    """A PNG carrying a ComfyUI-style graph + an sd.cpp-style parameters block."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo()
    meta.add_text("prompt", '{"1":{"class_type":"UnetLoaderGGUF","inputs":{"unet_name":"secret.gguf"}}}')
    meta.add_text("parameters", "a secret prompt, Steps: 6, Seed: 1000, Model: secret_model")
    meta.add_text("workflow", '{"nodes":[{"type":"KSampler"}]}')
    img = Image.new("RGB", (64, 48), (30, 120, 90))
    img.save(path, pnginfo=meta)
    return img


def test_detects_metadata() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — detection leg skipped.")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        dirty, clean = tmp / "dirty.png", tmp / "clean.png"
        _make_png_with_recipe(dirty)
        Image.new("RGB", (8, 8), (1, 2, 3)).save(clean)
        found = e.inspect(dirty)
        assert "prompt" in found and "parameters" in found and "workflow" in found, found
        assert e.inspect(clean) == [], f"false positive on a clean file: {e.inspect(clean)}"
    print("PASS: metadata_scrubber detection — finds prompt/workflow/parameters, no false positives.")


def test_scrub_is_clean_lossless_and_nondestructive() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — scrub leg skipped.")
        return
    from PIL import Image, ImageChops
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "art.png"
        original = _make_png_with_recipe(src).copy()
        out = tmp / "deliver"

        res = e.process(src, e.ScrubOptions(out_root=out, dry_run=False))
        assert res.action == "scrubbed", f"expected scrubbed, got {res.action}: {res.reason}"

        # 1. delivered copy carries nothing
        assert e.inspect(res.out_path) == [], f"delivered file still leaks: {e.inspect(res.out_path)}"

        # 2. master untouched — the copy-only guarantee
        assert e.inspect(src), "MASTER WAS MODIFIED — copy-only contract broken"

        # 3. pixels identical
        with Image.open(res.out_path) as got:
            assert ImageChops.difference(original, got.convert("RGB")).getbbox() is None, \
                "pixels changed during scrub"
    print("PASS: metadata_scrubber scrub — delivered clean, master intact, pixels identical.")


def test_refuses_to_overwrite_source() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — guard leg skipped.")
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "art.png"
        _make_png_with_recipe(src)
        # out_root == the source's own folder puts the output exactly on the master.
        res = e.process(src, e.ScrubOptions(out_root=tmp, dry_run=False))
        assert res.action == "failed" and res.detail == "path.inplace", \
            f"in-place write was not refused: {res.action}/{res.detail}"
        assert e.inspect(src), "master lost its metadata despite the refusal"
    print("PASS: metadata_scrubber guard — refuses to write over the master.")


def test_dry_run_writes_nothing() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — dry-run leg skipped.")
        return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "art.png"
        _make_png_with_recipe(src)
        out = tmp / "deliver"
        res = e.process(src, e.ScrubOptions(out_root=out, dry_run=True))
        assert res.action == "dry-run", res.action
        assert "prompt" in res.found, res.found
        assert not out.exists(), "dry run created output"
    print("PASS: metadata_scrubber dry run — reports the leak, writes nothing.")


def test_durable_batch() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — durable-batch leg skipped.")
        return
    from toolbox.batch_core import BatchRunner, ItemOutcome, ItemState, JobDefinition, JobState
    from toolbox.sqlite_job_store import SQLiteJobStore

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src_dir = tmp / "masters"
        src_dir.mkdir()
        good_a, bad, good_b = src_dir / "a.png", src_dir / "bad.png", src_dir / "b.png"
        _make_png_with_recipe(good_a)
        bad.write_bytes(b"not an image")
        _make_png_with_recipe(good_b)
        opts = e.ScrubOptions(out_root=tmp / "deliver", dry_run=False)
        job = JobDefinition.create(
            tool_id="metadata_scrubber", tool_version="1", workflow_version="scrub.v1",
            inputs=[good_a, bad, good_b], settings=opts.to_dict(),
        )

        def classify(res: e.Result) -> ItemOutcome:
            if res.action in ("scrubbed", "already-clean", "copied"):
                return ItemOutcome.completed(res.to_dict(), res.reason)
            if res.action in ("skipped", "dry-run"):
                return ItemOutcome.skipped(res.to_dict(), res.reason)
            return ItemOutcome.failed(res.reason, data=res.to_dict())

        db = tmp / "jobs.sqlite3"
        with SQLiteJobStore(db) as store:
            report = BatchRunner(store).run(job, lambda p: e.process(p, opts), classify)
        assert report.state is JobState.COMPLETED_WITH_WARNINGS, report.state
        assert report.counts[ItemState.COMPLETED.value] == 2, report.counts
        assert report.counts[ItemState.QUARANTINED.value] == 1, report.counts

        # A deleted delivery file must be re-made, not silently reported as done.
        (tmp / "deliver" / "a.png").unlink()
        rerun: list[str] = []
        with SQLiteJobStore(db) as store:
            repaired = BatchRunner(store).run(
                job, lambda p: rerun.append(p.name) or e.process(p, opts), classify,
                validate_stored=lambda item: e.validate_result(e.Result(**item.data)),
            )
        assert repaired.recovered and rerun == ["a.png"], f"unexpected repair set: {rerun}"
        assert (tmp / "deliver" / "a.png").is_file(), "missing delivery file was not repaired"
    print("PASS: metadata_scrubber durable batch — bad input isolated, missing output repaired.")


def main() -> int:
    test_detects_metadata()
    test_scrub_is_clean_lossless_and_nondestructive()
    test_refuses_to_overwrite_source()
    test_dry_run_writes_nothing()
    test_durable_batch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

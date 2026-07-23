"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Two legs:
  * PURE — pairing classification, resolution bucketing, deterministic split, and
    literal/regex replace. Needs nothing but the engine; runs everywhere.
  * FULL — a real temp folder of PNGs + sidecar captions (one image missing its
    caption, one orphan caption) is run through pair_report, split and bucket.
    Asserts copies land under the output root, the SOURCES ARE UNCHANGED, and the
    manifest is non-empty. Needs Pillow (skips cleanly without it).

Run standalone:  python -m tools.dataset_manager.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.dataset_manager import engine as e  # noqa: E402


def test_pairing() -> None:
    d = "/ds"
    paths = [
        Path(d) / "a.png", Path(d) / "a.txt",       # pair
        Path(d) / "b.png", Path(d) / "b.caption",   # pair (alt ext)
        Path(d) / "c.png",                          # missing caption
        Path(d) / "z.txt",                          # orphan caption
    ]
    res = e.pair_files(paths, (".txt", ".caption"))
    assert len(res["pairs"]) == 2, res["pairs"]
    assert [p.name for p in res["missing"]] == ["c.png"], res["missing"]
    assert [p.name for p in res["orphans"]] == ["z.txt"], res["orphans"]
    print("PASS: pair_files — pairs / missing / orphans classified.")


def test_bucket() -> None:
    assert e.bucket_for(512, 512, "dimensions") == "512x512"
    assert e.bucket_for(512, 512, "aspect") == "square"
    assert e.bucket_for(768, 512, "aspect") == "landscape"
    assert e.bucket_for(512, 768, "aspect") == "portrait"
    assert e.bucket_for(None, None, "dimensions") == "unknown"
    print("PASS: bucket_for — dimensions + aspect + unknown.")


def test_split() -> None:
    names = [f"img{i:02d}.png" for i in range(10)]      # already sorted
    assign = e.split_assign(names, (0.8, 0.1, 0.1))
    counts = {s: sum(1 for v in assign.values() if v == s) for s in e.SPLITS}
    assert counts == {"train": 8, "val": 1, "test": 1}, counts
    # deterministic: identical result on a repeat run
    assert e.split_assign(names, (0.8, 0.1, 0.1)) == assign
    # each image keeps a single split (no leakage)
    assert set(assign.values()) <= set(e.SPLITS)
    print(f"PASS: split_assign — 80/10/10 over 10 -> {counts} (reproducible).")


def test_replace() -> None:
    assert e.apply_replace("a cat, a cat", "cat", "dog") == "a dog, a dog"
    assert e.apply_replace("frame_001 frame_002", r"frame_\d+", "F", regex=True) == "F F"
    assert e.apply_replace("no change", "", "x") == "no change"
    assert e._count_replacements("a cat, a cat", "cat", False) == 2
    print("PASS: apply_replace — literal + regex + no-op.")


def _make_dataset(root: Path) -> None:
    from PIL import Image
    Image.new("RGB", (512, 512), (200, 60, 60)).save(root / "img01.png")   # square, has caption
    Image.new("RGB", (768, 512), (60, 200, 60)).save(root / "img02.png")   # landscape, has caption
    Image.new("RGB", (512, 768), (60, 60, 200)).save(root / "img03.png")   # portrait, MISSING caption
    (root / "img01.txt").write_text("a red square, cat", encoding="utf-8")
    (root / "img02.caption").write_text("a green banner", encoding="utf-8")
    (root / "orphan.txt").write_text("no image here", encoding="utf-8")    # orphan caption


def test_full_pipeline() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — full-pipeline leg skipped.")
        return
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"; src.mkdir()
        _make_dataset(src)
        images = sorted(p for p in src.iterdir() if p.suffix.lower() in e.IMAGE_EXTS)
        # snapshot source bytes to prove non-destructiveness later
        before = {p: p.read_bytes() for p in src.iterdir() if p.is_file()}

        # --- pair_report (writes manifest + pair report) ---
        rep = e.run(images, e.DatasetOptions(operation="pair_report",
                                             out_root=Path(td) / "report", dry_run=False))
        assert rep.pairs == 2 and rep.missing == 1 and rep.orphans == 1, (rep.pairs, rep.missing, rep.orphans)
        assert rep.manifest and Path(rep.manifest["csv"]).stat().st_size > 0, "manifest empty"
        assert rep.pair_report and Path(rep.pair_report["json"]).is_file(), "pair report missing"

        # --- split (copies image+caption pairs into train/val/test) ---
        split_out = Path(td) / "split"
        rep = e.run(images, e.DatasetOptions(operation="split", out_root=split_out,
                                             ratios=(0.8, 0.1, 0.1), dry_run=False))
        # 3 images -> 2 train / 1 val / 0 test by rounded thresholds; every image lands somewhere
        copied = list(split_out.rglob("*.png"))
        assert len(copied) == 3, f"expected 3 copied images, got {len(copied)}"
        assert (split_out / "train").is_dir(), "no train folder"
        # a paired caption travels with its image
        train_caps = list((split_out / "train").glob("*.txt")) + list((split_out / "train").glob("*.caption"))
        assert train_caps, "caption did not travel with its image into the split"

        # --- bucket (aspect subfolders) ---
        bucket_out = Path(td) / "bucket"
        rep = e.run(images, e.DatasetOptions(operation="bucket", bucket_mode="aspect",
                                             out_root=bucket_out, dry_run=False))
        assert (bucket_out / "square" / "img01.png").is_file(), "square bucket wrong"
        assert (bucket_out / "landscape" / "img02.png").is_file(), "landscape bucket wrong"
        assert (bucket_out / "portrait" / "img03.png").is_file(), "portrait bucket wrong"

        # --- sources UNCHANGED after every copy op ---
        after = {p: p.read_bytes() for p in src.iterdir() if p.is_file()}
        assert set(before) == set(after), "a source file was added/removed"
        assert all(before[p] == after[p] for p in before), "a source file's bytes changed"

        # --- dry-run replace changes nothing, still counts ---
        rep = e.run(images, e.DatasetOptions(operation="replace", find="cat", replace="dog",
                                             out_root=Path(td) / "repl", dry_run=True))
        assert rep.changed == 1, f"expected 1 replacement counted, got {rep.changed}"
        assert not (Path(td) / "repl").exists(), "dry-run wrote files"

        # A mistaken same-folder destination must fail before touching any source.
        guarded_before = {path: path.read_bytes() for path in src.iterdir() if path.is_file()}
        rep = e.run(
            images,
            e.DatasetOptions(
                operation="replace", find="cat", replace="dog",
                out_root=src, dry_run=False,
            ),
        )
        assert rep.failed == 1 and "must not be a source folder" in rep.messages[0]
        guarded_after = {path: path.read_bytes() for path in src.iterdir() if path.is_file()}
        assert guarded_before == guarded_after, "same-folder output modified a source"

        # Two source trees with the same destination name must be rejected as a
        # complete plan before the first copy, never silently last-writer-wins.
        left = Path(td) / "left"; right = Path(td) / "right"
        left.mkdir(); right.mkdir()
        from PIL import Image
        left_image = left / "same.png"; right_image = right / "same.png"
        Image.new("RGB", (16, 16), "red").save(left_image)
        Image.new("RGB", (16, 16), "blue").save(right_image)
        collision_out = Path(td) / "collision"
        collision = e.run(
            [left_image, right_image],
            e.DatasetOptions(
                operation="split", ratios=(1, 0, 0),
                out_root=collision_out, dry_run=False,
            ),
        )
        assert collision.failed and any("destination collision" in m for m in collision.messages)
        assert not collision_out.exists(), "collision plan wrote partial output"

    print("PASS: full pipeline — pair_report + split + bucket copied; SOURCES UNCHANGED; manifest non-empty.")


def main() -> int:
    test_pairing()
    test_bucket()
    test_split()
    test_replace()
    test_full_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

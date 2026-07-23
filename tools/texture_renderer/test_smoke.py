"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Substance Designer and Material Maker are NOT installed on CI (and aren't
bundled — they're user-provided external CLIs), so we cannot exercise a real
render. Instead we prove the entire PURE surface + the post-processing that this
tool actually owns:

  * find_projects        — flat + recursive discovery of .sbsar/.ptex
  * get_log2_res         — resolution label → Substance log2 pair
  * substance_cmd /
    material_maker_cmd   — exact argv structure
  * validate_engine      — reject missing / wrong-name, ACCEPT a correctly-named
                           stub (with or without .exe — cross-platform)
  * cleanup_and_resize   — remove non-PNG junk, resize a real PNG, honest counts

The external-engine invocation itself is a thin `run_cmd` wrapper (render_*),
not exercised here — there is no Substance/Material Maker binary on CI. The
dry-run path of render_* IS exercised (it builds the command without shelling
out).

Run standalone:  python -m tools.texture_renderer.test_smoke
Skips cleanly (prints SKIP, returns 0) if Pillow is not installed.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

# Allow standalone `python test_smoke.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.texture_renderer import engine as e  # noqa: E402


def _test_find_projects(tmp: Path) -> None:
    # flat: two .sbsar at the top level
    (tmp / "a.sbsar").write_text("x")
    (tmp / "b.sbsar").write_text("x")
    # nested: one more .sbsar + a .ptex, in a subfolder
    sub = tmp / "nested"; sub.mkdir()
    (sub / "c.sbsar").write_text("x")
    (sub / "mat.ptex").write_text("x")

    flat = e.find_projects(tmp, ".sbsar", recursive=False)
    assert len(flat) == 2, f"flat sbsar: expected 2, got {len(flat)}"
    rec = e.find_projects(tmp, ".sbsar", recursive=True)
    assert len(rec) == 3, f"recursive sbsar: expected 3, got {len(rec)}"
    ptex = e.find_projects(tmp, "ptex", recursive=True)   # bare ext (no dot) accepted
    assert len(ptex) == 1, f"ptex: expected 1, got {len(ptex)}"
    assert e.find_projects(tmp / "does-not-exist", ".sbsar", True) == [], "missing dir must yield []"


def _test_log2_res() -> None:
    assert e.get_log2_res("512x512") == "9,9"
    assert e.get_log2_res("1024x1024") == "10,10"
    assert e.get_log2_res("2048x2048") == "11,11"
    assert e.get_log2_res("4096x4096") == "12,12"
    assert e.get_log2_res("bogus") == "10,10", "unknown label must fall back to 1024²"


def _test_cmds() -> None:
    sbs = e.substance_cmd("sbsrender.exe", "in.sbsar", "out", "10,10")
    assert sbs[0] == "sbsrender.exe" and sbs[1] == "render"
    assert "--inputs" in sbs and "in.sbsar" in sbs
    assert "--output-path" in sbs and "out" in sbs
    assert sbs[sbs.index("--set-value") + 1] == "$outputsize@10,10"

    mm = e.material_maker_cmd("material_maker", "proj.ptex", "out", "Unreal")
    assert mm[0] == "material_maker"
    assert "--export-material" in mm
    assert mm[mm.index("--target") + 1] == "Unreal"
    assert mm[mm.index("-o") + 1] == "out"
    assert mm[-1] == "proj.ptex"


def _test_validate_engine(tmp: Path) -> None:
    # missing path
    assert e.validate_engine("", "sbsrender")["error"]
    assert e.validate_engine(tmp / "nope.exe", "sbsrender")["error"]
    # wrong name
    wrong = tmp / "designer.exe"; wrong.write_text("x")
    r = e.validate_engine(wrong, "sbsrender")
    assert r["error"] and r["error_type"] == "engine.wrong", f"wrong-name not rejected: {r}"
    # correctly-named stub — .exe on Windows, bare name elsewhere (cross-platform)
    name = "sbsrender.exe" if os.name == "nt" else "sbsrender"
    good = tmp / name; good.write_text("x")
    r = e.validate_engine(good, "sbsrender")
    assert not r["error"], f"correctly-named engine rejected: {r}"


def _test_cleanup_and_resize(tmp: Path) -> None:
    from PIL import Image

    out = tmp / "mm_out"; out.mkdir()
    Image.new("RGB", (512, 512), (128, 64, 32)).save(out / "albedo.png")
    (out / "material.tres").write_text("junk")   # non-PNG engine sidecar

    res = e.cleanup_and_resize(out, resize_to=256)
    assert not res["error"], res["details"]
    counts = res["data"]
    assert counts["cleaned"] >= 1, f"junk not removed: {counts}"
    assert counts["resized"] >= 1, f"png not resized: {counts}"
    assert counts["errors"] == 0, f"unexpected errors: {counts}"
    assert not (out / "material.tres").exists(), "non-PNG junk should be deleted"
    # read back the resized PNG — `with` so Windows can clean the tempdir
    with Image.open(out / "albedo.png") as img:
        assert img.size == (256, 256), f"expected 256x256, got {img.size}"

    # missing directory → clean error envelope, not a crash
    assert e.cleanup_and_resize(tmp / "nope", resize_to=None)["error"]


def _test_dry_run(tmp: Path) -> None:
    """render_* dry-run builds the command without shelling out to a real engine."""
    eng = tmp / ("sbsrender.exe" if os.name == "nt" else "sbsrender"); eng.write_text("x")
    opts = e.RenderOptions(engine_path=str(eng), input_dir=str(tmp),
                           output_dir=str(tmp / "out"), resolution="2048x2048", dry_run=True)
    r = e.render_substance(tmp / "thing.sbsar", opts)
    assert r.action == "dry-run", f"expected dry-run, got {r.action}: {r.reason}"
    assert "$outputsize@11,11" in r.reason, f"planned cmd missing res: {r.reason}"
    assert not (tmp / "out").exists(), "dry-run must not create the output dir"


def _test_isolated_publish(tmp: Path) -> None:
    from PIL import Image

    mm_engine = tmp / ("material_maker.exe" if os.name == "nt" else "material_maker")
    sbs_engine = tmp / ("sbsrender.exe" if os.name == "nt" else "sbsrender")
    mm_engine.write_text("stub")
    sbs_engine.write_text("stub")
    mm_project = tmp / "material.ptex"
    sbs_project = tmp / "archive.sbsar"
    mm_project.write_text("project")
    sbs_project.write_text("archive")

    output_root = tmp / "output"
    mm_destination = output_root / mm_project.stem
    mm_destination.mkdir(parents=True)
    keep_text = mm_destination / "KEEP_LICENSE.txt"
    keep_text.write_text("must survive")
    keep_png = mm_destination / "existing.png"
    Image.new("RGB", (32, 16), (10, 20, 30)).save(keep_png)
    keep_png_bytes = keep_png.read_bytes()

    original_run = e._run

    def successful_mm(command):
        stage = Path(command[command.index("-o") + 1])
        assert ".ks-render-stage" in stage.parts
        Image.new("RGB", (24, 12), (100, 60, 20)).save(stage / "generated.png")
        (stage / "generated.tres").write_text("owned sidecar")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    e._run = successful_mm
    try:
        result = e.render_material_maker(
            mm_project,
            e.RenderOptions(
                engine_path=str(mm_engine), input_dir=str(tmp),
                output_dir=str(output_root), group=True, resize=64,
            ),
        )
    finally:
        e._run = original_run
    assert result.action == "rendered", result.reason
    assert keep_text.read_text() == "must survive"
    assert keep_png.read_bytes() == keep_png_bytes
    assert (mm_destination / "generated.png").is_file()
    assert not (mm_destination / "generated.tres").exists()
    with Image.open(mm_destination / "generated.png") as generated:
        assert generated.size == (64, 64)
    assert not (output_root / ".ks-render-stage").exists()

    failed_project = tmp / "failed.ptex"
    failed_project.write_text("project")

    def failed_mm(command):
        stage = Path(command[command.index("-o") + 1])
        Image.new("RGB", (8, 8), (255, 0, 0)).save(stage / "partial.png")
        return SimpleNamespace(returncode=7, stdout="", stderr="render failed")

    e._run = failed_mm
    try:
        failed = e.render_material_maker(
            failed_project,
            e.RenderOptions(
                engine_path=str(mm_engine), input_dir=str(tmp),
                output_dir=str(output_root), group=True,
            ),
        )
    finally:
        e._run = original_run
    assert failed.action == "failed"
    assert not (output_root / failed_project.stem).exists()
    assert not (output_root / ".ks-render-stage").exists()

    def successful_sbs(command):
        stage = Path(command[command.index("--output-path") + 1])
        (stage / "archive_basecolor.tga").write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    e._run = successful_sbs
    try:
        substance = e.render_substance(
            sbs_project,
            e.RenderOptions(
                engine_path=str(sbs_engine), input_dir=str(tmp),
                output_dir=str(output_root), group=True,
            ),
        )
    finally:
        e._run = original_run
    assert substance.action == "rendered"
    assert (output_root / sbs_project.stem / "archive_basecolor.tga").is_file()
    assert not (output_root / ".ks-render-stage").exists()

    unowned_project = tmp / "unowned.ptex"
    unowned_project.write_text("project")
    unowned_opts = e.RenderOptions(
        engine_path=str(mm_engine), input_dir=str(tmp),
        output_dir=str(output_root), group=True,
    )
    unowned_stage = e._stage_for(unowned_project, unowned_opts)
    unowned_stage.mkdir(parents=True)
    sentinel = unowned_stage / "user-file.txt"
    sentinel.write_text("not owned by KS")
    called = False

    def must_not_run(_command):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    e._run = must_not_run
    try:
        refused = e.render_material_maker(unowned_project, unowned_opts)
    finally:
        e._run = original_run
    assert refused.action == "failed" and refused.detail == "output.stage"
    assert not called and sentinel.read_text() == "not owned by KS"

    protected_root = tmp / "protected"
    protected_root.mkdir()
    protected_project = protected_root / "source.sbsar"
    protected_project.write_bytes(b"original source bytes")
    protected_before = protected_project.read_bytes()

    def overwrite_source(command):
        stage = Path(command[command.index("--output-path") + 1])
        (stage / protected_project.name).write_bytes(b"replacement")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    e._run = overwrite_source
    try:
        protected_result = e.render_substance(
            protected_project,
            e.RenderOptions(
                engine_path=str(sbs_engine), input_dir=str(protected_root),
                output_dir=str(protected_root), group=False,
            ),
        )
    finally:
        e._run = original_run
    assert protected_result.action == "failed"
    assert protected_project.read_bytes() == protected_before
    assert not (protected_root / ".ks-render-stage").exists()

    rollback_stage = tmp / "rollback_stage"
    (rollback_stage / "sub").mkdir(parents=True)
    (rollback_stage / "a.png").write_bytes(b"new first output")
    (rollback_stage / "sub" / "b.png").write_bytes(b"new second output")
    rollback_dest = tmp / "rollback_dest"
    rollback_dest.mkdir()
    existing = rollback_dest / "a.png"
    existing.write_bytes(b"original first output")
    (rollback_dest / "sub").write_bytes(b"blocks second output directory")
    try:
        e._publish_stage(
            rollback_stage,
            rollback_dest,
            [rollback_stage / "a.png", rollback_stage / "sub" / "b.png"],
        )
    except OSError:
        pass
    else:
        raise AssertionError("publication failure did not propagate")
    assert existing.read_bytes() == b"original first output"
    assert not (rollback_dest / "sub" / "b.png").exists()


def main() -> int:
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("SKIP: Pillow not installed — cannot run cleanup/resize checks.")
        return 0

    # Each helper gets a fresh subdir to avoid cross-contamination.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, fn in (("find", _test_find_projects), ("validate", _test_validate_engine),
                         ("cleanup", _test_cleanup_and_resize), ("dry", _test_dry_run),
                         ("publish", _test_isolated_publish)):
            sub = root / name; sub.mkdir()
            fn(sub)
        _test_log2_res()
        _test_cmds()

    print("PASS: texture_renderer — discovery, commands, isolated publish, "
          "source/stage guards, cleanup/resize, and dry-run verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

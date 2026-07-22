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
                         ("cleanup", _test_cleanup_and_resize), ("dry", _test_dry_run)):
            sub = root / name; sub.mkdir()
            fn(sub)
        _test_log2_res()
        _test_cmds()

    print("PASS: texture_renderer — discovery, cmd-build, engine validation, "
          "cleanup/resize, and dry-run all verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

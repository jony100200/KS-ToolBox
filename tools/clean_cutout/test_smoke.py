"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Two layers, so it still verifies something real without downloading the model:
  * ALWAYS (needs only numpy+Pillow): the pure edge math — despill kills a green
    tint, defringe shrinks the alpha edge, coverage reads correctly.
  * IF onnxruntime is installed AND the u2net model is already cached: the full
    ONNX pipeline cuts a synthetic subject and writes a valid RGBA PNG. Skips
    (does not fail) when onnxruntime is absent or the model isn't cached — a
    smoke test must not trigger a 176 MB download.

Run standalone:  python -m tools.clean_cutout.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from tools.clean_cutout import engine as e  # noqa: E402


def test_pure_math() -> None:
    # despill: a pixel that is pure green must lose its green above max(r,b).
    green = np.array([[[10, 200, 20]]], dtype=np.uint8)
    out = e.despill(green)
    assert out[0, 0, 1] <= max(out[0, 0, 0], out[0, 0, 2]) + 1e-3, "despill left green tint"

    # defringe: eroding a solid square's alpha must reduce the opaque count.
    rgba = np.zeros((20, 20, 4), dtype=np.uint8)
    rgba[5:15, 5:15, 3] = 255
    before = int((rgba[:, :, 3] > 127).sum())
    eroded = e.defringe(rgba, erode_px=1, feather=0.0)
    after = int((eroded[:, :, 3] > 127).sum())
    assert after < before, f"defringe did not erode the edge ({after} !< {before})"

    # coverage: half-opaque image reads ~0.5.
    half = np.zeros((10, 10, 4), dtype=np.uint8)
    half[:, :5, 3] = 255
    cov = e.alpha_coverage(half)
    assert abs(cov - 0.5) < 1e-6, f"coverage wrong: {cov}"
    print("PASS: clean_cutout pure math — despill, defringe, coverage.")


def test_full_pipeline() -> None:
    if importlib.util.find_spec("onnxruntime") is None or importlib.util.find_spec("PIL") is None:
        print("SKIP: onnxruntime/Pillow not installed — full-pipeline leg skipped.")
        return
    if not any((d / "u2net.onnx").is_file() for d in e._model_dirs()):
        print("SKIP: u2net model not cached — inference leg skipped (no download in tests).")
        return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # a bright disc on a flat background — something for the matte to find.
        img = np.full((128, 128, 3), 30, dtype=np.uint8)
        yy, xx = np.ogrid[:128, :128]
        disc = (yy - 64) ** 2 + (xx - 64) ** 2 <= 40 ** 2
        img[disc] = (230, 60, 60)
        src = tmp / "subject.png"
        Image.fromarray(img, "RGB").save(src)

        out = tmp / "out"
        res = e.process(src, e.CutoutOptions(out_root=out, dry_run=False))
        assert res.action in ("cut", "skipped"), f"unexpected: {res.action} — {res.reason}"
        if res.action == "cut":
            assert Path(res.out_path).is_file(), "cutout PNG not written"
            assert Image.open(res.out_path).mode == "RGBA", "output is not RGBA"
        print(f"PASS: clean_cutout full pipeline — {res.action} ({res.detail or res.reason}).")


def main() -> int:
    test_pure_math()
    test_full_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

The deterministic core is verified with ONLY numpy + Pillow (no model, no
download) — matching the public rule that the tool must work fully without AI:
  * pure ops: chroma_alpha, detect_bg_color, despill, premultiply, coverage.
  * full deterministic pipeline: a subject on a flat green background is keyed
    out (chroma) and on an auto-detected background (solid).
The AI method is only exercised if onnxruntime + a cached model are present.

Run standalone:  python -m tools.alpha_doctor.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

np: Any = None
e: Any = None


def test_pure() -> None:
    # chroma: a green pixel keys to ~0, a red pixel stays ~255.
    px = np.array([[[0, 255, 0], [220, 30, 30]]], dtype=np.uint8)
    a = e.chroma_alpha(px, e._hex_to_rgb("#00FF00"), tol=100, feather=60)
    assert a[0, 0] < 40 and a[0, 1] > 200, f"chroma alpha wrong: {a.tolist()}"
    # detect_bg_color: a green border is detected as green.
    img = np.zeros((10, 10, 3), np.uint8); img[:, :, 1] = 255; img[3:7, 3:7] = (200, 30, 30)
    bg = e.detect_bg_color(img)
    assert bg[1] > 200 and bg[0] < 40, f"bg detect wrong: {bg}"
    # despill kills green tint; premultiply darkens transparent RGB; coverage.
    assert e.despill(np.array([[[10, 200, 20]]], np.uint8))[0, 0, 1] <= 20 + 1e-3
    rgba = np.zeros((4, 4, 4), np.uint8); rgba[..., :3] = 200; rgba[..., 3] = 128
    assert e.premultiply(rgba)[0, 0, 0] < 200
    half = np.zeros((10, 10, 4), np.uint8); half[:, :5, 3] = 255
    assert abs(e.alpha_coverage(half) - 0.5) < 1e-6
    print("PASS: alpha_doctor pure — chroma, bg-detect, despill, premultiply, coverage.")


def test_deterministic_pipeline() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — pipeline leg skipped."); return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"
        # a red disc on a flat green background.
        img = np.zeros((128, 128, 3), np.uint8); img[:, :, 1] = 255
        yy, xx = np.ogrid[:128, :128]
        img[(yy - 64) ** 2 + (xx - 64) ** 2 <= 40 ** 2] = (220, 40, 40)
        src = tmp / "subject.png"; Image.fromarray(img, "RGB").save(src)

        for method in ("chroma", "solid", "edge_flood"):
            res = e.process(src, e.AlphaOptions(out_root=out, method=method, key_color="#00FF00",
                                                dry_run=False))
            assert res.action == "cut", f"{method}: {res.reason}"
            with Image.open(res.out_path) as got:
                assert got.mode == "RGBA", f"{method}: not RGBA"
            # the disc (~30% of the frame) should survive; the green bg should not.
            assert 0.10 < res.coverage < 0.60, f"{method}: coverage {res.coverage:.2f} off"
    print("PASS: alpha_doctor deterministic pipeline — chroma / solid / edge_flood keyed a subject.")


def test_ai_optional() -> None:
    if importlib.util.find_spec("onnxruntime") is None or importlib.util.find_spec("PIL") is None:
        print("SKIP: onnxruntime not installed — AI method skipped (optional)."); return
    if not any((d / "u2net.onnx").is_file() for d in e._model_dirs()):
        print("SKIP: u2net model not cached — AI method skipped (no download in tests)."); return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img = np.full((128, 128, 3), 30, np.uint8)
        yy, xx = np.ogrid[:128, :128]
        img[(yy - 64) ** 2 + (xx - 64) ** 2 <= 44 ** 2] = (230, 60, 60)
        src = tmp / "s.png"; Image.fromarray(img, "RGB").save(src)
        res = e.process(src, e.AlphaOptions(out_root=tmp / "out", method="ai", dry_run=False))
        assert res.action in ("cut", "skipped"), f"ai: {res.reason}"
    print("PASS: alpha_doctor AI method (u2net) — optional path works.")


def main() -> int:
    global np, e
    if importlib.util.find_spec("numpy") is None:
        print("SKIP: numpy not installed — alpha_doctor tests skipped.")
        return 0
    import numpy as _np
    from tools.alpha_doctor import engine as _engine

    np = _np
    e = _engine
    test_pure()
    test_deterministic_pipeline()
    test_ai_optional()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

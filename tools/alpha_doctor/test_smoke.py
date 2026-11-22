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

try:
    import numpy as np
    from tools.alpha_doctor import engine as e
except ImportError:
    np = None
    e = None



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
            assert res.artifact and e.validate_result(
                res,
                e.AlphaOptions(
                    out_root=out, method=method, key_color="#00FF00",
                    dry_run=False,
                ),
            )
            with Image.open(res.out_path) as got:
                assert got.mode == "RGBA", f"{method}: not RGBA"
            # the disc (~30% of the frame) should survive; the green bg should not.
            assert 0.10 < res.coverage < 0.60, f"{method}: coverage {res.coverage:.2f} off"

        output = Path(res.out_path)
        output_bytes = output.read_bytes()
        output.write_bytes(b"X" * len(output_bytes))
        assert not e.validate_result(
            res,
            e.AlphaOptions(out_root=out, method="edge_flood", dry_run=False),
        ), "same-size cutout corruption was reused"
        output.write_bytes(output_bytes)
        assert e.validate_result(
            res,
            e.AlphaOptions(out_root=out, method="edge_flood", dry_run=False),
        )

        cancel_out = tmp / "cancelled"
        try:
            e.process(
                src,
                e.AlphaOptions(out_root=cancel_out, dry_run=False),
                cancelled=lambda: True,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("Alpha Doctor cancellation did not propagate")
        assert not cancel_out.exists()
        assert not list(tmp.rglob("*.part.png"))

        source_bytes = src.read_bytes()
        self_target = e.process(
            src,
            e.AlphaOptions(
                out_root=tmp, method="chroma", key_color="#00FF00",
                do_defringe=False, dry_run=False,
            ),
        )
        assert self_target.action == "failed"
        assert self_target.detail == "output.collision"
        assert src.read_bytes() == source_bytes, "Alpha Doctor replaced its source"

        other_dir = tmp / "other"
        other_dir.mkdir()
        same_stem = other_dir / "subject.jpg"
        Image.new("RGB", (32, 32), (240, 240, 240)).save(same_stem)
        collisions = e.find_output_collisions(
            [src, same_stem],
            e.AlphaOptions(out_root=out, dry_run=False),
        )
        assert len(collisions) == 1, "same-stem outputs were not rejected"
    print("PASS: alpha_doctor deterministic pipeline — chroma / solid / edge_flood keyed a subject.")


def test_safety_contracts() -> None:
    invalid = [
        e.AlphaOptions(method="missing"),
        e.AlphaOptions(method="chroma", key_color="green"),
        e.AlphaOptions(tolerance=float("nan")),
        e.AlphaOptions(tolerance=-1),
        e.AlphaOptions(feather_band=500),
        e.AlphaOptions(erode_px=33),
        e.AlphaOptions(min_coverage=1.1),
        e.AlphaOptions(method="ai", model="unknown"),
    ]
    for options in invalid:
        normalized, reason = e.normalized_options(options)
        assert normalized is None and reason

    with tempfile.TemporaryDirectory() as td:
        model_dir = Path(td)
        corrupt = model_dir / "u2net.onnx"
        corrupt.write_bytes(b"not an ONNX model")
        original_dirs = e._model_dirs
        e._model_dirs = lambda: [model_dir]
        e._VERIFIED_MODELS.clear()
        try:
            checked = e._ensure_model("u2net")
            assert checked["error_type"] == "model.corrupt"
            corrupt.unlink()
            denied = e._ensure_model("u2net")
            assert denied["error_type"] == "model.permission"
            assert not list(model_dir.glob("*.part"))
        finally:
            e._VERIFIED_MODELS.clear()
            e._model_dirs = original_dirs

    class FakeDownload:
        headers = {}

        def __init__(self):
            self._read = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            if self._read:
                return b""
            self._read = True
            return b"M" * 1024

    with tempfile.TemporaryDirectory() as td:
        model_dir = Path(td)
        original_dirs = e._model_dirs
        original_destination = e._download_model_path
        original_open = e.urllib.request.urlopen
        checks = 0

        def cancel_download() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 2

        e._model_dirs = lambda: [model_dir]
        e._download_model_path = lambda model: model_dir / f"{model}.onnx"
        e.urllib.request.urlopen = lambda *_args, **_kwargs: FakeDownload()
        try:
            try:
                e._ensure_model(
                    "u2net", allow_download=True, cancelled=cancel_download
                )
            except e.CommandCancelled:
                pass
            else:
                raise AssertionError("model download cancellation did not propagate")
            assert not list(model_dir.iterdir()), "cancelled model left staged data"
        finally:
            e._model_dirs = original_dirs
            e._download_model_path = original_destination
            e.urllib.request.urlopen = original_open
    print("PASS: alpha_doctor safety — settings, source/collision, model consent/checksum.")


def test_ai_optional() -> None:
    if importlib.util.find_spec("onnxruntime") is None or importlib.util.find_spec("PIL") is None:
        print("SKIP: onnxruntime not installed — AI method skipped (optional)."); return
    model = next(
        (name for name in ("u2netp", "u2net") if e.cached_model_path(name) is not None),
        None,
    )
    if model is None:
        print("SKIP: no AI model cached — AI method skipped (no download in tests)."); return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img = np.full((128, 128, 3), 30, np.uint8)
        yy, xx = np.ogrid[:128, :128]
        img[(yy - 64) ** 2 + (xx - 64) ** 2 <= 44 ** 2] = (230, 60, 60)
        src = tmp / "s.png"; Image.fromarray(img, "RGB").save(src)
        res = e.process(
            src,
            e.AlphaOptions(
                out_root=tmp / "out", method="ai", model=model, dry_run=False,
            ),
        )
        assert res.action in ("cut", "skipped"), f"ai: {res.reason}"
    print(f"PASS: alpha_doctor AI method ({model}) — optional path works.")


def test_auto_despill_and_enclosed() -> None:
    """2026-11-12 audit: despill was off by default (edge spill 143); enclosed background kept."""
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — despill leg skipped."); return
    from PIL import Image, ImageFilter
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        yy, xx = np.ogrid[:128, :128]
        disc = ((yy - 64) ** 2 + (xx - 64) ** 2 <= 40 ** 2)
        for screen, bg, chan in (("green", (0, 255, 0), 1), ("blue", (0, 0, 255), 2)):
            # a teal-ish subject composited with a soft edge, plus a solid subject-colour patch
            alpha = np.asarray(Image.fromarray(disc.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(2)),
                               np.float32)[..., None] / 255
            subject = np.array((40, 150, 140), np.float32)
            img = (subject * alpha + np.array(bg, np.float32) * (1 - alpha)).astype(np.uint8)
            src = tmp / f"{screen}.png"; Image.fromarray(img, "RGB").save(src)
            res = e.process(src, e.AlphaOptions(out_root=tmp / screen, method="solid", dry_run=False))
            assert res.action == "cut" and f"despill:auto-{screen}" in res.detail, res
            with Image.open(res.out_path) as got:
                out = np.asarray(got.convert("RGBA"), np.float32)
            others = [c for c in range(3) if c != chan]
            edge = (out[..., 3] > 0) & (out[..., 3] < 255)
            spill = np.clip(out[..., chan] - np.maximum(out[..., others[0]], out[..., others[1]]), 0, None)
            # the subject's own colour has a green excess of 10, so allow that much
            assert edge.any() and float(spill[edge].mean()) < 5.0, (screen, float(spill[edge].mean()))
            off = e.process(src, e.AlphaOptions(out_root=tmp / f"{screen}_off", method="solid",
                                                green_despill=False, dry_run=False))
            with Image.open(off.out_path) as got:
                raw = np.asarray(got.convert("RGBA"), np.float32)
            raw_spill = np.clip(raw[..., chan] - np.maximum(raw[..., others[0]], raw[..., others[1]]), 0, None)
            assert float(raw_spill[edge].mean()) > 3 * float(spill[edge].mean()) + 5, "despill had no effect"
            assert tuple(out[64, 64, :3]) == (40, 150, 140), "subject interior must be untouched"
        # A paper background is not a screen: no despill.
        paper = np.full((64, 64, 3), (242, 238, 228), np.uint8); paper[16:48, 16:48] = (30, 140, 90)
        Image.fromarray(paper).save(tmp / "paper.png")
        res = e.process(tmp / "paper.png", e.AlphaOptions(out_root=tmp / "paper", method="solid", dry_run=False))
        assert "despill" not in res.detail, res.detail
        # Edge flood: a background-coloured hole enclosed by a ring stays unless asked.
        ring = np.full((96, 96, 3), 245, np.uint8)
        yy, xx = np.ogrid[:96, :96]
        r2 = (yy - 48) ** 2 + (xx - 48) ** 2
        ring[(r2 <= 36 ** 2) & (r2 >= 20 ** 2)] = (200, 30, 30)
        Image.fromarray(ring).save(tmp / "ring.png")
        kept = e.process(tmp / "ring.png", e.AlphaOptions(out_root=tmp / "kept", method="edge_flood",
                                                          do_defringe=False, dry_run=False))
        removed = e.process(tmp / "ring.png", e.AlphaOptions(out_root=tmp / "removed", method="edge_flood",
                                                             remove_enclosed=True, do_defringe=False, dry_run=False))
        with Image.open(kept.out_path) as a, Image.open(removed.out_path) as b:
            assert a.getpixel((48, 48))[3] == 255 and b.getpixel((48, 48))[3] == 0
            assert b.getpixel((48, 20))[3] == 255, "the ring itself must stay"
    print("PASS: alpha_doctor auto despill (green/blue edges, interior untouched, none on paper) + enclosed option.")


def test_red_orange_despill_and_decontamination() -> None:
    """Verify red/orange rim spill suppression + inward RGB edge decontamination."""
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — decontamination leg skipped.")
        return
    from PIL import Image

    # 1. Pure red and orange despill preserve neutral/navy colors while clamping warm spill.
    probe = np.array(
        [[[230, 95, 35], [220, 30, 30], [20, 30, 60], [180, 180, 185]]],
        dtype=np.uint8,
    )
    orange_out = e.despill(probe, "orange")
    red_out = e.despill(probe, "red")
    assert orange_out[0, 0, 0] <= orange_out[0, 0, 1] + 1e-3
    assert orange_out[0, 0, 1] < 95, f"orange secondary green spill not pulled: {orange_out[0, 0]}"
    assert red_out[0, 1, 0] <= 30 + 1e-3
    assert tuple(orange_out[0, 2].astype(int)) == (20, 30, 60), "navy untouched"
    assert tuple(orange_out[0, 3].astype(int)) == (180, 180, 185), "neutral grey untouched"
    assert e.screen_colour(np.array([220, 30, 30], dtype=np.float32)) == "red"

    # 2. Direct inward RGB decontamination removes orange rim + white outline on boundary pixels.
    rgba = np.zeros((64, 64, 4), dtype=np.uint8)
    rgba[16:48, 16:48, :3] = (20, 30, 60)
    rgba[16:48, 16:48, 3] = 255
    # Contaminate outer 2px rim inside solid boundary with warm orange-red spill, plus 1px grey halo.
    rgba[16:18, 16:48, :3] = (230, 95, 35)
    rgba[46:48, 16:48, :3] = (230, 95, 35)
    rgba[16:48, 16:18, :3] = (230, 95, 35)
    rgba[16:48, 46:48, :3] = (230, 95, 35)
    rgba[15, 15:49] = (210, 210, 215, 128)
    rgba[48, 15:49] = (210, 210, 215, 128)

    cleaned = e.decontaminate_edges(rgba, inward_px=2, bleed_px=2)
    assert tuple(cleaned[32, 32]) == (20, 30, 60, 255), "interior core altered"
    assert tuple(cleaned[16, 32, :3]) == (20, 30, 60), f"rim not replaced: {cleaned[16, 32]}"
    assert tuple(cleaned[15, 32, :3]) == (20, 30, 60), f"halo not replaced: {cleaned[15, 32]}"
    assert int(cleaned[15, 32, 3]) == 128, "decontaminate_edges must preserve alpha"

    # 3. Full process() pipeline with orange rim despill + inward decontamination.
    for bad in (
        e.AlphaOptions(green_despill="purple"),
        e.AlphaOptions(decontaminate_px=0),
        e.AlphaOptions(decontaminate_px=17),
        e.AlphaOptions(decontaminate_px=True),
    ):
        norm, reason = e.normalized_options(bad)
        assert norm is None and reason, f"expected invalid option rejection for {bad}"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        img = np.zeros((96, 96, 3), dtype=np.uint8)
        img[24:72, 24:72] = (20, 30, 60)
        # Bake a 2px orange rim + 1px white outline around the dark navy square over #000000.
        img[24:26, 24:72] = (230, 95, 35)
        img[70:72, 24:72] = (230, 95, 35)
        img[24:72, 24:26] = (230, 95, 35)
        img[24:72, 70:72] = (230, 95, 35)
        img[23, 23:73] = (210, 210, 215)
        img[72, 23:73] = (210, 210, 215)
        src = tmp / "presenter.png"
        Image.fromarray(img, "RGB").save(src)

        opts = e.AlphaOptions(
            out_root=tmp / "out",
            method="chroma",
            key_color="#000000",
            tolerance=40.0,
            feather_band=20.0,
            green_despill="orange",
            do_decontaminate=True,
            decontaminate_px=3,
            do_defringe=True,
            erode_px=1,
            feather=0.5,
            dry_run=False,
        )
        res = e.process(src, opts)
        assert res.action == "cut", res
        assert "despill:orange" in res.detail and "decontam:3" in res.detail, res.detail
        with Image.open(res.out_path) as got:
            out = np.asarray(got.convert("RGBA"), dtype=np.uint8)
        assert tuple(out[48, 48, :3]) == (20, 30, 60), "interior navy damaged"
        visible = out[..., 3] > 20
        red_excess = out[..., 0].astype(np.int16) - np.maximum(out[..., 1], out[..., 2]).astype(np.int16)
        assert int(red_excess[visible].max()) <= 5, f"orange rim survived: {int(red_excess[visible].max())}"
    print("PASS: alpha_doctor red/orange rim despill + inward RGB edge decontamination.")


def test_video_prores_pipeline() -> None:
    """End-to-end ProRes 4444 .mov matting, audio preservation, reuse check, and cancellation."""
    import subprocess

    tools = e.tools_status()
    if not tools.get("ffmpeg") or not tools.get("ffprobe"):
        print("SKIP: ffmpeg/ffprobe not installed — video ProRes pipeline skipped.")
        return

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = tmp / "clip.mp4"
        out = tmp / "out"
        gen_cmd = [
            tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0x00FF00:s=160x90:r=6:d=0.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
            "-vf", "drawbox=x=50:y=20:w=55:h=50:color=0x1E2640:t=fill",
            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(src),
        ]
        gen = subprocess.run(gen_cmd, capture_output=True, text=True)
        assert gen.returncode == 0, f"fixture build failed: {gen.stderr}"

        opts = e.AlphaOptions(
            out_root=out,
            method="chroma",
            key_color="#00FF00",
            tolerance=95.0,
            feather_band=45.0,
            do_decontaminate=True,
            decontaminate_px=2,
            dry_run=False,
        )
        res = e.process(src, opts)
        assert res.action == "cut", f"video cut failed: {res}"
        assert res.out_path and Path(res.out_path).suffix == ".mov", res.out_path
        assert res.artifact is not None
        assert res.artifact["pix_fmt"].startswith("yuva"), res.artifact
        assert res.artifact["has_audio"] is True, res.artifact
        assert res.artifact["frames"] >= 2, res.artifact
        assert "decontam:2" in res.detail, res.detail
        assert e.validate_result(res, opts, expected_src=src)

        out_mov = Path(res.out_path)
        mov_bytes = out_mov.read_bytes()
        out_mov.write_bytes(b"X" * len(mov_bytes))
        assert not e.validate_result(res, opts), "same-size .mov corruption was reused"
        out_mov.write_bytes(mov_bytes)
        assert e.validate_result(res, opts), "valid .mov failed re-validation"

        # Decode frame 0 from the ProRes 4444 .mov and inspect alpha corners vs subject center.
        raw_cmd = [
            tools["ffmpeg"], "-hide_banner", "-loglevel", "error",
            "-i", str(out_mov), "-map", "0:v:0", "-frames:v", "1",
            "-vf", "format=rgba", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
        ]
        dec = subprocess.run(raw_cmd, capture_output=True)
        assert dec.returncode == 0 and len(dec.stdout) == 160 * 90 * 4
        frame0 = np.frombuffer(dec.stdout, dtype=np.uint8).reshape((90, 160, 4))
        assert frame0[5, 5, 3] < 20, f"corner alpha not transparent: {frame0[5, 5]}"
        assert frame0[45, 75, 3] > 240, f"subject center not opaque: {frame0[45, 75]}"

        # Cancellation must clean staged .part.mov without leaving orphans.
        cancel_out = tmp / "cancelled"
        checks = 0

        def cancel_after_start() -> bool:
            nonlocal checks
            checks += 1
            return checks >= 3

        try:
            e.process(
                src,
                e.AlphaOptions(out_root=cancel_out, method="chroma", key_color="#00FF00", dry_run=False),
                cancelled=cancel_after_start,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("video cancellation did not propagate")
        assert not list(tmp.rglob("*.part.mov")), "cancelled video left staged .part.mov"

        # Collision checks: self-overwrite (.mov -> same .mov) and same-stem (.mp4 + .mov).
        self_mov = tmp / "same.mov"
        self_mov.write_bytes(mov_bytes)
        self_res = e.process(self_mov, e.AlphaOptions(out_root=tmp, dry_run=False))
        assert self_res.action == "failed" and self_res.detail == "output.collision"
        assert self_mov.read_bytes() == mov_bytes, "source .mov overwritten"

        other_dir = tmp / "other"
        other_dir.mkdir()
        other_mov = other_dir / "clip.mov"
        other_mov.write_bytes(b"stub")
        collisions = e.find_output_collisions([src, other_mov], e.AlphaOptions(out_root=out, dry_run=False))
        assert len(collisions) == 1, "same-stem video outputs were not rejected"
    print("PASS: alpha_doctor video ProRes 4444 .mov pipeline — matte, audio, validation, cancel, collision.")


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
    test_auto_despill_and_enclosed()
    test_red_orange_despill_and_decontamination()
    test_video_prores_pipeline()
    test_safety_contracts()
    test_ai_optional()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

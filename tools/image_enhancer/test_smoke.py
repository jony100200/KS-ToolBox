"""Release-bar smoke test for the standalone local Image Enhancer."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.image_enhancer import engine as e  # noqa: E402
from tools.image_enhancer import filter_stack as fs  # noqa: E402
from tools.image_enhancer import smart  # noqa: E402
from tools.image_enhancer import cli  # noqa: E402
from tools.image_enhancer import classical_ops  # noqa: E402
from tools.image_enhancer import model_rack  # noqa: E402


def _psnr(a, b) -> float:
    x = np.asarray(a.convert("RGB"), np.float64); y = np.asarray(b.convert("RGB"), np.float64)
    mse = float(np.mean((x - y) ** 2))
    return 99.0 if mse == 0 else float(10 * np.log10(255 ** 2 / mse))


def _fixture():
    """Photo-like synthetic image: gradients, hard edges, soft texture (deterministic)."""
    from PIL import Image, ImageDraw, ImageFilter
    h, w = 192, 256
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(3)
    tex = np.asarray(Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8))
                     .filter(ImageFilter.GaussianBlur(3)), np.float32) - 128
    rgb = np.stack([60 + xx * 0.5 + tex * 0.8, 50 + yy * 0.6 + tex * 0.6, 120 + tex * 0.5], -1)
    image = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 120, 120), fill=(220, 180, 60))
    draw.ellipse((140, 60, 220, 150), fill=(30, 140, 90))
    draw.line((10, 170, 245, 20), fill=(250, 250, 250), width=2)
    return image


def _run(e_opts_root, src, **kw):
    from PIL import Image
    res = e.process(src, e.EnhanceOptions(out_root=e_opts_root, mode="deterministic", **kw))
    assert res.action in {"enhanced", "needs-review"}, res
    with Image.open(res.out_path) as out:
        return out.convert("RGBA").copy(), res


def quality_regressions() -> None:
    """2026-11-12 audit regressions. Pillow + numpy only, so they run in the shipped build."""
    from PIL import Image, ImageFilter
    clean = _fixture()
    rng = np.random.default_rng(7)
    arr = np.asarray(clean, np.float32)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Manual presets do not get the smart tonal plan.
        clean.save(root / "clean.png")
        out, res = _run(root / "manual", root / "clean.png", preset="custom")
        assert res.metadata["smart_plan_applied"] is False
        assert _psnr(out, clean) == 99.0, "custom preset with neutral sliders changed the image"
        _, auto = _run(root / "auto", root / "clean.png", preset="auto")
        assert auto.metadata["smart_plan_applied"] is True
        # Zero-strength passes never run (a 0-strength de-gloss used to boost pores).
        assert fs.build_stack(preset="custom", brightness=1, contrast=1, gamma=1, hue_degrees=0, saturation=1,
                              vibrance=0, temperature=0, tint=0, denoise=0, sharpen=1, high_pass=0,
                              edge_boost=0) == []

        # Denoise: edge-preserving, beats the old Gaussian (audit 31.0 dB) by a margin.
        noisy = Image.fromarray(np.clip(arr + rng.normal(0, 12, arr.shape), 0, 255).astype(np.uint8))
        noisy.save(root / "noisy.png")
        den, _ = _run(root / "den", root / "noisy.png", preset="custom", denoise=1.0)
        before = _psnr(noisy, clean)
        assert _psnr(den, clean) >= before + 6.0, (before, _psnr(den, clean))
        # ...and barely touches a clean image.
        den_clean, _ = _run(root / "den_clean", root / "clean.png", preset="custom", denoise=1.0)
        assert _psnr(den_clean, clean) >= 40.0, _psnr(den_clean, clean)

        # Sharpen / detail: unsharp mask must improve a mildly blurred image.
        blurry = clean.filter(ImageFilter.GaussianBlur(1.2)); blurry.save(root / "blurry.png")
        for kw in ({"preset": "detail"}, {"preset": "custom", "sharpen": 2.0}):
            sharp, _ = _run(root / f"sharp_{kw['preset']}", root / "blurry.png", **kw)
            assert _psnr(sharp, clean) > _psnr(blurry, clean), (kw, _psnr(sharp, clean), _psnr(blurry, clean))

        # Despeckle preset: gated median only.
        speck = arr.copy(); mask = rng.random(arr.shape[:2])
        speck[mask < 0.005] = 0; speck[mask > 0.995] = 255
        Image.fromarray(speck.astype(np.uint8)).save(root / "speck.png")
        desp, _ = _run(root / "desp", root / "speck.png", preset="despeckle_clean")
        assert _psnr(desp, clean) >= 38.0, _psnr(desp, clean)

        # Edge boost keeps the brightness (was blended towards a black edge map).
        boosted, _ = _run(root / "edge", root / "clean.png", preset="custom", edge_boost=1.0)
        luma_in = float(np.asarray(clean.convert("L"), np.float32).mean())
        luma_out = float(np.asarray(boosted.convert("L"), np.float32).mean())
        assert abs(luma_out - luma_in) < 1.5, (luma_in, luma_out)

        # Transparent sprite: the colour hidden under alpha=0 must not bleed into edges.
        rgba = np.dstack([arr, np.zeros(arr.shape[:2], np.float32)])
        rgba[30:160, 30:220, 3] = 255
        rgba[28:30, 30:220, 3] = 128                     # a soft top edge
        rgba[rgba[..., 3] == 0, :3] = (255, 0, 255)      # magenta hidden colour
        Image.fromarray(rgba.astype(np.uint8), "RGBA").save(root / "sprite.png")
        sprite, _ = _run(root / "sprite", root / "sprite.png", preset="gentle_restore")
        band = np.asarray(sprite)[28:31, 30:220].astype(np.float32)
        magenta = np.clip((band[..., 0] + band[..., 2]) / 2 - band[..., 1], 0, None).mean()
        reference = np.clip((arr[28:31, 30:220, 0] + arr[28:31, 30:220, 2]) / 2 - arr[28:31, 30:220, 1], 0, None).mean()
        assert magenta <= reference + 3.0, (magenta, reference)

        # OpenCV-only presets announce their fallback instead of running silently.
        if not classical_ops.has_opencv():
            _, clahe = _run(root / "clahe", root / "clean.png", preset="clahe_texture")
            assert clahe.action == "needs-review" and clahe.metadata.get("fallbacks"), clahe
            assert "needs-OpenCV" in clahe.reason, clahe.reason
            assert e.utility_status()["opencv_ready"] is False
    print("PASS: image_enhancer quality - manual presets exact, denoise/sharpen/despeckle improve, "
          "edge boost keeps brightness, no RGBA fringe, OpenCV fallbacks reported.")


def main() -> int:
    from PIL import Image, ImageDraw
    state = e.utility_status()
    assert all(key in state for key in ("upscale_ready", "segmentation_ready", "faces_ready", "repair_ready")), state
    import importlib.util
    quality_regressions()
    if importlib.util.find_spec("cv2") is None:
        print("SKIP: OpenCV not installed - Image Enhancer checks skipped (optional).")
        return 0
    assert state["repair_ready"] is True, state
    valid_output_actions = {"enhanced", "needs-review"}
    compacted = fs.compact_stack([fs.FilterPass("contrast", {"factor": 1.1}), fs.FilterPass("contrast", {"factor": 1.2})])
    assert len(compacted) == 1 and round(compacted[0].params["factor"], 2) == 1.32

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rgb = root / "source.png"
        image = Image.new("RGB", (64, 48), (100, 110, 130))
        ImageDraw.Draw(image).rectangle((16, 12, 48, 36), fill=(200, 90, 50))
        image.save(rgb)

        # 1. Spatial, Wavelet, Creative, CLAHE, & Privacy Operations Test
        # (a) Wavelet Decompose exact mathematical identity test
        bands, residual = classical_ops.wavelet_decompose(image, scales=5)
        assert len(bands) == 5
        reconstructed = classical_ops.wavelet_recombine(bands, residual)
        src_arr = np.asarray(image, dtype=np.float32)
        rec_arr = np.asarray(reconstructed, dtype=np.float32)
        assert np.allclose(src_arr, rec_arr, atol=1.0), "Wavelet decomposition failed exact reconstruction"

        # (b) Multi-Scale Retinex with Color Restoration (MSRCR)
        retinex_img = classical_ops.retinex_mscr(image)
        assert retinex_img.size == image.size

        # (c) Shadows / Highlights, Unsharp Mask, Soft Glow, Clarity, Vignette, Film Grain, Split Tone
        sh_img = classical_ops.shadows_highlights(image)
        assert sh_img.size == image.size
        unsharp_img = classical_ops.unsharp_mask_threshold(image)
        assert unsharp_img.size == image.size
        glow_img = classical_ops.soft_glow_orton(image)
        assert glow_img.size == image.size
        clarity_img = classical_ops.clarity(image)
        assert clarity_img.size == image.size
        vig_img = classical_ops.vignette(image)
        assert vig_img.size == image.size
        grain_img = classical_ops.film_grain(image)
        assert grain_img.size == image.size
        split_img = classical_ops.split_tone(image)
        assert split_img.size == image.size

        # (d) CLAHE, Auto White-Balance, Despeckle, DCP Dehaze
        clahe_img = classical_ops.clahe_local_contrast(image)
        assert clahe_img.size == image.size
        awb_img = classical_ops.auto_white_balance(image)
        assert awb_img.size == image.size
        despeckle_img = classical_ops.adaptive_despeckle(image)
        assert despeckle_img.size == image.size
        dcp_img = classical_ops.dark_channel_dehaze(image)
        assert dcp_img.size == image.size
        vec_img = classical_ops.vector_cel_shade(image)
        assert vec_img.size == image.size
        anime_img = classical_ops.anime_cel_shader(image)
        assert anime_img.size == image.size
        red_img = classical_ops.fix_red_eye(image)
        assert red_img.size == image.size

        # (e) Privacy & Censor Blurs
        priv_blur = classical_ops.privacy_blur(image, radius=24.0)
        assert priv_blur.size == image.size
        pix_blur = classical_ops.privacy_blur(image, pixelate_block=8)
        assert pix_blur.size == image.size
        focus_blur = classical_ops.radial_focus_blur(image, focus_radius=0.3)
        assert focus_blur.size == image.size
        box_blur = classical_ops.box_censor_blur(image, boxes=((10, 10, 30, 30),))
        assert box_blur.size == image.size

        # 2. One-Button Auto Enhance test
        auto_res = e.process(rgb, e.EnhanceOptions(out_root=root / "auto", preset="auto", mode="deterministic"))
        assert auto_res.action in valid_output_actions and e.validate_result(auto_res), auto_res
        assert "smart_profile" in auto_res.metadata and "smart_plan" in auto_res.metadata

        # 3. Creative, CLAHE, & Privacy Presets in Engine
        vivid_res = e.process(rgb, e.EnhanceOptions(out_root=root / "vivid", preset="vivid_pop", mode="deterministic"))
        assert vivid_res.action in valid_output_actions and e.validate_result(vivid_res), vivid_res

        clahe_res = e.process(rgb, e.EnhanceOptions(out_root=root / "clahe", preset="clahe_texture", mode="deterministic"))
        assert clahe_res.action in valid_output_actions and e.validate_result(clahe_res), clahe_res

        awb_res = e.process(rgb, e.EnhanceOptions(out_root=root / "awb", preset="auto_white_balance", mode="deterministic"))
        assert awb_res.action in valid_output_actions and e.validate_result(awb_res), awb_res

        desp_res = e.process(rgb, e.EnhanceOptions(out_root=root / "desp", preset="despeckle_clean", mode="deterministic"))
        assert desp_res.action in valid_output_actions and e.validate_result(desp_res), desp_res

        dcp_res = e.process(rgb, e.EnhanceOptions(out_root=root / "dcp", preset="dcp_dehaze", mode="deterministic"))
        assert dcp_res.action in valid_output_actions and e.validate_result(dcp_res), dcp_res

        vec_res = e.process(rgb, e.EnhanceOptions(out_root=root / "vec", preset="vector_cel", mode="deterministic"))
        assert vec_res.action in valid_output_actions and e.validate_result(vec_res), vec_res

        anime_res = e.process(rgb, e.EnhanceOptions(out_root=root / "anime", preset="anime_style", mode="deterministic"))
        assert anime_res.action in valid_output_actions and e.validate_result(anime_res), anime_res

        red_res = e.process(rgb, e.EnhanceOptions(out_root=root / "redeye", preset="red_eye_fix", mode="deterministic"))
        assert red_res.action in valid_output_actions and e.validate_result(red_res), red_res

        teal_res = e.process(rgb, e.EnhanceOptions(out_root=root / "teal", preset="cinematic_teal", mode="deterministic"))
        assert teal_res.action in valid_output_actions and e.validate_result(teal_res), teal_res

        glamour_res = e.process(rgb, e.EnhanceOptions(out_root=root / "glamour", preset="soft_glamour", mode="deterministic"))
        assert glamour_res.action in valid_output_actions and e.validate_result(glamour_res), glamour_res

        priv_res = e.process(rgb, e.EnhanceOptions(out_root=root / "privacy", preset="privacy_censor", mode="deterministic"))
        assert priv_res.action in valid_output_actions and e.validate_result(priv_res), priv_res

        pix_res = e.process(rgb, e.EnhanceOptions(out_root=root / "pixelate", preset="pixelate_censor", mode="deterministic"))
        assert pix_res.action in valid_output_actions and e.validate_result(pix_res), pix_res

        # 4. De-Gloss / Natural Skin test
        degloss_res = e.process(rgb, e.EnhanceOptions(out_root=root / "degloss", preset="natural_skin", mode="deterministic"))
        assert degloss_res.action in valid_output_actions and e.validate_result(degloss_res), degloss_res

        phase1 = e.process(rgb, e.EnhanceOptions(out_root=root / "phase1", preset="detail"))
        assert phase1.action in valid_output_actions and e.validate_result(phase1), phase1
        assert phase1.metadata["smart_plan"]["effective_mode"] == "hybrid", phase1.metadata

        profile = smart.analyse(image)
        deterministic_plan = smart.plan(profile, requested_mode="deterministic", auto_select=False, requested_scale=4)
        assert deterministic_plan.effective_mode == "deterministic" and not deterministic_plan.use_model

        complexion_guard = smart.skin_tone_guard(Image.new("RGB", (64, 64), (114, 74, 42)),
                                                  Image.new("RGB", (64, 64), (205, 201, 192)))
        assert complexion_guard["needs_review"], complexion_guard

        deterministic = e.process(rgb, e.EnhanceOptions(out_root=root / "deterministic", mode="deterministic", scale_factor=4))
        assert deterministic.action in valid_output_actions and e.validate_result(deterministic), deterministic
        with Image.open(deterministic.out_path) as deterministic_image:
            assert deterministic_image.size == image.size, "deterministic mode loaded an upscale model"
        assert deterministic.metadata["smart_plan"]["use_model"] is False

        preview = e.process(rgb, e.EnhanceOptions(out_root=root / "preview", mode="hybrid", auto_select_mode=True, dry_run=True))
        assert preview.action == "dry-run" and preview.metadata["smart_profile"] and preview.metadata["smart_plan"], preview

        colour = e.process(rgb, e.EnhanceOptions(out_root=root / "colour", preset="custom", hue_degrees=120, saturation=0.7,
                                                  vibrance=0.2, temperature=0.15, tint=-0.1))
        assert colour.action in valid_output_actions and e.validate_result(colour), colour
        with Image.open(colour.out_path) as adjusted:
            assert adjusted.convert("RGB").getpixel((20, 20)) != image.getpixel((20, 20)), "HSV stack did not alter the fixture"

        phase2 = e.process(rgb, e.EnhanceOptions(out_root=root / "phase2", preset="detail", region_mode="manual_box",
                                                  manual_box=(20, 20, 60, 60), debug_outputs=True))
        assert phase2.action in valid_output_actions and len(phase2.artifacts) == 2 and e.validate_result(phase2), phase2

        if state["segmentation_ready"]:
            segmented = e.process(rgb, e.EnhanceOptions(out_root=root / "segmented", region_mode="subject_mask", debug_outputs=True))
            assert segmented.action in valid_output_actions and len(segmented.artifacts) == 1 and e.validate_result(segmented), segmented
        assert e._face_boxes(image) == []

        rgba = Image.new("RGBA", (40, 40), (220, 100, 40, 255))
        ImageDraw.Draw(rgba).ellipse((16, 16, 23, 23), fill=(0, 0, 0, 0))
        cut = root / "cutout.png"
        rgba.save(cut)
        phase3 = e.process(cut, e.EnhanceOptions(out_root=root / "phase3", repair_alpha_holes=True, debug_outputs=True))
        assert phase3.action in valid_output_actions and e.validate_result(phase3), phase3
        with Image.open(phase3.out_path) as repaired:
            assert repaired.mode == "RGBA" and repaired.getpixel((20, 20))[3] == 255

        if state["upscale_ready"] and not os.environ.get("KS_TOOLBOX_SKIP_GPU"):
            ai = e.process(rgb, e.EnhanceOptions(out_root=root / "ai", mode="ai", scale_factor=2))
            assert ai.action in valid_output_actions and e.validate_result(ai), ai
            assert ai.metadata["smart_plan"]["effective_mode"] == "ai" and ai.metadata["smart_plan"]["use_model"]
            with Image.open(ai.out_path) as upscaled:
                assert upscaled.size == (128, 96), upscaled.size

        if not os.environ.get("KS_TOOLBOX_SKIP_GPU"):      # face detail runs Real-ESRGAN when ready
            face_detail = e.process(rgb, e.EnhanceOptions(out_root=root / "face_detail", region_mode="manual_box",
                                                           manual_box=(20, 20, 60, 60), face_detail=True))
            assert face_detail.action in valid_output_actions and e.validate_result(face_detail), face_detail

        # 5. Headless CLI execution test
        cli_out = root / "cli_out"
        cli_code = cli.main(["--input", str(rgb), "--out", str(cli_out), "--preset", "auto"])
        assert cli_code == 0
        assert (cli_out / f"{rgb.stem}_enhanced.png").is_file()
        assert (cli_out / "_enhance_manifest.json").is_file()

        # 6. Model Rack Catalog & Status test — only models engine.py actually
        # loads belong here (yunet_face, u2netp_mask); see model_rack.py header.
        specs = model_rack.list_models()
        assert len(specs) == 2
        assert {s.id for s in specs} == {"yunet_face", "u2netp_mask"}
        rack_status = model_rack.get_rack_status()
        assert rack_status["total"] == 2
        assert all(s.download_url.startswith("http") for s in specs)

    print("PASS: Image Enhancer Auto-Enhance, CLAHE, Auto-WB, Despeckle, Privacy Censor & Blur, Creative Studio Ops, Wavelet De-Gloss, Model Rack, Phases 1-3, Real-ESRGAN, and Headless CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

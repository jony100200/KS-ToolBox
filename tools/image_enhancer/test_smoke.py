"""Release-bar smoke test for the standalone local Image Enhancer."""
from __future__ import annotations

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


def main() -> int:
    from PIL import Image, ImageDraw
    state = e.utility_status()
    assert all(state[key] for key in ("upscale_ready", "segmentation_ready", "faces_ready", "repair_ready")), state
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

        ai = e.process(rgb, e.EnhanceOptions(out_root=root / "ai", mode="ai", scale_factor=2))
        assert ai.action in valid_output_actions and e.validate_result(ai), ai
        assert ai.metadata["smart_plan"]["effective_mode"] == "ai" and ai.metadata["smart_plan"]["use_model"]
        with Image.open(ai.out_path) as upscaled:
            assert upscaled.size == (128, 96), upscaled.size

        face_detail = e.process(rgb, e.EnhanceOptions(out_root=root / "face_detail", region_mode="manual_box",
                                                       manual_box=(20, 20, 60, 60), face_detail=True))
        assert face_detail.action in valid_output_actions and e.validate_result(face_detail), face_detail

        # 5. Headless CLI execution test
        cli_out = root / "cli_out"
        cli_code = cli.main(["--input", str(rgb), "--out", str(cli_out), "--preset", "auto"])
        assert cli_code == 0
        assert (cli_out / f"{rgb.stem}_enhanced.png").is_file()
        assert (cli_out / "_enhance_manifest.json").is_file()

        # 6. Model Rack Catalog & Status test
        specs = model_rack.list_models()
        assert len(specs) >= 8
        rack_status = model_rack.get_rack_status()
        assert rack_status["total"] >= 8
        assert all(s.download_url.startswith("http") for s in specs)

    print("PASS: Image Enhancer Auto-Enhance, CLAHE, Auto-WB, Despeckle, Privacy Censor & Blur, Creative Studio Ops, Wavelet De-Gloss, Model Rack, Phases 1-3, Real-ESRGAN, and Headless CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

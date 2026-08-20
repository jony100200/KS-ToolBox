"""Release-bar smoke test for the standalone local Image Enhancer."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.image_enhancer import engine as e  # noqa: E402
from tools.image_enhancer import filter_stack as fs  # noqa: E402
from tools.image_enhancer import smart  # noqa: E402


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
        ImageDraw.Draw(image).rectangle((16, 12, 48, 36), fill=(200, 90, 50)); image.save(rgb)
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
        # Runs the compact YuNet inference even though the synthetic fixture has no face.
        assert e._face_boxes(image) == []

        rgba = Image.new("RGBA", (40, 40), (220, 100, 40, 255))
        ImageDraw.Draw(rgba).ellipse((16, 16, 23, 23), fill=(0, 0, 0, 0))
        cut = root / "cutout.png"; rgba.save(cut)
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
    print("PASS: Image Enhancer phases 1–3 + local Real-ESRGAN 2×.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

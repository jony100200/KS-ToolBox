"""Real CustomTkinter → JobQueue → BatchRunner → QueuePanel integration check."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TERMINAL_STATES = {"completed", "completed_with_warnings", "failed", "cancelled"}


def _run_and_wait(
    app, panel, label: str, expected_items: int, start=None
) -> str:
    (start or panel._run)()
    job_id = panel._active_job_id
    assert job_id, f"{label} did not submit a queue job"
    deadline = time.monotonic() + 10
    snapshot = None
    while time.monotonic() < deadline:
        app.update()
        snapshot = app._services.queue.snapshot(job_id)
        if snapshot and snapshot.state.value in _TERMINAL_STATES:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"queued {label} job did not finish")
    control_deadline = time.monotonic() + 2
    while panel._run_btn.cget("state") != "normal" and time.monotonic() < control_deadline:
        app.update()
        time.sleep(0.01)
    assert snapshot.state.value == "completed", snapshot
    assert snapshot.completed_items == expected_items, snapshot
    assert panel._run_btn.cget("state") == "normal", (
        f"{label} controls did not recover; callback errors="
        f"{app._services.queue.subscriber_errors}"
    )
    return job_id


def main() -> int:
    try:
        from PIL import Image
        from toolbox.discovery import discover
        from toolbox.engine_common import resolve_tool, run_cmd
        from toolbox.queue_panel import QueuePanel
        from toolbox.shell import ToolBoxShell
    except ImportError as ex:
        print(f"SKIP: queue UI integration dependency unavailable: {ex}")
        return 0

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        os.environ["KS_TOOLBOX_STATE_DIR"] = str(root / "state")
        first, second = root / "first.png", root / "second.png"
        Image.new("RGB", (64, 32), (20, 40, 60)).save(first)
        Image.new("RGB", (32, 64), (60, 40, 20)).save(second)
        material_base = root / "stone_BaseColor.png"
        material_rough = root / "stone_Roughness.png"
        Image.new("RGB", (32, 32), (100, 80, 60)).save(material_base)
        Image.new("L", (32, 32), 128).save(material_rough)

        app = ToolBoxShell(discover())
        app.withdraw()
        try:
            app._select("image_rescale")
            panel = app._panels["image_rescale"]
            panel._add([first, second])
            job_id = _run_and_wait(app, panel, "Image Rescale", 2)

            app._select("icon_normalizer")
            icon_panel = app._panels["icon_normalizer"]
            icon_panel._add([first, second])
            icon_job_id = _run_and_wait(app, icon_panel, "Icon Normalizer", 2)

            app._select("pixel_art")
            pixel_panel = app._panels["pixel_art"]
            pixel_panel._add([first, second])
            pixel_job_id = _run_and_wait(app, pixel_panel, "Pixel Art Converter", 2)

            alpha_source = root / "alpha_subject.png"
            alpha_image = Image.new("RGB", (64, 64), (0, 255, 0))
            alpha_image.paste((220, 30, 30), (16, 16, 48, 48))
            alpha_image.save(alpha_source)
            app._select("alpha_doctor")
            alpha_panel = app._panels["alpha_doctor"]
            alpha_panel._add([alpha_source])
            alpha_panel._dry.deselect()
            alpha_out = root / "alpha_out"
            alpha_panel._out_entry.insert(0, str(alpha_out))
            alpha_job_id = _run_and_wait(app, alpha_panel, "Alpha Doctor", 1)
            alpha_output = alpha_out / "alpha_subject.png"
            assert alpha_output.is_file()
            assert (alpha_out / "cutout_manifest.csv").is_file()
            alpha_reuse_id = _run_and_wait(app, alpha_panel, "Alpha Doctor reuse", 1)
            assert alpha_reuse_id == alpha_job_id
            assert app._services.queue.completion(alpha_job_id).report.reused
            alpha_output.write_bytes(b"X" * alpha_output.stat().st_size)
            alpha_repair_id = _run_and_wait(app, alpha_panel, "Alpha Doctor repair", 1)
            assert alpha_repair_id == alpha_job_id
            assert not app._services.queue.completion(alpha_job_id).report.reused
            with Image.open(alpha_output) as repaired:
                repaired.load()
                assert repaired.mode == "RGBA"

            app._select("format_converter")
            format_panel = app._panels["format_converter"]
            format_panel._add([first])
            format_panel._target.set("jpg")
            format_panel._dry.deselect()
            format_out = root / "format_out"
            format_panel._out_entry.insert(0, str(format_out))
            format_job_id = _run_and_wait(
                app, format_panel, "Format Converter", 1
            )
            assert (format_out / "first.jpg").is_file()
            assert (format_out / "convert_manifest.csv").is_file()

            app._select("asset_auditor")
            audit_panel = app._panels["asset_auditor"]
            audit_panel._add([first, second])
            audit_out = root / "audit_out"
            audit_panel._out_entry.insert(0, str(audit_out))
            audit_job_id = _run_and_wait(app, audit_panel, "Asset Auditor", 1)
            assert (audit_out / "audit.html").is_file()
            assert (audit_out / "audit.json").is_file()
            assert (audit_out / "audit_issues.csv").is_file()
            audit_reuse_id = _run_and_wait(app, audit_panel, "Asset Auditor reuse", 1)
            assert audit_reuse_id == audit_job_id
            assert app._services.queue.completion(audit_job_id).report.reused
            audit_html = audit_out / "audit.html"
            audit_html.write_bytes(b"X" * audit_html.stat().st_size)
            audit_repair_id = _run_and_wait(app, audit_panel, "Asset Auditor repair", 1)
            assert audit_repair_id == audit_job_id
            assert not app._services.queue.completion(audit_job_id).report.reused
            assert b"<html" in audit_html.read_bytes().lower()

            dataset_src = root / "dataset_src"
            dataset_src.mkdir()
            dataset_a, dataset_b = dataset_src / "ds_a.png", dataset_src / "ds_b.png"
            Image.new("RGB", (48, 48), (120, 30, 30)).save(dataset_a)
            Image.new("RGB", (64, 48), (30, 120, 30)).save(dataset_b)
            (dataset_src / "ds_a.txt").write_text("red subject", encoding="utf-8")
            (dataset_src / "ds_b.txt").write_text("green subject", encoding="utf-8")
            app._select("dataset_manager")
            dataset_panel = app._panels["dataset_manager"]
            dataset_panel._add([dataset_a, dataset_b])
            dataset_panel._op.set("Train / val / test split")
            dataset_panel._on_op_change("Train / val / test split")
            dataset_panel._dry.deselect()
            dataset_out = root / "dataset_out"
            dataset_panel._out_entry.insert(0, str(dataset_out))
            dataset_job_id = _run_and_wait(app, dataset_panel, "Dataset Manager", 1)
            dataset_marker = dataset_out / "dataset_provenance.json"
            assert dataset_marker.is_file()
            assert len(list(dataset_out.rglob("ds_*.png"))) == 2
            dataset_reuse_id = _run_and_wait(app, dataset_panel, "Dataset Manager reuse", 1)
            assert dataset_reuse_id == dataset_job_id
            assert app._services.queue.completion(dataset_job_id).report.reused
            dataset_victim = next(dataset_out.rglob("ds_*.png"))
            dataset_victim.write_bytes(b"X" * dataset_victim.stat().st_size)
            dataset_repair_id = _run_and_wait(app, dataset_panel, "Dataset Manager repair", 1)
            assert dataset_repair_id == dataset_job_id
            assert not app._services.queue.completion(dataset_job_id).report.reused
            assert dataset_victim.read_bytes() != b"X" * dataset_victim.stat().st_size

            app._select("material_converter")
            material_panel = app._panels["material_converter"]
            material_panel._add([material_base, material_rough])
            material_job_id = _run_and_wait(app, material_panel, "Material Converter", 1)

            renderer_input = root / "renderer_input"
            renderer_input.mkdir()
            renderer_project = renderer_input / "stone.sbsar"
            renderer_project.write_bytes(b"stub substance archive")
            renderer_engine = root / (
                "sbsrender.exe" if os.name == "nt" else "sbsrender"
            )
            renderer_engine.write_bytes(b"stub renderer executable")
            renderer_out = root / "renderer_out"
            app._select("texture_renderer")
            renderer_panel = app._panels["texture_renderer"]
            for entry, value in (
                (renderer_panel._sbs_engine, renderer_engine),
                (renderer_panel._sbs_input, renderer_input),
                (renderer_panel._sbs_output, renderer_out),
            ):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            renderer_panel._sbs_dry.deselect()

            from tools.texture_renderer import engine as renderer_module
            from tools.texture_renderer import panel as renderer_panel_module

            renderer_calls = 0
            original_renderer_run = renderer_module._run
            original_confirm = renderer_panel_module.messagebox.askyesno

            def fake_renderer(command, **_kwargs):
                nonlocal renderer_calls
                renderer_calls += 1
                stage = Path(command[command.index("--output-path") + 1])
                (stage / "stone_basecolor.tga").write_bytes(b"rendered texture")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            renderer_module._run = fake_renderer
            renderer_panel_module.messagebox.askyesno = lambda *_args, **_kwargs: True
            try:
                renderer_job_id = _run_and_wait(
                    app, renderer_panel, "Texture Renderer", 1,
                    start=renderer_panel._start,
                )
                renderer_output = (
                    renderer_out / renderer_project.stem / "stone_basecolor.tga"
                )
                assert renderer_output.read_bytes() == b"rendered texture"
                renderer_manifest = next(
                    iter(renderer_out.glob("texture_render_manifest_*.json"))
                )
                renderer_manifest.unlink()
                renderer_reuse_id = _run_and_wait(
                    app, renderer_panel, "Texture Renderer reuse", 1,
                    start=renderer_panel._start,
                )
                assert renderer_reuse_id == renderer_job_id
                assert app._services.queue.completion(renderer_job_id).report.reused
                assert renderer_calls == 1
                assert renderer_manifest.is_file(), "reuse must repair provenance"
                renderer_output.write_bytes(b"X" * renderer_output.stat().st_size)
                renderer_repair_id = _run_and_wait(
                    app, renderer_panel, "Texture Renderer repair", 1,
                    start=renderer_panel._start,
                )
                assert renderer_repair_id == renderer_job_id
                assert not app._services.queue.completion(renderer_job_id).report.reused
                assert renderer_calls == 2
                assert renderer_output.read_bytes() == b"rendered texture"
            finally:
                renderer_module._run = original_renderer_run
                renderer_panel_module.messagebox.askyesno = original_confirm

            svg_out = root / "svg_out"
            app._select("to_svg")
            svg_panel = app._panels["to_svg"]
            svg_panel._add([first])
            svg_panel._dry.deselect()
            svg_panel._out_entry.insert(0, str(svg_out))

            from tools.to_svg import engine as svg_module
            from tools.to_svg import panel as svg_panel_module

            svg_calls = 0
            original_vtracer = sys.modules.get("vtracer")
            original_find_spec = svg_panel_module.importlib.util.find_spec
            original_svg_confirm = svg_panel_module.messagebox.askyesno

            def fake_vtracer(_source, destination, **_kwargs):
                nonlocal svg_calls
                svg_calls += 1
                Path(destination).write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg">'
                    '<path d="M0 0 L1 0 L1 1 Z"/></svg>',
                    encoding="utf-8",
                )

            sys.modules["vtracer"] = SimpleNamespace(
                convert_image_to_svg_py=fake_vtracer
            )
            svg_panel_module.importlib.util.find_spec = lambda _name: object()
            svg_panel_module.messagebox.askyesno = lambda *_args, **_kwargs: True
            try:
                svg_job_id = _run_and_wait(app, svg_panel, "To SVG", 1)
                svg_output = svg_out / "first.svg"
                assert svg_output.is_file()
                assert (svg_out / "svg_manifest.csv").is_file()
                svg_reuse_id = _run_and_wait(app, svg_panel, "To SVG reuse", 1)
                assert svg_reuse_id == svg_job_id
                assert app._services.queue.completion(svg_job_id).report.reused
                assert svg_calls == 1
                svg_output.write_bytes(b"X" * svg_output.stat().st_size)
                svg_repair_id = _run_and_wait(app, svg_panel, "To SVG repair", 1)
                assert svg_repair_id == svg_job_id
                assert not app._services.queue.completion(svg_job_id).report.reused
                assert svg_calls == 2
                assert b"<svg" in svg_output.read_bytes()
            finally:
                if original_vtracer is None:
                    sys.modules.pop("vtracer", None)
                else:
                    sys.modules["vtracer"] = original_vtracer
                svg_panel_module.importlib.util.find_spec = original_find_spec
                svg_panel_module.messagebox.askyesno = original_svg_confirm

            app._select("showcase")
            showcase_panel = app._panels["showcase"]
            showcase_panel._add([first, second])
            showcase_job_id = _run_and_wait(app, showcase_panel, "Showcase Contact Sheet", 1)

            app._select("tileset_checker")
            tileset_panel = app._panels["tileset_checker"]
            tileset_panel._add([first, second])
            tileset_job_id = _run_and_wait(app, tileset_panel, "Tileset Checker", 2)

            package = root / "bundle.zip"
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("Assets/readme.txt", b"queue extraction")
            app._select("package_extractor")
            package_panel = app._panels["package_extractor"]
            package_panel._add([package])
            package_panel._dry.deselect()
            package_out = root / "package_out"
            package_panel._out_entry.insert(0, str(package_out))
            package_job_id = _run_and_wait(
                app, package_panel, "Package Extractor", 1
            )
            assert (
                package_out / "bundle" / "Assets" / "readme.txt"
            ).read_bytes() == b"queue extraction"
            assert (package_out / "bundle" / "_extract_report.json").is_file()
            assert (package_out / "extract_manifest.csv").is_file()

            audio_job_id = None
            chopper_job_id = None
            video_job_id = None
            ffmpeg = resolve_tool("ffmpeg")
            if ffmpeg:
                sample = root / "sample.mp4"
                made = run_cmd([
                    ffmpeg, "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=15:duration=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(sample),
                ], timeout=30)
                assert made.returncode == 0 and sample.is_file(), made.stderr[-300:]
                app._select("video_compressor")
                video_panel = app._panels["video_compressor"]
                video_panel._add([sample])
                video_job_id = _run_and_wait(app, video_panel, "Video Compressor", 1)

                tone = root / "tone.wav"
                made_audio = run_cmd([
                    ffmpeg, "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.3", str(tone),
                ], timeout=30)
                assert made_audio.returncode == 0 and tone.is_file(), made_audio.stderr[-300:]
                app._select("audio_tool")
                audio_panel = app._panels["audio_tool"]
                audio_panel._add([tone])
                audio_panel._dry.deselect()
                audio_out = root / "audio_out"
                audio_panel._out_entry.insert(0, str(audio_out))
                audio_job_id = _run_and_wait(app, audio_panel, "Audio Tool", 1)
                assert (audio_out / "tone.mp3").is_file()
                assert (audio_out / "audio_manifest.csv").is_file()
                duplicate_tone = root / "duplicate" / tone.name
                duplicate_tone.parent.mkdir()
                duplicate_tone.write_bytes(tone.read_bytes())
                audio_panel._add([duplicate_tone])
                audio_panel._mirror.deselect()
                assert not audio_panel._pre_run_check(audio_panel._collect_options())

                chop_sample = root / "chop_sample.mp4"
                made_chop = run_cmd([
                    ffmpeg, "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=0.4",
                    "-f", "lavfi", "-i", "color=c=black:size=160x120:rate=15:duration=0.3",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=0.4",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                    "-map", "[v]", "-pix_fmt", "yuv420p", str(chop_sample),
                ], timeout=30)
                assert made_chop.returncode == 0 and chop_sample.is_file(), made_chop.stderr[-300:]
                app._select("video_chopper")
                chopper_panel = app._panels["video_chopper"]
                chopper_panel._add([chop_sample])
                chopper_panel._dry.deselect()
                chopper_panel._min_clip.delete(0, "end")
                chopper_panel._min_clip.insert(0, "0.2")
                chop_out = root / "chop_out"
                chopper_panel._out_entry.insert(0, str(chop_out))
                chopper_job_id = _run_and_wait(app, chopper_panel, "Video Chopper", 1)
                assert len(list(chop_out.rglob("*_clip_*.mp4"))) == 2
                assert (chop_out / "chop_manifest.csv").is_file()

            app._select(app.QUEUE_ID)
            app.update()
            queue_panel = app._panels[app.QUEUE_ID]
            assert isinstance(queue_panel, QueuePanel)
            assert any(item.job_id == job_id for item in app._services.queue.history())
            assert any(item.job_id == icon_job_id for item in app._services.queue.history())
            assert any(item.job_id == pixel_job_id for item in app._services.queue.history())
            assert any(item.job_id == alpha_job_id for item in app._services.queue.history())
            assert any(item.job_id == format_job_id for item in app._services.queue.history())
            assert any(item.job_id == audit_job_id for item in app._services.queue.history())
            assert any(item.job_id == dataset_job_id for item in app._services.queue.history())
            assert any(item.job_id == material_job_id for item in app._services.queue.history())
            assert any(item.job_id == renderer_job_id for item in app._services.queue.history())
            assert any(item.job_id == svg_job_id for item in app._services.queue.history())
            assert any(item.job_id == showcase_job_id for item in app._services.queue.history())
            assert any(item.job_id == tileset_job_id for item in app._services.queue.history())
            assert any(item.job_id == package_job_id for item in app._services.queue.history())
            if audio_job_id:
                assert any(item.job_id == audio_job_id for item in app._services.queue.history())
            if chopper_job_id:
                assert any(item.job_id == chopper_job_id for item in app._services.queue.history())
            if video_job_id:
                assert any(item.job_id == video_job_id for item in app._services.queue.history())
        finally:
            app._on_close()

    print("PASS: CustomTkinter submitted Image Rescale, Icon Normalizer, Pixel Art, Alpha Doctor, "
          "Format Converter, Asset Auditor, Dataset Manager, Material Converter, Texture Renderer, "
          "To SVG, Showcase, Tileset Checker, Package "
          "Extractor, Audio Tool, Video Compressor, and Video Chopper through the shell "
          "queue; history UI rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

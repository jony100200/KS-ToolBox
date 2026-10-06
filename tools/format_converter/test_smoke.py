"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Five legs, each skipping cleanly if its dependency is absent:
  * dispatch/kinds  — pure, no deps.
  * images          — Pillow: png -> jpg (flatten), ico, webp.
  * audio/video     — bundled ffmpeg: mp4 -> mp3 (extract) and mp4 -> gif.
  * documents       — markdown: md -> html.
  * PDF page/text   — pypdfium2: two-page PDF -> atomic page set + streamed txt + md.

Run standalone:  python -m tools.format_converter.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.format_converter import engine as e  # noqa: E402
from toolbox.engine_common import run_cancellable_cmd  # noqa: E402


def test_dispatch() -> None:
    assert e.source_kind(".PNG") == "image"
    assert e.source_kind(".mp4") == "video"
    assert e.source_kind(".gif") == "gif"
    assert e.source_kind(".md") == "markdown"
    assert e.source_kind(".xyz") == ""
    assert "png" in e.targets_for("image") and "pdf" in e.targets_for("markdown")
    assert "mp3" in e.targets_for("video") and "gif" in e.targets_for("video")
    assert ("pdf", "png") in e.DISPATCH and ("docx", "pdf") in e.DISPATCH
    print("PASS: dispatch/kinds — routing table wired for all four families.")


def test_images() -> None:
    if importlib.util.find_spec("PIL") is None:
        print("SKIP: Pillow not installed — image leg skipped."); return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"
        src = tmp / "pic.png"
        img = Image.new("RGBA", (64, 48), (200, 40, 40, 128)); img.save(src)
        for target, checkmode in (("jpg", "RGB"), ("ico", None), ("webp", None)):
            opts = e.ConvertOptions(target=target, out_root=out, dry_run=False)
            res = e.process(src, opts)
            assert res.action == "converted", f"{target}: {res.reason}"
            assert Path(res.out_path).is_file(), f"{target}: no output"
            assert len(res.artifacts) == 1 and e.validate_result(res, opts)
            if checkmode:
                with Image.open(res.out_path) as got:
                    assert got.mode == checkmode, f"{target}: mode {got.mode}"

        jpg_opts = e.ConvertOptions(target="jpg", out_root=out, dry_run=False)
        jpg = e.process(src, jpg_opts)
        artifact = Path(jpg.out_path)
        original = artifact.read_bytes()
        artifact.write_bytes(b"X" * len(original))
        assert not e.validate_result(jpg, jpg_opts), "same-size corruption must invalidate reuse"

        preview_opts = e.ConvertOptions(target="png", out_root=tmp / "preview", dry_run=True)
        preview = e.process(src, preview_opts)
        assert preview.action == "dry-run" and e.validate_result(preview, preview_opts)

        cancel_out = tmp / "cancel"
        try:
            e.process(
                src, e.ConvertOptions(target="jpg", out_root=cancel_out, dry_run=False),
                cancelled=lambda: True,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("image cancellation did not propagate")
        assert not cancel_out.exists(), "cancelled conversion wrote output"

        other = tmp / "other" / src.name
        other.parent.mkdir()
        other.write_bytes(src.read_bytes())
        assert len(e.find_output_collisions([src, other], jpg_opts)) == 1
        invalid = e.process(src, e.ConvertOptions(target="jpg", quality=0, dry_run=True))
        assert invalid.action == "failed" and invalid.detail == "bad.options"
        assert not list(tmp.rglob("*.part.*")), "image conversion leaked a staged file"

        # Exercise exact directory/page-set validation without optional PDFium.
        pdf = tmp / "pages.pdf"; pdf.write_bytes(b"%PDF-fake")
        page_opts = e.ConvertOptions(target="png", out_root=out, dry_run=False)
        page_dir = e.plan_output(pdf, page_opts); page_dir.mkdir()
        for index in (1, 2):
            Image.new("RGB", (8, 8), (index, 0, 0)).save(
                page_dir / f"page_{index:03d}.png"
            )
        inspected = e._inspect_output(page_dir, page_dir, page_opts, {"pages": 2})
        assert not inspected["error"]
        page_result = e.Result(
            str(pdf), "converted", "pdf → png", before="pdf", after="png",
            out_path=str(page_dir), artifacts=inspected["data"], detail="2 pages",
        )
        assert e.validate_result(page_result, page_opts)
        Image.new("RGB", (8, 8)).save(page_dir / "page_003.png")
        assert not e.validate_result(page_result, page_opts), "stale page must reject reuse"
    print("PASS: images — png -> jpg (flattened), ico, webp.")


def test_ffmpeg() -> None:
    if not e.resolve_tool("ffmpeg"):
        print("SKIP: ffmpeg not found — audio/video leg skipped."); return
    ff = e.resolve_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"
        src = tmp / "clip.mp4"
        make = [ff, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-c:a", "aac", "-shortest", "-pix_fmt", "yuv420p", str(src)]
        if run_cancellable_cmd(make, timeout=120).returncode != 0:
            print("SKIP: could not synthesize a sample with this ffmpeg."); return
        for target in ("mp3", "gif"):
            opts = e.ConvertOptions(target=target, out_root=out, dry_run=False)
            res = e.process(src, opts)
            assert res.action == "converted", f"{target}: {res.reason}"
            assert Path(res.out_path).is_file() and Path(res.out_path).stat().st_size > 0, f"{target}: empty"
            assert e.validate_result(res, opts), f"{target}: stored artifact invalid"
        try:
            e.process(
                src, e.ConvertOptions(target="mp3", out_root=tmp / "cancel", dry_run=False),
                cancelled=lambda: True,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("ffmpeg cancellation did not propagate")
        assert not list((tmp / "cancel").rglob("*")), "cancelled FFmpeg left artifacts"
        assert not list(tmp.rglob("*.part.*")), "media conversion leaked a staged file"
    print("PASS: audio/video — mp4 -> mp3 (extract) and mp4 -> gif.")


def test_docs() -> None:
    if importlib.util.find_spec("markdown") is None:
        print("SKIP: 'markdown' not installed — document leg skipped."); return
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"
        src = tmp / "note.md"
        src.write_text("# Title\n\n- a\n- b\n\n`code`\n", encoding="utf-8")
        res = e.process(src, e.ConvertOptions(target="html", out_root=out, dry_run=False))
        assert res.action == "converted", res.reason
        html = Path(res.out_path).read_text(encoding="utf-8")
        assert "<h1>" in html and "<li>" in html, "markdown not rendered"
        assert e.validate_result(
            res, e.ConvertOptions(target="html", out_root=out, dry_run=False)
        )
    print("PASS: documents — md -> html.")


def test_pdf_pages() -> None:
    if importlib.util.find_spec("pypdfium2") is None:
        print("SKIP: 'pypdfium2' not installed — PDF page-set leg skipped."); return
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"; src = tmp / "pages.pdf"
        first = Image.new("RGB", (32, 24), "red")
        second = Image.new("RGB", (32, 24), "blue")
        first.save(src, "PDF", save_all=True, append_images=[second])
        first.close(); second.close()
        opts = e.ConvertOptions(target="png", out_root=out, dry_run=False, dpi=72)
        result = e.process(src, opts)
        assert result.action == "converted", result.reason
        assert len(result.artifacts) == 2 and e.validate_result(result, opts)
        assert sorted(path.name for path in Path(result.out_path).iterdir()) == [
            "page_001.png", "page_002.png",
        ]
        rerun = e.process(src, opts)
        assert rerun.action == "failed" and rerun.detail == "output.exists"
        assert e.validate_result(result, opts), "existing page set was modified"
        text_opts = e.ConvertOptions(target="txt", out_root=tmp / "text", dry_run=False)
        text_result = e.process(src, text_opts)
        assert text_result.action == "converted", text_result.reason
        assert Path(text_result.out_path).is_file()
        assert e.validate_result(text_result, text_opts)
        md_opts = e.ConvertOptions(target="md", out_root=tmp / "md", dry_run=False)
        md_result = e.process(src, md_opts)
        assert md_result.action == "converted", md_result.reason
        assert e.validate_result(md_result, md_opts)
        markdown = Path(md_result.out_path).read_text(encoding="utf-8")
        assert "Total Pages" in markdown and "Page 1" in markdown, "pdf -> md structure missing"
    print("PASS: PDF — two-page render, streamed text, and Markdown output validated.")


def main() -> int:
    test_dispatch()
    test_images()
    test_ffmpeg()
    test_docs()
    test_pdf_pages()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

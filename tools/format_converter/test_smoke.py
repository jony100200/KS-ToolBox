"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Four legs, each skipping cleanly if its dependency is absent:
  * dispatch/kinds  — pure, no deps.
  * images          — Pillow: png -> jpg (flatten), ico, webp.
  * audio/video     — bundled ffmpeg: mp4 -> mp3 (extract) and mp4 -> gif.
  * documents       — markdown: md -> html.

Run standalone:  python -m tools.format_converter.test_smoke
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.format_converter import engine as e  # noqa: E402


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
            res = e.process(src, e.ConvertOptions(target=target, out_root=out, dry_run=False))
            assert res.action == "converted", f"{target}: {res.reason}"
            assert Path(res.out_path).is_file(), f"{target}: no output"
            if checkmode:
                with Image.open(res.out_path) as got:
                    assert got.mode == checkmode, f"{target}: mode {got.mode}"
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
        if e.run_cmd(make, timeout=120).returncode != 0:
            print("SKIP: could not synthesize a sample with this ffmpeg."); return
        for target in ("mp3", "gif"):
            res = e.process(src, e.ConvertOptions(target=target, out_root=out, dry_run=False))
            assert res.action == "converted", f"{target}: {res.reason}"
            assert Path(res.out_path).is_file() and Path(res.out_path).stat().st_size > 0, f"{target}: empty"
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
    print("PASS: documents — md -> html.")


def main() -> int:
    test_dispatch()
    test_images()
    test_ffmpeg()
    test_docs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

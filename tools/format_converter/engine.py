"""Format Converter engine — convert files between formats across four families.

Pure logic, no UI, no global state. Cross-platform. Routing is a dispatch table
keyed on (source_kind, target); each converter returns the standard error
envelope. Heavy/optional deps are imported LAZILY inside their converter and
degrade with a clear "install X" message — the app never crashes for a missing
document library.

Families & engines:
  images       — Pillow (already shipped)
  audio/video  — the bundled ffmpeg (resolved via engine_common.resolve_tool)
  documents    — markdown / xhtml2pdf / mammoth / pypdfium2 (optional, lazy)

Public interface:
    source_kind(ext)          -> str
    targets_for(kind)         -> list[str]        (panel builds its dropdown from this)
    convert(src, dst, opts)   -> envelope         (low-level: dispatch lookup)
    process(path, opts)       -> Result           (orchestrator: kind -> plan -> convert)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

from toolbox.engine_common import ok, err, resolve_tool, run_cmd

# --- kinds --------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".ico"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus"}
MARKDOWN_EXTS = {".md", ".markdown"}
HTML_EXTS = {".html", ".htm"}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
# .gif is special-cased: image OR video depending on the chosen target.

# Every extension the tool will accept as input.
ALL_EXTS = (IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | MARKDOWN_EXTS
            | HTML_EXTS | DOCX_EXTS | PDF_EXTS | {".gif"})


def source_kind(ext: str) -> str:
    ext = ext.lower()
    if ext == ".gif":
        return "gif"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in MARKDOWN_EXTS:
        return "markdown"
    if ext in HTML_EXTS:
        return "html"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in PDF_EXTS:
        return "pdf"
    return ""


@dataclass
class ConvertOptions:
    target: str                        # target token: "png","mp4","pdf","html","txt"...
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    dry_run: bool = True
    # image
    quality: int = 90                  # jpg / webp
    flatten_bg: str = "white"          # background when target lacks alpha
    # audio / video
    audio_bitrate: str = "192k"
    fps: int = 0                       # 0 = keep source (video->gif uses 12 then)
    gif_width: int = 480               # scale for video->gif
    # documents
    dpi: int = 150                     # pdf->image render density
    extra_css: str = ""                # md/html/docx -> pdf styling override


@dataclass
class Result:
    src: str
    action: str                        # converted | skipped | failed | dry-run
    reason: str
    before: str = ""                   # source kind
    after: str = ""                    # target token
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- atomic write helpers -----------------------------------------------------

def _tmp_for(dst: Path) -> Path:
    """`name.part.ext` temp so ffmpeg/Pillow can still infer the format."""
    return dst.with_name(f"{dst.stem}.part{dst.suffix}")


def _write_text_atomic(dst: Path, text: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dst)


# --- image converters (Pillow) ------------------------------------------------

def _img_convert(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    try:
        from PIL import Image
    except ImportError:
        return err("dep.missing", "image conversion needs Pillow — pip install pillow")
    tgt = dst.suffix.lower()
    try:
        with Image.open(src) as im:
            animated = getattr(im, "n_frames", 1) > 1
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = _tmp_for(dst)

            if tgt == ".ico":
                im.convert("RGBA").save(tmp, format="ICO",
                                        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
                tmp.replace(dst)
                return ok({"note": "multi-size ico"})

            # Animated source -> animated target (gif/webp): keep the frames.
            if animated and tgt in (".gif", ".webp"):
                frames = []
                try:
                    for i in range(im.n_frames):
                        im.seek(i)
                        frames.append(im.convert("RGBA").copy())
                finally:
                    im.seek(0)
                dur = im.info.get("duration", 100)
                frames[0].save(tmp, save_all=True, append_images=frames[1:],
                               loop=im.info.get("loop", 0), duration=dur, disposal=2)
                tmp.replace(dst)
                return ok({"frames": len(frames)})

            # Still image (or first frame of an animation).
            img = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
            note = "first frame only" if animated else ""
            if tgt in (".jpg", ".jpeg", ".bmp") and img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, opts.flatten_bg)
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])   # composite over the flat colour
                img = bg
            save_kw: dict = {}
            if tgt in (".jpg", ".jpeg", ".webp"):
                save_kw["quality"] = opts.quality
            img.save(tmp, **save_kw)
            tmp.replace(dst)
            return ok({"note": note} if note else None, details=note)
    except Exception as ex:                             # PIL raises many types
        return err("image.failed", f"{src.name}: {ex}")


# --- ffmpeg converters (bundled binary) ---------------------------------------

_MUXER = {".mp4": "mp4", ".mov": "mov", ".mkv": "matroska", ".webm": "webm",
          ".gif": "gif", ".mp3": "mp3", ".wav": "wav", ".flac": "flac",
          ".aac": "adts", ".m4a": "ipod", ".ogg": "ogg", ".opus": "opus"}


def _ff_run(src: Path, dst: Path, mid_args: list[str]) -> dict:
    """Run `ffmpeg -y -i SRC <mid_args> -f <muxer> TMP` then atomic-replace."""
    ff = resolve_tool("ffmpeg")
    if not ff:
        return err("dep.missing", "audio/video conversion needs ffmpeg (bundle a bin/ or install it)")
    muxer = _MUXER.get(dst.suffix.lower())
    if not muxer:
        return err("unsupported.target", f"no ffmpeg muxer for {dst.suffix}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    cmd = [ff, "-y", "-i", str(src), *mid_args, "-f", muxer, str(tmp)]
    try:
        r = run_cmd(cmd, timeout=3600)
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return err("ff.error", f"ffmpeg error on {src.name}: {ex}", retryable=True)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return err("ff.failed", f"ffmpeg failed: {(r.stderr or '')[-300:]}")
    tmp.replace(dst)
    return ok()


def _audio_codec(target_ext: str, bitrate: str) -> list[str]:
    return {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", bitrate],
        ".wav": ["-c:a", "pcm_s16le"],
        ".flac": ["-c:a", "flac"],
        ".aac": ["-c:a", "aac", "-b:a", bitrate],
        ".m4a": ["-c:a", "aac", "-b:a", bitrate],
        ".ogg": ["-c:a", "libvorbis", "-b:a", bitrate],
        ".opus": ["-c:a", "libopus", "-b:a", bitrate],
    }[target_ext]


def _ff_video(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    tgt = dst.suffix.lower()
    if tgt == ".webm":
        args = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"]
    else:  # mp4 / mov / mkv
        args = ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", opts.audio_bitrate]
        if tgt in (".mp4", ".mov"):
            args += ["-movflags", "+faststart"]
    return _ff_run(src, dst, args)


def _ff_video_to_gif(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    fps = opts.fps or 12
    vf = (f"fps={fps},scale={opts.gif_width}:-1:flags=lanczos,"
          f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
    return _ff_run(src, dst, ["-vf", vf])


def _ff_extract_audio(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    return _ff_run(src, dst, ["-vn", *_audio_codec(dst.suffix.lower(), opts.audio_bitrate)])


def _ff_audio(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    return _ff_run(src, dst, _audio_codec(dst.suffix.lower(), opts.audio_bitrate))


# --- document converters (optional, lazy) -------------------------------------

_DEFAULT_CSS = """
@page { size: A4; margin: 2cm; }
body { font-family: 'Helvetica','Arial',sans-serif; font-size: 11pt; line-height: 1.5; color: #111; }
h1,h2,h3 { font-family: 'Helvetica','Arial',sans-serif; color: #000; }
code,pre { font-family: 'Courier New',monospace; background: #f4f4f4; }
pre { padding: 8px; white-space: pre-wrap; }
table { border-collapse: collapse; } td,th { border: 1px solid #999; padding: 4px 8px; }
img { max-width: 100%; }
"""


def _wrap_html(body: str, extra_css: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{_DEFAULT_CSS}\n{extra_css}</style></head><body>{body}</body></html>")


def _md_to_html_str(src: Path, opts: ConvertOptions) -> tuple[str | None, dict | None]:
    try:
        import markdown
    except ImportError:
        return None, err("dep.missing", "Markdown conversion needs 'markdown' — pip install markdown")
    body = markdown.markdown(src.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "codehilite", "sane_lists"])
    return _wrap_html(body, opts.extra_css), None


def _docx_to_html_str(src: Path, opts: ConvertOptions) -> tuple[str | None, dict | None]:
    try:
        import mammoth
    except ImportError:
        return None, err("dep.missing", "DOCX conversion needs 'mammoth' — pip install mammoth")
    with open(src, "rb") as f:
        body = mammoth.convert_to_html(f).value
    return _wrap_html(body, opts.extra_css), None


def _html_str_to_pdf(html: str, dst: Path) -> dict:
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return err("dep.missing", "PDF output needs 'xhtml2pdf' — pip install xhtml2pdf")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    try:
        with open(tmp, "wb") as out:
            status = pisa.CreatePDF(html, dest=out, encoding="utf-8")
    except Exception as ex:
        tmp.unlink(missing_ok=True)
        return err("pdf.failed", f"PDF render error: {ex}")
    if status.err or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return err("pdf.failed", "PDF render reported errors")
    tmp.replace(dst)
    return ok()


def _md_to_html(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    html, e = _md_to_html_str(src, opts)
    if e:
        return e
    _write_text_atomic(dst, html)
    return ok()


def _md_to_pdf(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    html, e = _md_to_html_str(src, opts)
    return e if e else _html_str_to_pdf(html, dst)


def _html_to_pdf(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    return _html_str_to_pdf(src.read_text(encoding="utf-8"), dst)


def _docx_to_html(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    html, e = _docx_to_html_str(src, opts)
    if e:
        return e
    _write_text_atomic(dst, html)
    return ok()


def _docx_to_pdf(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    html, e = _docx_to_html_str(src, opts)
    return e if e else _html_str_to_pdf(html, dst)


def _pdf_to_images(src: Path, dst_dir: Path, opts: ConvertOptions) -> dict:
    """Render each PDF page to an image. `dst_dir` is a directory; pages land as
    page_001.<ext> inside. Streams page-by-page (never holds all pixmaps)."""
    try:
        import pypdfium2 as pdfium
        from PIL import Image  # noqa: F401 (pdfium renders to PIL)
    except ImportError:
        return err("dep.missing", "PDF→image needs 'pypdfium2' + Pillow — pip install pypdfium2 pillow")
    ext = "png" if opts.target == "png" else "jpg"
    scale = max(0.1, opts.dpi / 72.0)
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf = pdfium.PdfDocument(str(src))
        n = len(pdf)
        for i in range(n):
            page = pdf[i]
            pil = page.render(scale=scale).to_pil()
            if ext == "jpg" and pil.mode in ("RGBA", "P", "LA"):
                pil = pil.convert("RGB")
            out = dst_dir / f"page_{i + 1:03d}.{ext}"
            tmp = _tmp_for(out)
            pil.save(tmp)
            tmp.replace(out)
        pdf.close()
    except Exception as ex:
        return err("pdf.failed", f"{src.name}: {ex}")
    return ok({"pages": n})


def _pdf_to_text(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return err("dep.missing", "PDF→text needs 'pypdfium2' — pip install pypdfium2")
    try:
        pdf = pdfium.PdfDocument(str(src))
        chunks = []
        for i in range(len(pdf)):
            tp = pdf[i].get_textpage()
            chunks.append(tp.get_text_range())
        pdf.close()
    except Exception as ex:
        return err("pdf.failed", f"{src.name}: {ex}")
    _write_text_atomic(dst, "\n\n".join(chunks))
    return ok()


# --- dispatch -----------------------------------------------------------------

DISPATCH: dict[tuple[str, str], Callable[[Path, Path, ConvertOptions], dict]] = {}


def _register(kinds: list[str], targets: list[str], fn) -> None:
    for k in kinds:
        for tgt in targets:
            DISPATCH[(k, tgt)] = fn


_IMG_TARGETS = ["png", "jpg", "webp", "bmp", "tiff", "ico"]
_register(["image", "gif"], _IMG_TARGETS, _img_convert)
_register(["gif"], ["png", "jpg", "webp"], _img_convert)          # gif still-frame path
_register(["gif", "video"], ["mp4", "mov", "mkv", "webm"], _ff_video)
_register(["video"], ["gif"], _ff_video_to_gif)
_register(["video"], ["mp3", "wav", "aac", "m4a", "flac"], _ff_extract_audio)
_register(["audio"], ["mp3", "wav", "flac", "aac", "m4a", "ogg", "opus"], _ff_audio)
DISPATCH[("markdown", "html")] = _md_to_html
DISPATCH[("markdown", "pdf")] = _md_to_pdf
DISPATCH[("html", "pdf")] = _html_to_pdf
DISPATCH[("docx", "html")] = _docx_to_html
DISPATCH[("docx", "pdf")] = _docx_to_pdf
DISPATCH[("pdf", "png")] = _pdf_to_images
DISPATCH[("pdf", "jpg")] = _pdf_to_images
DISPATCH[("pdf", "txt")] = _pdf_to_text

# targets that produce a directory of files rather than a single file
_DIR_OUTPUT = {("pdf", "png"), ("pdf", "jpg")}


def targets_for(kind: str) -> list[str]:
    """Valid target tokens for a source kind — the panel builds its dropdown from this."""
    seen: list[str] = []
    for (k, tgt) in DISPATCH.keys():
        if k == kind and tgt not in seen:
            seen.append(tgt)
    return sorted(seen)


# --- process ------------------------------------------------------------------

def plan_output(src: Path, opts: ConvertOptions) -> Path:
    """Where the converted file (or, for pdf→images, the page directory) goes.
    Mirror preserves the input subtree; else flat under out_root; else a
    `converted` folder beside the source."""
    src = Path(src)
    kind = source_kind(src.suffix)
    is_dir = (kind, opts.target) in _DIR_OUTPUT
    name = src.stem if is_dir else f"{src.stem}.{opts.target}"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "converted" / name


def convert(src: Path, dst: Path, opts: ConvertOptions) -> dict:
    """Low-level: look up the (kind, target) converter and run it. Envelope out."""
    kind = source_kind(Path(src).suffix)
    fn = DISPATCH.get((kind, opts.target))
    if fn is None:
        return err("unsupported.pair", f"cannot convert {kind or '?'} → {opts.target}")
    return fn(Path(src), Path(dst), opts)


def process(path: str | Path, opts: ConvertOptions) -> Result:
    """detect kind → plan output → (dry-run report | convert), for one file."""
    src = Path(path)
    kind = source_kind(src.suffix)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if not kind:
        return Result(str(src), "failed", f"unsupported input type: {src.suffix}", detail="unsupported.source")
    if (kind, opts.target) not in DISPATCH:
        return Result(str(src), "failed", f"cannot convert {kind} → {opts.target}",
                      before=kind, after=opts.target, detail="unsupported.pair")

    dst = plan_output(src, opts)
    if src.resolve() == dst.resolve():
        return Result(str(src), "skipped", "source is already the target format",
                      before=kind, after=opts.target)
    if opts.dry_run:
        return Result(str(src), "dry-run", f"would convert {kind} → {opts.target}",
                      before=kind, after=opts.target, out_path=str(dst))

    res = convert(src, dst, opts)
    if res["error"]:
        return Result(str(src), "failed", res["details"], before=kind, after=opts.target,
                      detail=res["error_type"])
    detail = res.get("details") or ""
    data = res.get("data") or {}
    if "pages" in data:
        detail = f"{data['pages']} pages"
    elif "frames" in data:
        detail = f"{data['frames']} frames"
    return Result(str(src), "converted", f"{kind} → {opts.target}", before=kind,
                  after=opts.target, out_path=str(dst), detail=detail)

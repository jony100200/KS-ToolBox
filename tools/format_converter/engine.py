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
    find_output_collisions(paths, opts) -> dict   (preflight shared destinations)
    convert(src, dst, opts, cancelled)  -> envelope (low-level dispatch)
    process(path, opts, cancelled)      -> Result (stage, validate, commit)
    validate_result(result, opts)       -> bool   (exact recovery/reuse gate)
"""
from __future__ import annotations

import codecs
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    err,
    find_output_collisions as _find_collisions,
    ok,
    probe_media_duration,
    resolve_tool,
    run_cancellable_cmd,
    sha256_file,
)

# --- kinds --------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".ico"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus"}
MARKDOWN_EXTS = {".md", ".markdown"}
HTML_EXTS = {".html", ".htm"}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
# .gif is special-cased: image OR video depending on the chosen target.
_MAX_PDF_PAGES = 10_000
_MAX_IMAGE_FRAMES = 10_000

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
    artifacts: list[dict] = field(default_factory=list)
    detail: str = ""
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- atomic write helpers -----------------------------------------------------

def _tmp_for(dst: Path) -> Path:
    """`name.part.ext` temp so ffmpeg/Pillow can still infer the format."""
    return dst.with_name(f"{dst.stem}.part{dst.suffix}")


def _candidate_for(dst: Path, directory: bool) -> Path:
    return dst.with_name(f"{dst.name}.part") if directory else _tmp_for(dst)


def _remove_candidate(path: Path) -> str | None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return None
    except FileNotFoundError:
        return None
    except OSError as ex:
        return f"could not remove staged output {path}: {ex}"


def _cancelled(cancelled: Callable[[], bool] | None, label: str, path: Path) -> None:
    if cancelled is not None and cancelled():
        raise CommandCancelled([label, str(path)])


def _write_text_atomic(
    dst: Path,
    text: str,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    try:
        _cancelled(cancelled, "text-write", dst)
        tmp.write_text(text, encoding="utf-8")
        _cancelled(cancelled, "text-commit", dst)
        tmp.replace(dst)
    except BaseException as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise


# --- image converters (Pillow) ------------------------------------------------

def _img_convert(
    src: Path,
    dst: Path,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    try:
        from PIL import Image
    except ImportError:
        return err("dep.missing", "image conversion needs Pillow — pip install pillow")
    tgt = dst.suffix.lower()
    tmp = _tmp_for(dst)
    try:
        _cancelled(cancelled, "image-convert", src)
        with Image.open(src) as im:
            source_frames = int(getattr(im, "n_frames", 1))
            animated = source_frames > 1
            if source_frames > _MAX_IMAGE_FRAMES:
                return err(
                    "resource.limit",
                    f"image has {source_frames} frames; maximum is {_MAX_IMAGE_FRAMES}",
                )
            dst.parent.mkdir(parents=True, exist_ok=True)

            if tgt == ".ico":
                im.convert("RGBA").save(tmp, format="ICO",
                                        sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
                _cancelled(cancelled, "image-commit", dst)
                tmp.replace(dst)
                return ok({"note": "multi-size ico"})

            # Animated source -> animated target (gif/webp): keep the frames.
            if animated and tgt in (".gif", ".webp"):
                frames = []
                try:
                    for i in range(source_frames):
                        _cancelled(cancelled, "image-frames", src)
                        im.seek(i)
                        frames.append(im.convert("RGBA").copy())
                    dur = im.info.get("duration", 100)
                    frame_count = len(frames)
                    frames[0].save(tmp, save_all=True, append_images=frames[1:],
                                   loop=im.info.get("loop", 0), duration=dur, disposal=2)
                    _cancelled(cancelled, "image-commit", dst)
                    tmp.replace(dst)
                    return ok({"frames": frame_count})
                finally:
                    for frame in frames:
                        frame.close()

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
            _cancelled(cancelled, "image-commit", dst)
            tmp.replace(dst)
            return ok({"note": note} if note else None, details=note)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except Exception as ex:                             # PIL raises many types
        cleanup_error = _remove_candidate(tmp)
        details = f"{src.name}: {ex}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("image.failed", details)


# --- ffmpeg converters (bundled binary) ---------------------------------------

_MUXER = {".mp4": "mp4", ".mov": "mov", ".mkv": "matroska", ".webm": "webm",
          ".gif": "gif", ".mp3": "mp3", ".wav": "wav", ".flac": "flac",
          ".aac": "adts", ".m4a": "ipod", ".ogg": "ogg", ".opus": "opus"}


def _ff_run(
    src: Path,
    dst: Path,
    mid_args: list[str],
    cancelled: Callable[[], bool] | None = None,
) -> dict:
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
        r = run_cancellable_cmd(cmd, timeout=3600, cancelled=cancelled)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except subprocess.TimeoutExpired:
        cleanup_error = _remove_candidate(tmp)
        details = f"ffmpeg timed out on {src.name}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("ff.timeout", details, retryable=True)
    except OSError as ex:
        cleanup_error = _remove_candidate(tmp)
        details = f"ffmpeg error on {src.name}: {ex}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("ff.error", details, retryable=True)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        cleanup_error = _remove_candidate(tmp)
        details = f"ffmpeg failed: {(r.stderr or '')[-300:]}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("ff.failed", details)
    try:
        _cancelled(cancelled, "ffmpeg-commit", dst)
        tmp.replace(dst)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except OSError as ex:
        cleanup_error = _remove_candidate(tmp)
        details = f"could not commit ffmpeg output: {ex}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("io.commit", details, retryable=True)
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


def _ff_video(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    tgt = dst.suffix.lower()
    if tgt == ".webm":
        args = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"]
    else:  # mp4 / mov / mkv
        args = ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                "-c:a", "aac", "-b:a", opts.audio_bitrate]
        if tgt in (".mp4", ".mov"):
            args += ["-movflags", "+faststart"]
    return _ff_run(src, dst, args, cancelled)


def _ff_video_to_gif(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    fps = opts.fps or 12
    vf = (f"fps={fps},scale={opts.gif_width}:-1:flags=lanczos,"
          f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
    return _ff_run(src, dst, ["-vf", vf], cancelled)


def _ff_extract_audio(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    return _ff_run(
        src, dst, ["-vn", *_audio_codec(dst.suffix.lower(), opts.audio_bitrate)],
        cancelled,
    )


def _ff_audio(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    return _ff_run(
        src, dst, _audio_codec(dst.suffix.lower(), opts.audio_bitrate), cancelled
    )


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


def _md_to_html_str(
    src: Path,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[str | None, dict | None]:
    try:
        import markdown
    except ImportError:
        return None, err("dep.missing", "Markdown conversion needs 'markdown' — pip install markdown")
    try:
        _cancelled(cancelled, "markdown-read", src)
        body = markdown.markdown(
            src.read_text(encoding="utf-8"),
            extensions=["tables", "fenced_code", "codehilite", "sane_lists"],
        )
        _cancelled(cancelled, "markdown-render", src)
        return _wrap_html(body, opts.extra_css), None
    except CommandCancelled:
        raise
    except Exception as ex:  # optional Markdown extensions raise varied types
        return None, err("document.failed", f"{src.name}: {ex}")


def _docx_to_html_str(
    src: Path,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[str | None, dict | None]:
    try:
        import mammoth
    except ImportError:
        return None, err("dep.missing", "DOCX conversion needs 'mammoth' — pip install mammoth")
    try:
        _cancelled(cancelled, "docx-read", src)
        with open(src, "rb") as f:
            body = mammoth.convert_to_html(f).value
        _cancelled(cancelled, "docx-render", src)
        return _wrap_html(body, opts.extra_css), None
    except CommandCancelled:
        raise
    except Exception as ex:  # Mammoth/ZIP parsing raises varied types
        return None, err("document.failed", f"{src.name}: {ex}")


def _html_str_to_pdf(
    html: str,
    dst: Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return err("dep.missing", "PDF output needs 'xhtml2pdf' — pip install xhtml2pdf")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    try:
        _cancelled(cancelled, "pdf-render", dst)
        with open(tmp, "wb") as out:
            status = pisa.CreatePDF(html, dest=out, encoding="utf-8")
        _cancelled(cancelled, "pdf-commit", dst)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except Exception as ex:
        cleanup_error = _remove_candidate(tmp)
        details = f"PDF render error: {ex}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("pdf.failed", details)
    if status.err or not tmp.is_file() or tmp.stat().st_size == 0:
        cleanup_error = _remove_candidate(tmp)
        details = "PDF render reported errors"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("pdf.failed", details)
    tmp.replace(dst)
    return ok()


def _md_to_html(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    html, e = _md_to_html_str(src, opts, cancelled)
    if e:
        return e
    _write_text_atomic(dst, html, cancelled)
    return ok()


def _md_to_pdf(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    html, e = _md_to_html_str(src, opts, cancelled)
    return e if e else _html_str_to_pdf(html, dst, cancelled)


def _html_to_pdf(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    try:
        _cancelled(cancelled, "html-read", src)
        html = src.read_text(encoding="utf-8")
    except CommandCancelled:
        raise
    except (OSError, UnicodeError) as ex:
        return err("document.failed", f"{src.name}: {ex}")
    return _html_str_to_pdf(html, dst, cancelled)


def _docx_to_html(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    html, e = _docx_to_html_str(src, opts, cancelled)
    if e:
        return e
    _write_text_atomic(dst, html, cancelled)
    return ok()


def _docx_to_pdf(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    html, e = _docx_to_html_str(src, opts, cancelled)
    return e if e else _html_str_to_pdf(html, dst, cancelled)


def _pdf_to_images(src: Path, dst_dir: Path, opts: ConvertOptions, cancelled=None) -> dict:
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
    pdf = None
    try:
        _cancelled(cancelled, "pdf-open", src)
        pdf = pdfium.PdfDocument(str(src))
        n = len(pdf)
        if n > _MAX_PDF_PAGES:
            return err(
                "resource.limit",
                f"PDF has {n} pages; maximum supported per job is {_MAX_PDF_PAGES}",
            )
        for i in range(n):
            _cancelled(cancelled, "pdf-pages", src)
            page = pdf[i]
            bitmap = None
            pil = None
            try:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
                if ext == "jpg" and pil.mode in ("RGBA", "P", "LA"):
                    converted = pil.convert("RGB")
                    pil.close()
                    pil = converted
                out = dst_dir / f"page_{i + 1:03d}.{ext}"
                tmp = _tmp_for(out)
                pil.save(tmp)
                _cancelled(cancelled, "pdf-page-commit", out)
                tmp.replace(out)
            finally:
                if pil is not None:
                    pil.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()
    except CommandCancelled:
        raise
    except Exception as ex:
        return err("pdf.failed", f"{src.name}: {ex}")
    finally:
        if pdf is not None:
            pdf.close()
    return ok({"pages": n})


def _pdf_to_text(src: Path, dst: Path, opts: ConvertOptions, cancelled=None) -> dict:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return err("dep.missing", "PDF→text needs 'pypdfium2' — pip install pypdfium2")
    pdf = None
    tmp = _tmp_for(dst)
    try:
        _cancelled(cancelled, "pdf-open", src)
        pdf = pdfium.PdfDocument(str(src))
        if len(pdf) > _MAX_PDF_PAGES:
            return err(
                "resource.limit",
                f"PDF has {len(pdf)} pages; maximum supported per job is {_MAX_PDF_PAGES}",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8", newline="\n") as output:
            for i in range(len(pdf)):
                _cancelled(cancelled, "pdf-text", src)
                page = pdf[i]
                textpage = None
                try:
                    textpage = page.get_textpage()
                    if i:
                        output.write("\n\n")
                    output.write(textpage.get_text_range())
                finally:
                    if textpage is not None:
                        textpage.close()
                    page.close()
        _cancelled(cancelled, "pdf-text-commit", dst)
        tmp.replace(dst)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(tmp)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except Exception as ex:
        cleanup_error = _remove_candidate(tmp)
        details = f"{src.name}: {ex}"
        if cleanup_error:
            details += f"; {cleanup_error}"
        return err("pdf.failed", details)
    finally:
        if pdf is not None:
            pdf.close()
    return ok()


# --- dispatch -----------------------------------------------------------------

DISPATCH: dict[
    tuple[str, str],
    Callable[[Path, Path, ConvertOptions, Callable[[], bool] | None], dict],
] = {}


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


def convert(
    src: Path,
    dst: Path,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Low-level: look up the (kind, target) converter and run it. Envelope out."""
    kind = source_kind(Path(src).suffix)
    fn = DISPATCH.get((kind, opts.target))
    if fn is None:
        return err("unsupported.pair", f"cannot convert {kind or '?'} → {opts.target}")
    return fn(Path(src), Path(dst), opts, cancelled)


def find_output_collisions(paths, opts: ConvertOptions) -> dict[str, tuple[str, ...]]:
    """Detect selected sources that would write the same file/page directory."""
    def outputs(source: Path):
        planned = plan_output(source, opts)
        return () if _same_path(source, planned) else (planned,)

    return _find_collisions(paths, outputs)


def _whole_number(value) -> bool:
    return not isinstance(value, bool) and not (
        isinstance(value, float) and not value.is_integer()
    )


def _normalized_options(opts: ConvertOptions) -> tuple[ConvertOptions | None, str]:
    numeric = (opts.quality, opts.fps, opts.gif_width, opts.dpi)
    if not all(_whole_number(value) for value in numeric):
        return None, "quality, FPS, GIF width, and DPI must be whole numbers"
    try:
        quality, fps = int(opts.quality), int(opts.fps)
        gif_width, dpi = int(opts.gif_width), int(opts.dpi)
        target = str(opts.target).strip().lower().lstrip(".")
        out_root = Path(opts.out_root) if opts.out_root is not None else None
        input_root = Path(opts.input_root) if opts.input_root is not None else None
    except (TypeError, ValueError, OSError) as ex:
        return None, f"invalid conversion options: {ex}"
    if target not in {item for _, item in DISPATCH}:
        return None, f"unsupported target: {target or '?'}"
    if not 1 <= quality <= 100:
        return None, "JPEG/WebP quality must be between 1 and 100"
    if not 0 <= fps <= 240:
        return None, "frame rate must be between 0 and 240"
    if not 16 <= gif_width <= 8192:
        return None, "GIF width must be between 16 and 8192 pixels"
    if not 36 <= dpi <= 1200:
        return None, "PDF render DPI must be between 36 and 1200"
    bitrate = str(opts.audio_bitrate).strip().lower()
    match = re.fullmatch(r"([1-9][0-9]{0,3})k", bitrate)
    if match is None or int(match.group(1)) > 1024:
        return None, "audio bitrate must be between 1k and 1024k"
    if not isinstance(opts.flatten_bg, str) or not opts.flatten_bg.strip():
        return None, "flatten background colour is required"
    if not isinstance(opts.extra_css, str):
        return None, "extra CSS must be text no larger than 1 MiB"
    try:
        css_bytes = len(opts.extra_css.encode("utf-8"))
    except UnicodeEncodeError:
        return None, "extra CSS is not valid Unicode text"
    if css_bytes > 1024 * 1024:
        return None, "extra CSS must be text no larger than 1 MiB"
    return replace(
        opts,
        target=target,
        out_root=out_root,
        input_root=input_root,
        quality=quality,
        fps=fps,
        gif_width=gif_width,
        dpi=dpi,
        audio_bitrate=bitrate,
        flatten_bg=opts.flatten_bg.strip(),
        mirror=bool(opts.mirror),
        dry_run=bool(opts.dry_run),
    ), ""


def _inspect_image(path: Path, cancelled=None) -> dict:
    try:
        from PIL import Image
    except ImportError:
        return err("dep.missing", "image validation needs Pillow — pip install pillow")
    try:
        _cancelled(cancelled, "image-validation", path)
        with Image.open(path) as image:
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1))
            mode = image.mode
            image.seek(max(0, frames - 1))
            image.load()
        _cancelled(cancelled, "image-validation", path)
        if width <= 0 or height <= 0 or frames <= 0:
            return err("output.invalid", "image has invalid geometry or frame count")
        return ok({"width": width, "height": height, "mode": mode, "frames": frames})
    except (OSError, ValueError, EOFError) as ex:
        return err("output.invalid", f"image validation failed: {ex}")


def _validate_utf8(path: Path, cancelled=None) -> dict:
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                _cancelled(cancelled, "text-validation", path)
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
        return ok()
    except CommandCancelled:
        raise
    except (OSError, UnicodeError) as ex:
        return err("output.invalid", f"text validation failed: {ex}")


def _inspect_file(
    candidate: Path,
    logical_path: Path,
    target: str,
    cancelled: Callable[[], bool] | None,
) -> dict:
    try:
        size = candidate.stat().st_size
    except OSError as ex:
        return err("output.missing", f"output is unavailable: {ex}")
    if size <= 0 and target != "txt":
        return err("output.empty", f"{candidate.name} is empty")
    record = {"path": str(logical_path), "bytes": size, "target": target}
    if target in {*_IMG_TARGETS, "gif"}:
        inspected = _inspect_image(candidate, cancelled)
        if inspected["error"]:
            return inspected
        record.update(inspected["data"])
    elif target in {key.lstrip(".") for key in _MUXER} and target != "gif":
        duration = probe_media_duration(candidate, cancelled=cancelled)
        if duration["error"]:
            return err(
                "output.invalid", duration["details"],
                retryable=duration["retryable"],
            )
        record["duration_seconds"] = duration["data"]
    elif target == "pdf":
        try:
            with candidate.open("rb") as handle:
                signature = handle.read(5)
            if signature != b"%PDF-":
                return err("output.invalid", "PDF signature is missing")
        except OSError as ex:
            return err("output.invalid", f"PDF validation failed: {ex}")
    elif target in {"html", "txt"}:
        text_valid = _validate_utf8(candidate, cancelled)
        if text_valid["error"]:
            return text_valid
    try:
        record["sha256"] = sha256_file(candidate, cancelled=cancelled)
    except CommandCancelled:
        raise
    except OSError as ex:
        return err("output.invalid", f"could not hash output: {ex}")
    return ok(record)


def _inspect_output(
    candidate: Path,
    logical_path: Path,
    opts: ConvertOptions,
    converter_data: dict,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    if not candidate.is_dir():
        artifact = _inspect_file(candidate, logical_path, opts.target, cancelled)
        return artifact if artifact["error"] else ok([artifact["data"]])
    pages = converter_data.get("pages")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        return err("output.invalid", "PDF render produced no valid page count")
    expected = [candidate / f"page_{index:03d}.{opts.target}" for index in range(1, pages + 1)]
    try:
        actual = sorted(path for path in candidate.iterdir() if path.is_file())
    except OSError as ex:
        return err("output.invalid", f"could not inspect rendered pages: {ex}")
    if actual != expected:
        return err("output.invalid", "rendered page set is incomplete or contains stale files")
    artifacts: list[dict] = []
    for page in expected:
        _cancelled(cancelled, "page-validation", page)
        inspected = _inspect_file(
            page, logical_path / page.name, opts.target, cancelled
        )
        if inspected["error"]:
            return inspected
        artifacts.append(inspected["data"])
    return ok(artifacts)


def process(
    path: str | Path,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """detect kind → plan output → (dry-run report | convert), for one file."""
    src = Path(path)
    kind = source_kind(src.suffix)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    normalized, options_error = _normalized_options(opts)
    if normalized is None:
        return Result(str(src), "failed", options_error, detail="bad.options")
    opts = normalized
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

    directory = (kind, opts.target) in _DIR_OUTPUT
    candidate = _candidate_for(dst, directory)
    if candidate.exists():
        return Result(
            str(src), "failed", f"staged output already exists: {candidate}",
            before=kind, after=opts.target, out_path=str(dst), detail="output.busy",
        )
    if directory and dst.exists():
        return Result(
            str(src), "failed", f"page output folder already exists: {dst}",
            before=kind, after=opts.target, out_path=str(dst), detail="output.exists",
        )
    if directory:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return Result(
                str(src), "failed", f"staged output already exists: {candidate}",
                before=kind, after=opts.target, out_path=str(dst),
                detail="output.busy",
            )
        except OSError as ex:
            return Result(
                str(src), "failed", f"could not prepare page staging folder: {ex}",
                before=kind, after=opts.target, out_path=str(dst),
                detail="io.prepare", retryable=True,
            )
    try:
        res = convert(src, candidate, opts, cancelled=cancelled)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(candidate)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except OSError as ex:
        cleanup_error = _remove_candidate(candidate)
        reason = f"conversion I/O failed: {ex}"
        if cleanup_error:
            reason += f"; {cleanup_error}"
        return Result(
            str(src), "failed", reason, before=kind, after=opts.target,
            out_path=str(dst), detail="io.failed", retryable=True,
        )
    if res["error"]:
        cleanup_error = _remove_candidate(candidate)
        reason = res["details"]
        if cleanup_error:
            reason += f"; {cleanup_error}"
        return Result(str(src), "failed", reason, before=kind, after=opts.target,
                      out_path=str(dst), detail=res["error_type"], retryable=res["retryable"])
    detail = res.get("details") or ""
    data = res.get("data") or {}
    try:
        inspected = _inspect_output(candidate, dst, opts, data, cancelled)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(candidate)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    if inspected["error"]:
        cleanup_error = _remove_candidate(candidate)
        reason = inspected["details"]
        if cleanup_error:
            reason += f"; {cleanup_error}"
        return Result(
            str(src), "failed", reason, before=kind, after=opts.target,
            out_path=str(dst), detail=inspected["error_type"],
            retryable=inspected["retryable"],
        )
    try:
        _cancelled(cancelled, "conversion-commit", dst)
        candidate.replace(dst)
    except CommandCancelled as ex:
        cleanup_error = _remove_candidate(candidate)
        if cleanup_error:
            raise OSError(cleanup_error) from ex
        raise
    except OSError as ex:
        cleanup_error = _remove_candidate(candidate)
        reason = f"could not commit converted output: {ex}"
        if cleanup_error:
            reason += f"; {cleanup_error}"
        return Result(
            str(src), "failed", reason, before=kind, after=opts.target,
            out_path=str(dst), detail="io.commit", retryable=True,
        )
    if "pages" in data:
        detail = f"{data['pages']} pages"
    elif "frames" in data:
        detail = f"{data['frames']} frames"
    return Result(str(src), "converted", f"{kind} → {opts.target}", before=kind,
                  after=opts.target, out_path=str(dst), artifacts=inspected["data"],
                  detail=detail)


def _same_path(left, right) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False


def validate_result(
    result: Result,
    opts: ConvertOptions,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Reopen and verify the exact single artifact or rendered page set."""
    normalized, _ = _normalized_options(opts)
    if normalized is None or not isinstance(result.artifacts, list):
        return False
    if not isinstance(result.retryable, bool):
        return False
    src = Path(result.src)
    kind = source_kind(src.suffix)
    if not src.is_file() or not kind:
        return False
    dst = plan_output(src, normalized)
    if result.before != kind or result.after != normalized.target:
        return False
    if result.action == "skipped":
        return (
            _same_path(src, dst)
            and result.reason == "source is already the target format"
            and result.out_path is None
            and not result.artifacts
        )
    if result.action == "dry-run":
        return (
            normalized.dry_run
            and result.reason == f"would convert {kind} → {normalized.target}"
            and _same_path(result.out_path, dst)
            and not result.artifacts
        )
    if result.action != "converted" or normalized.dry_run:
        return False
    if (
        result.reason != f"{kind} → {normalized.target}"
        or not _same_path(result.out_path, dst)
        or not result.artifacts
    ):
        return False
    directory = (kind, normalized.target) in _DIR_OUTPUT
    if directory != dst.is_dir():
        return False
    converter_data = {"pages": len(result.artifacts)} if directory else {}
    inspected = _inspect_output(
        dst, dst, normalized, converter_data, cancelled=cancelled
    )
    return not inspected["error"] and inspected["data"] == result.artifacts

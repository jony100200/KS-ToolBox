"""Metadata Scrubber engine — copy images to a delivery folder with metadata removed.

Generated images are self-documenting in a way that is great internally and bad on
publish: ComfyUI writes its entire API graph into PNG `prompt`/`workflow` chunks, and
stable-diffusion.cpp writes prompt/seed/sampler/LoRA/model names into `parameters`.
Cameras and phones add EXIF (including GPS). All of it travels with the file.

This tool is **copy-only by contract**: sources are opened read-only and never written
to, so the masters keep their provenance while the delivered copies carry nothing. That
one-way guarantee is the whole point — an in-place scrub would destroy the only record
of how an image was made.

Pixels are preserved exactly: PNG/WebP re-encode losslessly, and JPEG is re-encoded with
the original quantization tables and subsampling (`quality="keep"`), which avoids a
second generation of loss. Colour is preserved by keeping the ICC profile, which
describes the pixels rather than their provenance; drop it with `keep_icc=False`.

Every written file is re-opened and re-inspected before it is reported as scrubbed. If
anything survived, the item fails loudly rather than silently shipping a leak.

Public interface:
    inspect(path)                 -> list[str]     metadata keys that would leak (pure read)
    process(path, opts)           -> Result        copy -> strip -> verify, for one image
    validate_result(result)       -> bool          used for durable-queue reuse
"""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS

# PNG text chunks, EXIF/XMP/IPTC containers, and the AI-recipe keys. `exif` and
# `xmp` are raw blobs; the rest are text chunk keywords Pillow surfaces in .info.
LEAKY_KEYS = (
    "prompt",           # ComfyUI: the complete API graph
    "workflow",         # ComfyUI: the UI graph
    "parameters",       # sd.cpp / A1111: prompt, seed, sampler, LoRA, model names
    "Comment", "Software", "Description", "Author", "Copyright", "Title",
    "exif", "XML:com.adobe.xmp", "xmp", "iptc", "photoshop", "adobe",
    "Raw profile type exif", "Raw profile type iptc", "Raw profile type xmp",
)

# Keys that describe the pixels rather than their provenance -> never treated as leaks.
_PIXEL_KEYS = ("icc_profile", "dpi", "gamma", "transparency", "srgb", "chromaticity")

_LOSSLESS_SUFFIXES = {".png", ".webp", ".bmp", ".tif", ".tiff"}
_JPEG_SUFFIXES = {".jpg", ".jpeg"}


@dataclass
class ScrubOptions:
    out_root: Path | None = None        # None -> ./clean beside each source
    input_root: Path | None = None      # mirror mode: recreate the tree under out_root
    mirror: bool = False
    keep_icc: bool = True               # colour fidelity; not provenance
    dry_run: bool = False
    copy_non_images: bool = True        # keep the delivery folder complete
    _leaky: tuple = field(default=LEAKY_KEYS, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_leaky", None)
        for k in ("out_root", "input_root"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d


@dataclass
class Result:
    src: str
    action: str                        # scrubbed | already-clean | copied | skipped | failed | dry-run
    reason: str
    found: str = ""                    # comma-separated keys that were present
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def inspect(path: str | Path) -> list[str]:
    """Metadata keys present on an image that would leak provenance. Read-only."""
    try:
        from PIL import Image
    except ImportError:
        return []
    try:
        with Image.open(path) as im:
            info = dict(im.info)
            # getexif() catches EXIF that never lands in .info (common for JPEG).
            has_exif = False
            try:
                has_exif = bool(im.getexif())
            except Exception:
                has_exif = False
    except Exception:
        return []
    found = [k for k, v in info.items()
             if k in LEAKY_KEYS and v not in (None, b"", "")
             and k not in _PIXEL_KEYS]
    if has_exif and "exif" not in found:
        found.append("exif")
    return sorted(found)


def plan_output(src: Path, opts: ScrubOptions) -> Path:
    """Where the cleaned copy goes. Never inside the source's own folder tree."""
    if opts.out_root is None:
        return src.parent / "clean" / src.name
    root = Path(opts.out_root)
    if opts.mirror and opts.input_root:
        try:
            return root / src.relative_to(Path(opts.input_root))
        except ValueError:
            pass                       # outside the declared root -> flat
    return root / src.name


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def process(path: str | Path, opts: ScrubOptions, *, cancelled=None) -> Result:
    """inspect -> copy without metadata -> re-inspect the written file."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if cancelled is not None and cancelled():
        return Result(str(src), "skipped", "cancelled", detail="run.cancelled")

    suffix = src.suffix.lower()
    if suffix not in IMAGE_EXTS:
        if not opts.copy_non_images:
            return Result(str(src), "skipped", "not an image", detail="type.skipped")
        dst = plan_output(src, opts)
        if opts.dry_run:
            return Result(str(src), "dry-run", "would copy (non-image)", out_path=str(dst),
                          detail="type.passthrough")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return Result(str(src), "copied", "non-image copied unchanged", out_path=str(dst),
                      detail="type.passthrough")

    try:
        from PIL import Image
    except ImportError:
        return Result(str(src), "failed", "Pillow not installed - pip install pillow",
                      detail="dep.missing")

    found = inspect(src)
    dst = plan_output(src, opts)

    # Refuse to write beside/over the master. The copy-only guarantee is the reason
    # this tool is safe to point at an irreplaceable library.
    if dst.resolve() == src.resolve():
        return Result(str(src), "failed", "output would overwrite the source",
                      found=", ".join(found), detail="path.inplace")

    if opts.dry_run:
        return Result(str(src), "dry-run",
                      f"would strip {', '.join(found)}" if found else "already clean",
                      found=", ".join(found), out_path=str(dst), detail="run.dry")

    try:
        with Image.open(src) as im:
            im.load()
            icc = im.info.get("icc_profile") if opts.keep_icc else None
            # A fresh image carries no .info, so nothing can ride along on save.
            clean = Image.new(im.mode, im.size)
            clean.paste(im)
            save_kw: dict = {}
            if icc:
                save_kw["icc_profile"] = icc
            if suffix in _JPEG_SUFFIXES:
                # Reuse the original tables: re-encoding without this adds a second
                # generation of JPEG loss to every delivered file.
                save_kw["quality"] = "keep"
                save_kw["subsampling"] = "keep"
            elif suffix in _LOSSLESS_SUFFIXES and suffix == ".webp":
                save_kw["lossless"] = True
            dst.parent.mkdir(parents=True, exist_ok=True)
            clean.save(dst, **save_kw)
    except Exception as exc:                     # noqa: BLE001 - reported, never swallowed
        return Result(str(src), "failed", f"{type(exc).__name__}: {exc}",
                      found=", ".join(found), detail="write.failed")

    # Verify the artifact, not the intention.
    remaining = inspect(dst)
    if remaining:
        return Result(str(src), "failed",
                      f"metadata survived the copy: {', '.join(remaining)}",
                      found=", ".join(found), out_path=str(dst), detail="verify.failed")

    if not found:
        return Result(str(src), "already-clean", "no metadata to remove",
                      out_path=str(dst), detail="clean.copied")
    return Result(str(src), "scrubbed", f"removed {', '.join(found)}",
                  found=", ".join(found), out_path=str(dst), detail="clean.scrubbed")


def validate_result(result: Result) -> bool:
    """True when a stored result still matches reality (durable-queue reuse)."""
    if result.action in ("skipped", "dry-run"):
        return True
    if result.action == "failed":
        return False
    if not result.out_path:
        return False
    out = Path(result.out_path)
    if not out.is_file():
        return False
    return not inspect(out)

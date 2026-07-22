"""To SVG engine — batch-vectorize raster images into SVG with vtracer.

Pure logic, no UI, no global state. vtracer (the Rust tracer's Python binding)
does the raster→vector conversion; it is imported LAZILY inside process() so
discovery and the sidebar keep working on a machine that hasn't installed it yet.

The conversion logic is distilled from ChobiEngine's tosvg_wrapper, keeping the
vtracer path and dropping the job-runner shape around it. Anti-patterns fixed on
the way in: the source swallowed import errors with `except Exception: pass` and
picked engines by silent fallback — here a missing dep is announced as a value
(a `failed` Result with `dep.missing` + a "pip install vtracer" message), never
crashed or hidden. The fragile string-splicing metadata injection is dropped.

Errors are values: process() returns a Result; the vectorize itself delegates to
vtracer. See CodingPrinciples.md.

Public interface:
    plan_output(src, opts)  -> Path            (where the .svg goes; pure)
    process(path, opts)     -> Result          (load -> vectorize -> save)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS

COLORMODES = ("color", "binary")            # vtracer: full-colour trace vs black/white
HIERARCHIES = ("stacked", "cutout")         # stacked layers vs cut-out (non-overlapping)


@dataclass
class SvgOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    colormode: str = "color"          # color | binary
    hierarchical: str = "stacked"     # stacked | cutout
    filter_speckle: int = 4           # discard blobs smaller than this (px) — denoise
    color_precision: int = 6          # bits of colour kept (higher = more colours)
    path_precision: int = 8           # decimal places in path coords (higher = finer)
    dry_run: bool = True


def plan_output(src: Path, opts: SvgOptions) -> Path:
    """Where this image's SVG goes. Mirror preserves the input subtree under
    out_root; else flat under out_root; else an `svg` folder beside src. The
    output always carries the `.svg` suffix."""
    src = Path(src)
    name = src.stem + ".svg"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "svg" / name


@dataclass
class Result:
    src: str
    action: str                       # converted | failed | dry-run
    reason: str
    out_path: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: SvgOptions) -> Result:
    """load -> vectorize -> save, for one image."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    dst = plan_output(src, opts)

    if opts.dry_run:
        return Result(str(src), "dry-run", f"would vectorize to {opts.colormode} SVG",
                      out_path=str(dst))

    # Lazy import: keeps the module (and therefore discovery) importable without
    # vtracer installed. A missing dep is a reported value, not a crash.
    try:
        import vtracer
    except ImportError:
        return Result(str(src), "failed", "vtracer not installed — pip install vtracer",
                      out_path=str(dst), detail="dep.missing")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")   # atomic write
        # vtracer reads the raster and writes the SVG itself; unset params (None)
        # fall back to its Rust defaults.
        vtracer.convert_image_to_svg_py(
            str(src), str(tmp),
            colormode=opts.colormode,
            hierarchical=opts.hierarchical,
            filter_speckle=int(opts.filter_speckle),
            color_precision=int(opts.color_precision),
            path_precision=int(opts.path_precision),
        )
        if not tmp.is_file() or tmp.stat().st_size == 0:
            # A silent empty write would be worse than a loud failure — announce it.
            tmp.unlink(missing_ok=True)
            return Result(str(src), "failed", f"vtracer produced no output for {src.name}",
                          out_path=str(dst), detail="convert.empty")
        tmp.replace(dst)
    except Exception as ex:               # vtracer raises on unreadable / unsupported images
        return Result(str(src), "failed", f"could not vectorize {src.name}: {ex}",
                      out_path=str(dst), detail="convert.failed")

    return Result(str(src), "converted", f"{opts.colormode} SVG, speckle {opts.filter_speckle}",
                  out_path=str(dst), detail=opts.hierarchical)

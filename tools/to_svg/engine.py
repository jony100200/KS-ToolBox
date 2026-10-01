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

import hashlib
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, asdict, replace
from pathlib import Path

from toolbox.engine_common import CommandCancelled, IMAGE_EXTS

COLORMODES = ("color", "binary")            # vtracer: full-colour trace vs black/white
HIERARCHIES = ("stacked", "cutout")         # stacked layers vs cut-out (non-overlapping)
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_SVG_BYTES = 512 * 1024 * 1024
MAX_SVG_ELEMENTS = 1_000_000
MAX_FILTER_SPECKLE = 1_000_000
MAX_PATH_PRECISION = 16
Cancelled = Callable[[], bool] | None


def _cancelled(cancelled: Cancelled, stage: str, path: str | Path = "") -> None:
    if cancelled is not None and cancelled():
        command = [stage]
        if path:
            command.append(str(path))
        raise CommandCancelled(command)


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


def normalized_options(opts: SvgOptions) -> tuple[SvgOptions | None, str]:
    if opts.colormode not in COLORMODES:
        return None, f"unsupported color mode: {opts.colormode!r}"
    if opts.hierarchical not in HIERARCHIES:
        return None, f"unsupported hierarchy: {opts.hierarchical!r}"

    def bounded_integer(value, name: str, minimum: int, maximum: int):
        if isinstance(value, bool):
            return None, f"{name} must be from {minimum} to {maximum}"
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None, f"{name} must be from {minimum} to {maximum}"
        if normalized != value or not minimum <= normalized <= maximum:
            return None, f"{name} must be from {minimum} to {maximum}"
        return normalized, ""

    speckle, error = bounded_integer(
        opts.filter_speckle, "filter speckle", 0, MAX_FILTER_SPECKLE
    )
    if error:
        return None, error
    color_precision, error = bounded_integer(
        opts.color_precision, "color precision", 1, 8
    )
    if error:
        return None, error
    path_precision, error = bounded_integer(
        opts.path_precision, "path precision", 0, MAX_PATH_PRECISION
    )
    if error:
        return None, error
    try:
        out_root = Path(opts.out_root) if opts.out_root is not None else None
        input_root = Path(opts.input_root) if opts.input_root is not None else None
    except (TypeError, ValueError):
        return None, "input/output roots must be filesystem paths"
    return replace(
        opts,
        out_root=out_root,
        input_root=input_root,
        mirror=bool(opts.mirror),
        filter_speckle=speckle,
        color_precision=color_precision,
        path_precision=path_precision,
        dry_run=bool(opts.dry_run),
    ), ""


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
    artifact: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _svg_artifact(
    path: str | Path,
    *,
    record_path: str | Path | None = None,
    cancelled: Cancelled = None,
) -> dict:
    """Validate one bounded SVG and return exact reusable artifact metadata."""
    source = Path(path)
    size = source.stat().st_size
    if size <= 0 or size > MAX_SVG_BYTES:
        raise OSError(f"SVG size must be from 1 to {MAX_SVG_BYTES} bytes")
    digest = hashlib.sha256()
    tail = b""
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            _cancelled(cancelled, "svg-validation", source)
            digest.update(chunk)
            scan = (tail + chunk).lower()
            if b"<!doctype" in scan or b"<!entity" in scan:
                raise OSError("SVG contains a forbidden document/entity declaration")
            tail = scan[-16:]

    elements = paths = 0
    root_name = ""
    try:
        for event, element in ET.iterparse(source, events=("start", "end")):
            _cancelled(cancelled, "svg-xml-validation", source)
            if event == "start":
                elements += 1
                if elements > MAX_SVG_ELEMENTS:
                    raise OSError(
                        f"SVG exceeds the {MAX_SVG_ELEMENTS} element limit"
                    )
                name = element.tag.rsplit("}", 1)[-1].lower()
                if elements == 1:
                    root_name = name
                if name == "path":
                    paths += 1
            else:
                element.clear()
    except ET.ParseError as ex:
        raise OSError(f"invalid SVG XML: {ex}") from ex
    if root_name != "svg":
        raise OSError("vector output root is not <svg>")
    recorded = Path(record_path) if record_path is not None else source
    return {
        "path": str(recorded.resolve(strict=False)),
        "bytes": size,
        "sha256": digest.hexdigest(),
        "elements": elements,
        "paths": paths,
    }


def process(
    path: str | Path,
    opts: SvgOptions,
    cancelled: Cancelled = None,
) -> Result:
    """load -> vectorize -> save, for one image."""
    src = Path(path)
    normalized, options_error = normalized_options(opts)
    if normalized is None:
        return Result(str(src), "failed", options_error, detail="options.invalid")
    opts = normalized
    _cancelled(cancelled, "svg-input", src)
    if src.is_symlink():
        return Result(
            str(src), "failed", f"symlinked inputs are not accepted: {src}",
            detail="file.symlink",
        )
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if src.suffix.lower() not in IMAGE_EXTS:
        return Result(
            str(src), "failed", f"unsupported raster format: {src.suffix or '(none)'}",
            detail="format.unsupported",
        )
    try:
        source_size = src.stat().st_size
    except OSError as ex:
        return Result(str(src), "failed", f"could not inspect {src}: {ex}",
                      detail="file.inspect")
    if source_size <= 0 or source_size > MAX_INPUT_BYTES:
        return Result(
            str(src), "failed",
            f"input size must be from 1 to {MAX_INPUT_BYTES} bytes",
            detail="file.size",
        )
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

    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if tmp.is_symlink() or (tmp.exists() and not tmp.is_file()):
            return Result(
                str(src), "failed", f"unsafe staged output path: {tmp}",
                out_path=str(dst), detail="output.stage",
            )
        tmp.unlink(missing_ok=True)
        _cancelled(cancelled, "svg-vectorize", src)
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
        _cancelled(cancelled, "svg-vectorize", src)
        artifact = _svg_artifact(
            tmp, record_path=dst, cancelled=cancelled
        )
        tmp.replace(dst)
        if dst.stat().st_size != artifact["bytes"]:
            raise OSError(f"published SVG size changed: {dst}")
    except CommandCancelled as ex:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup:
            raise OSError(f"SVG cancellation cleanup failed: {cleanup}") from ex
        raise
    except Exception as ex:               # vtracer raises on unreadable / unsupported images
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup:
            return Result(
                str(src), "failed",
                f"could not vectorize {src.name}: {ex}; staged cleanup failed: {cleanup}",
                out_path=str(dst), detail="convert.cleanup",
            )
        return Result(str(src), "failed", f"could not vectorize {src.name}: {ex}",
                      out_path=str(dst), detail="convert.failed")

    return Result(str(src), "converted", f"{opts.colormode} SVG, speckle {opts.filter_speckle}",
                  out_path=str(dst), detail=opts.hierarchical, artifact=artifact)


def validate_result(
    result: Result,
    opts: SvgOptions,
    *,
    expected_src: str | Path | None = None,
    cancelled: Cancelled = None,
) -> bool:
    normalized, _ = normalized_options(opts)
    if normalized is None or result.action == "failed":
        return False
    if expected_src is not None and (
        Path(result.src).resolve(strict=False)
        != Path(expected_src).resolve(strict=False)
    ):
        return False
    expected = plan_output(Path(result.src), normalized)
    if result.out_path is None or (
        Path(result.out_path).resolve(strict=False)
        != expected.resolve(strict=False)
    ):
        return False
    if result.action == "dry-run":
        return normalized.dry_run and result.artifact is None
    if normalized.dry_run or result.action != "converted":
        return False
    if not isinstance(result.artifact, dict):
        return False
    try:
        return _svg_artifact(
            expected, cancelled=cancelled
        ) == result.artifact
    except CommandCancelled:
        raise
    except (KeyError, OSError, TypeError, ValueError):
        return False

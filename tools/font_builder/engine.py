"""Font Builder engine — compile raster glyph images and SVGs into TrueType fonts.

Pure logic, headless, no UI, no global state.
Converts raster glyphs (PNG/JPG/WebP/BMP) into vector outlines via vtracer,
calculates font metrics (cap-height, baseline, advance width, side bearings),
and compiles valid .TTF TrueType font binaries using fontTools.

Public interface:
    plan_output(src, opts) -> Path
    parse_character_from_name(name) -> tuple[str, int] | None
    build_font(sources, opts, cancelled, progress) -> Result
    process(path, opts) -> Result
"""
from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from toolbox.engine_common import CommandCancelled, IMAGE_EXTS

SUPPORTED_EXTS = IMAGE_EXTS | {".svg"}
Cancelled = Callable[[], bool] | None
Progress = Callable[[int, int, str], None] | None

# Standard named glyphs mapping to character and unicode codepoint
NAMED_GLYPHS: dict[str, str] = {
    "space": " ",
    "sp": " ",
    "exclamation": "!",
    "exclam": "!",
    "question": "?",
    "period": ".",
    "dot": ".",
    "comma": ",",
    "colon": ":",
    "semicolon": ";",
    "hyphen": "-",
    "dash": "-",
    "minus": "-",
    "plus": "+",
    "equal": "=",
    "equals": "=",
    "slash": "/",
    "backslash": "\\",
    "at": "@",
    "hash": "#",
    "numbersign": "#",
    "percent": "%",
    "ampersand": "&",
    "asterisk": "*",
    "star": "*",
    "parenleft": "(",
    "parenright": ")",
    "bracketleft": "[",
    "bracketright": "]",
    "braceleft": "{",
    "braceright": "}",
    "underscore": "_",
    "quote": "'",
    "quotesingle": "'",
    "quotedbl": '"',
    "tilde": "~",
    "caret": "^",
    "grave": "`",
    "bar": "|",
    "dollar": "$",
    "euro": "€",
    "pound": "£",
    "yen": "¥",
}

# Standard characters that typically extend below the baseline (descenders)
DESCENDER_CHARS = set("gjpqyçşµ,;()[]{}")


@dataclass
class FontOptions:
    font_name: str = "CustomFont"
    family_name: str = "CustomFont"
    style_name: str = "Regular"
    designer: str = "KS ToolBox"
    version: str = "1.0"
    units_per_em: int = 1000
    cap_height: int = 700
    ascent: int = 800
    descent: int = -200
    side_bearing: int = 50
    monospace: bool = False
    fixed_width: int = 600
    auto_descenders: bool = True
    descender_ratio: float = 0.25
    filter_speckle: int = 4
    out_root: Path | None = None
    dry_run: bool = False


@dataclass
class Result:
    src: str
    action: str                       # built | failed | dry-run
    reason: str
    out_path: str | None = None
    detail: str = ""
    artifact: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _check_cancelled(cancelled: Cancelled, stage: str = "font_builder") -> None:
    if cancelled is not None and cancelled():
        raise CommandCancelled([stage])


def parse_character_from_name(name: str) -> tuple[str, int] | None:
    """Parse character and codepoint from filename stem (e.g. 'A', 'small_a', 'u0041', 'comma')."""
    raw = name.strip()
    if not raw:
        return None

    # Exact single character filename (e.g. 'A', 'a', '1', '$')
    if len(raw) == 1:
        return raw, ord(raw)

    lower = raw.lower()

    # Named glyphs (e.g. 'space', 'exclamation', 'comma')
    if lower in NAMED_GLYPHS:
        char = NAMED_GLYPHS[lower]
        return char, ord(char)

    # Prefix notations: cap_a, upper_a, lower_a, small_a
    if lower.startswith(("cap_", "upper_")) and len(lower) > 4:
        sub = raw[len(raw) - 1 :]
        if len(sub) == 1:
            return sub.upper(), ord(sub.upper())
    if lower.startswith(("small_", "lower_")) and len(lower) > 6:
        sub = raw[len(raw) - 1 :]
        if len(sub) == 1:
            return sub.lower(), ord(sub.lower())

    # Unicode hex formats: u0041, uni0041, u+0041, 0x0041
    hex_match = re.match(r"^(?:uni|u\+|u|0x)([0-9a-fA-F]{4,6})$", raw, re.IGNORECASE)
    if hex_match:
        try:
            cp = int(hex_match.group(1), 16)
            return chr(cp), cp
        except (ValueError, OverflowError):
            pass

    return None


def plan_output(src_or_folder: Path, opts: FontOptions) -> Path:
    """Determine the destination .ttf font file path."""
    filename = f"{opts.font_name.strip() or 'CustomFont'}.ttf"
    if opts.out_root is not None:
        return opts.out_root / filename
    if src_or_folder.is_dir():
        return src_or_folder / filename
    return src_or_folder.parent / filename


def _extract_svg_paths_from_file(svg_path: Path) -> list[str]:
    """Parse an SVG file and extract all path data strings."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    paths: list[str] = []
    for elem in root.iter():
        if elem.tag.endswith("path"):
            d = elem.attrib.get("d", "").strip()
            if d:
                paths.append(d)
        elif elem.tag.endswith("rect"):
            x = float(elem.attrib.get("x", 0))
            y = float(elem.attrib.get("y", 0))
            w = float(elem.attrib.get("width", 0))
            h = float(elem.attrib.get("height", 0))
            if w > 0 and h > 0:
                paths.append(f"M {x} {y} L {x+w} {y} L {x+w} {y+h} L {x} {y+h} Z")
        elif elem.tag.endswith("circle"):
            cx = float(elem.attrib.get("cx", 0))
            cy = float(elem.attrib.get("cy", 0))
            r = float(elem.attrib.get("r", 0))
            if r > 0:
                k = 0.5522847498 * r
                paths.append(
                    f"M {cx} {cy-r} "
                    f"C {cx+k} {cy-r} {cx+r} {cy-k} {cx+r} {cy} "
                    f"C {cx+r} {cy+k} {cx+k} {cy+r} {cx} {cy+r} "
                    f"C {cx-k} {cy+r} {cx-r} {cy+k} {cx-r} {cy} "
                    f"C {cx-r} {cy-k} {cx-k} {cy-r} {cx} {cy-r} Z"
                )
    return paths


def _raster_to_svg(raster_path: Path, temp_svg_path: Path, filter_speckle: int = 4) -> bool:
    """Convert raster image to SVG using vtracer."""
    try:
        import vtracer

        vtracer.convert_image_to_svg_py(
            str(raster_path),
            str(temp_svg_path),
            colormode="binary",
            filter_speckle=filter_speckle,
            color_precision=6,
            path_precision=4,
        )
        return temp_svg_path.exists() and temp_svg_path.stat().st_size > 0
    except Exception:
        return False


def _get_all_bounds(tt_glyph) -> tuple[float, float, float, float] | None:
    """Compute overall bounding box across all contours in a TrueType glyph."""
    coords = getattr(tt_glyph, "coordinates", None)
    if not coords or len(coords) == 0:
        return None
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))


def build_font(
    sources: Sequence[Path],
    opts: FontOptions,
    cancelled: Cancelled = None,
    progress: Progress = None,
) -> Result:
    """Compile a list of image/SVG glyph source files into a TrueType (.ttf) font."""
    try:
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.cu2quPen import Cu2QuPen
        from fontTools.pens.transformPen import TransformPen
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        from fontTools.svgLib.path import SVGPath
    except ImportError:
        return Result(
            str(sources[0]) if sources else "",
            "failed",
            "fontTools is not installed — install fonttools to use Font Builder.",
            detail="dep.missing",
        )

    _check_cancelled(cancelled, "init")

    valid_sources: list[tuple[Path, str, int]] = []
    for p in sources:
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTS:
            continue
        parsed = parse_character_from_name(p.stem)
        if parsed is not None:
            char, cp = parsed
            valid_sources.append((p, char, cp))

    first_src_str = str(sources[0]) if sources else ""
    if not valid_sources:
        return Result(
            first_src_str,
            "failed",
            "No recognized glyph images or SVGs found. Name files like 'A.png', 'b.png', '1.png', 'space.png', 'comma.png'.",
            detail="inputs.empty",
        )

    first_src = valid_sources[0][0]
    out_ttf = plan_output(first_src, opts)

    if opts.dry_run:
        return Result(
            first_src_str,
            "dry-run",
            f"Font '{opts.font_name}' planned with {len(valid_sources) + 2} glyphs",
            out_path=str(out_ttf),
            detail="dry_run",
            artifact={
                "font_name": opts.font_name,
                "glyph_count": len(valid_sources) + 2,
                "chars": "".join(sorted([char for _, char, _ in valid_sources])),
            },
        )

    out_ttf.parent.mkdir(parents=True, exist_ok=True)

    fb = FontBuilder(opts.units_per_em, isTTF=True)
    glyph_order: list[str] = [".notdef", "space"]
    cmap: dict[int, str] = {ord(" "): "space"}
    glyphs: dict[str, object] = {}
    h_metrics: dict[str, tuple[int, int]] = {}

    # 1. Standard .notdef glyph (hollow box)
    notdef_pen = TTGlyphPen(None)
    notdef_pen.moveTo((100, 0))
    notdef_pen.lineTo((100, opts.cap_height))
    notdef_pen.lineTo((500, opts.cap_height))
    notdef_pen.lineTo((500, 0))
    notdef_pen.closePath()
    notdef_pen.moveTo((150, 50))
    notdef_pen.lineTo((450, 50))
    notdef_pen.lineTo((450, opts.cap_height - 50))
    notdef_pen.lineTo((150, opts.cap_height - 50))
    notdef_pen.closePath()
    glyphs[".notdef"] = notdef_pen.glyph()
    h_metrics[".notdef"] = (600, 100)

    # 2. Standard space glyph (empty advance)
    space_width = opts.fixed_width if opts.monospace else int(opts.cap_height * 0.5)
    glyphs["space"] = TTGlyphPen(None).glyph()
    h_metrics["space"] = (space_width, 0)

    total = len(valid_sources)
    with tempfile.TemporaryDirectory(prefix="font_builder_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for i, (src_path, char, cp) in enumerate(valid_sources):
            _check_cancelled(cancelled, "glyph_vectorize")
            if progress is not None:
                progress(i + 1, total, f"Processing '{char}' ({i+1}/{total})")

            # Determine SVG path source
            if src_path.suffix.lower() == ".svg":
                target_svg = src_path
            else:
                target_svg = tmp_dir / f"glyph_{i}.svg"
                success = _raster_to_svg(src_path, target_svg, filter_speckle=opts.filter_speckle)
                if not success or not target_svg.exists():
                    continue

            paths = _extract_svg_paths_from_file(target_svg)
            if not paths:
                continue

            combined_svg = " ".join(paths)
            try:
                svg_obj = SVGPath.fromstring(f'<path d="{combined_svg}"/>')
            except Exception:
                continue

            # First pass: Raw bounds in SVG coordinates
            bounds_pen = TTGlyphPen(None)
            svg_obj.draw(bounds_pen)
            raw_bounds = _get_all_bounds(bounds_pen.glyph())
            if raw_bounds is None:
                x_min, y_min, x_max, y_max = 0.0, 0.0, 100.0, 100.0
            else:
                x_min, y_min, x_max, y_max = raw_bounds

            raw_w = max(1.0, x_max - x_min)
            raw_h = max(1.0, y_max - y_min)

            # Scale factor to match target Cap-Height
            scale = opts.cap_height / raw_h

            # Baseline calculation
            if opts.auto_descenders and char in DESCENDER_CHARS:
                drop_offset = opts.cap_height * opts.descender_ratio
                offset_y = (opts.cap_height - drop_offset) + (y_min * scale)
            else:
                offset_y = opts.cap_height + (y_min * scale)

            offset_x = opts.side_bearing - (x_min * scale)

            # Draw glyph transformed into TrueType coordinates
            tt_pen = TTGlyphPen(None)
            cu2qu_pen = Cu2QuPen(tt_pen, max_err=1.0)
            tpen = TransformPen(cu2qu_pen, (scale, 0.0, 0.0, -scale, offset_x, offset_y))
            svg_obj.draw(tpen)

            final_g = tt_pen.glyph()
            final_bounds = _get_all_bounds(final_g)

            glyph_name = f"uni{cp:04X}" if cp > 0xFFFF else f"u{cp:04X}"
            if char.isalnum() and len(char) == 1:
                glyph_name = char

            glyph_order.append(glyph_name)
            cmap[cp] = glyph_name
            glyphs[glyph_name] = final_g

            if opts.monospace:
                advance = opts.fixed_width
                lsb = opts.side_bearing
            else:
                if final_bounds is not None:
                    _, _, max_x, _ = final_bounds
                    advance = int(max_x + opts.side_bearing)
                    lsb = opts.side_bearing
                else:
                    advance = int(raw_w * scale) + (opts.side_bearing * 2)
                    lsb = opts.side_bearing

            h_metrics[glyph_name] = (advance, lsb)

    _check_cancelled(cancelled, "font_assemble")
    if progress is not None:
        progress(total, total, "Assembling TrueType font tables...")

    # Configure FontBuilder TrueType tables
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(h_metrics)
    fb.setupHorizontalHeader(ascent=opts.ascent, descent=opts.descent)
    fb.setupNameTable(
        {
            "familyName": opts.family_name,
            "styleName": opts.style_name,
            "uniqueFontIdentifier": f"{opts.family_name}:{opts.style_name}:2026",
            "fullName": f"{opts.family_name} {opts.style_name}",
            "psName": f"{opts.family_name}-{opts.style_name}".replace(" ", ""),
            "version": f"Version {opts.version}",
            "designer": opts.designer,
        }
    )
    fb.setupOS2(
        sTypoAscender=opts.ascent,
        sTypoDescender=opts.descent,
        usWinAscent=opts.ascent,
        usWinDescent=abs(opts.descent),
    )
    fb.setupPost()

    # Save final TTF binary
    fb.save(out_ttf)

    return Result(
        first_src_str,
        "built",
        f"Compiled '{opts.font_name}' TTF with {len(glyph_order)} glyphs",
        out_path=str(out_ttf),
        detail=f"{out_ttf.stat().st_size} bytes",
        artifact={
            "output_file": str(out_ttf),
            "size_bytes": out_ttf.stat().st_size,
            "glyph_count": len(glyph_order),
            "mapped_chars": len(cmap),
            "font_name": opts.font_name,
        },
    )


def process(path: Path, opts: FontOptions) -> Result:
    """Process a single file or directory containing glyphs into a TrueType font."""
    if path.is_dir():
        sources = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    else:
        sources = [path]
    return build_font(sources, opts)

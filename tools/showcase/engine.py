"""Showcase engine — turn a folder of images/sprites into presentation renders.

Pure logic, no UI, no global state, no AI, no network. Pillow only (lazy-imported
so discovery and the sidebar work before Pillow is installed). Three deterministic
compositing modes:

    contact       - a grid of thumbnails on a clean background (one aggregate PNG)
    hero          - each asset centered on a backdrop (per-file PNG)
    before_after  - two images (or an input + its same-name counterpart in a
                    second folder) side by side with a divider + labels (per-file)

Shared rules: preserve aspect ratio (Lanczos), never upscale beyond 2x, composite
RGBA over the chosen backdrop for opaque outputs or keep transparency for the
checkerboard / none backdrop. Output is always PNG, written atomically via a
`.part` temp then os.replace.

Pure helpers:
    thumbnail_fit(img, box)                 -> PIL.Image
    checkerboard(size, cell)                -> PIL.Image (RGBA)
    vertical_gradient(size, c1, c2)         -> PIL.Image (RGB)
    contact_sheet(images, cols, cell, pad, bg, labels, title=None) -> PIL.Image
    frame_hero(img, opts)                   -> PIL.Image
    before_after(a, b, labels, ...)         -> PIL.Image

Fallible IO:
    process(path, opts)            -> Result   (hero / before_after, per file)
    build_contact_sheet(paths, opts) -> Result (the aggregate contact sheet)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS, sha256_file

MODES = ("contact", "hero", "before_after", "spritesheet")
BG_STYLES = ("solid", "gradient", "checker", "none")
GRID_PRESETS = ("4x4", "3x3", "2x2", "custom")
SHEET_BG_STYLES = ("transparent", "match", "checker", "solid")
PRESENTATION_LIBRARY_FOLDERS = {
    "heroes": "Hero Renders",
    "animations": "Animations",
    "combined": "Combined Videos",
    "sheets": "Presentation Sheets",
}

_LABEL_H = 18          # label band under each contact-sheet thumbnail
_HEADER_H = 44         # contact-sheet title header
_MAX_UPSCALE = 2.0     # never enlarge an asset past 2x (release rule)
_WATERMARK = "Made with KS ToolBox"


# --- colour helpers (pure) ----------------------------------------------------

def _shade(c: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Scale an RGB colour toward black (factor<1) or white-clamped (factor>1)."""
    return tuple(max(0, min(255, int(v * factor))) for v in c[:3])


def _contrast_text(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    """Pick near-white or near-black text so it reads on the given background."""
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (17, 24, 39) if lum > 140 else (243, 244, 246)


# --- pure image helpers -------------------------------------------------------

def thumbnail_fit(img, box: tuple[int, int]):
    """Resize `img` to fit within `box` (w, h), preserving aspect ratio with
    Lanczos. Never enlarges past 2x. Returns a new image (mode unchanged)."""
    from PIL import Image

    bw, bh = max(1, box[0]), max(1, box[1])
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return img.copy()
    scale = min(bw / iw, bh / ih, _MAX_UPSCALE)
    new = (max(1, round(iw * scale)), max(1, round(ih * scale)))
    return img.resize(new, Image.Resampling.LANCZOS)


def checkerboard(size: tuple[int, int], cell: int = 16,
                 light=(255, 255, 255, 255), dark=(204, 204, 204, 255)):
    """A transparency-style checkerboard (RGBA) of `cell`-sized squares."""
    from PIL import Image, ImageDraw

    w, h = max(1, size[0]), max(1, size[1])
    cell = max(2, cell)
    img = Image.new("RGBA", (w, h), light)
    draw = ImageDraw.Draw(img)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if ((x // cell) + (y // cell)) % 2:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=dark)
    return img


def vertical_gradient(size: tuple[int, int], c1, c2):
    """A top→bottom linear gradient (RGB) from colour c1 to c2."""
    from PIL import Image

    w, h = max(1, size[0]), max(1, size[1])
    strip = Image.new("RGB", (1, h))
    denom = max(1, h - 1)
    px = strip.load()
    for y in range(h):
        f = y / denom
        px[0, y] = (
            round(c1[0] + (c2[0] - c1[0]) * f),
            round(c1[1] + (c2[1] - c1[1]) * f),
            round(c1[2] + (c2[2] - c1[2]) * f),
        )
    return strip.resize((w, h))


def _draw_centered(draw, cx: int, y: int, text: str, font, fill,
                   max_w: int | None = None):
    """Draw `text` horizontally centered on cx at vertical y, truncating with an
    ellipsis if it would exceed max_w."""
    if max_w is not None:
        while text and draw.textlength(text, font=font) > max_w and len(text) > 1:
            text = text[:-2] + "…" if len(text) > 2 else text[:-1]
    tw = draw.textlength(text, font=font)
    draw.text((cx - tw / 2, y), text, font=font, fill=fill)


def contact_sheet(images, cols: int, cell: int, pad: int, bg,
                  labels=None, title: str | None = None):
    """A grid of `images` (list of PIL images) as thumbnails on a clean `bg`.

    cols   : columns in the grid (rows derived).
    cell   : square thumbnail box side, in px.
    pad    : gutter/margin between cells and the sheet edge.
    bg     : RGB background colour tuple.
    labels : optional list of per-image caption strings (aligned to `images`).
    title  : optional header text drawn across the top.

    Returns an RGB image. Deterministic; opaque assets composite over `bg`.
    """
    from PIL import Image, ImageDraw, ImageFont

    n = len(images)
    cols = max(1, int(cols))
    cell = max(8, int(cell))
    pad = max(0, int(pad))
    rows = max(1, (n + cols - 1) // cols)
    font = ImageFont.load_default()

    label_h = _LABEL_H if labels else 0
    header_h = _HEADER_H if title else 0
    cell_h = cell + label_h
    sheet_w = pad + cols * (cell + pad)
    sheet_h = header_h + pad + rows * (cell_h + pad)

    sheet = Image.new("RGB", (sheet_w, sheet_h), tuple(bg[:3]))
    draw = ImageDraw.Draw(sheet)
    text_col = _contrast_text(tuple(bg[:3]))

    if title:
        _draw_centered(draw, sheet_w // 2, (header_h - 11) // 2, str(title),
                       font, text_col, max_w=sheet_w - 2 * pad)

    for idx, img in enumerate(images):
        col = idx % cols
        row = idx // cols
        cx = pad + col * (cell + pad)
        cy = header_h + pad + row * (cell_h + pad)
        thumb = thumbnail_fit(img.convert("RGBA"), (cell, cell))
        tw, th = thumb.size
        ox = cx + (cell - tw) // 2
        oy = cy + (cell - th) // 2
        sheet.paste(thumb, (ox, oy), thumb)
        if labels and idx < len(labels):
            _draw_centered(draw, cx + cell // 2, cy + cell + 3, str(labels[idx]),
                           font, text_col, max_w=cell)
    return sheet


def frame_hero(img, opts: "ShowcaseOptions"):
    """Center one asset on a nice backdrop (solid / gradient / checker / none)
    with margin, an optional drop shadow, optional caption and optional
    "Made with KS ToolBox" watermark.

    Returns RGB for solid/gradient (opaque) and RGBA for checker/none (keeps the
    checker pattern / transparency). Canvas is a square of opts.cell_size.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    size = max(32, int(opts.cell_size))
    canvas_size = (size, size)
    style = opts.bg_style if opts.bg_style in BG_STYLES else "solid"
    bg = tuple(opts.bg_color[:3])

    if style == "none":
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        opaque = False
        text_ref = (255, 255, 255)
    elif style == "checker":
        canvas = checkerboard(canvas_size, cell=max(8, size // 24))
        opaque = False
        text_ref = (255, 255, 255)
    elif style == "gradient":
        canvas = vertical_gradient(canvas_size, bg, _shade(bg, 0.55)).convert("RGBA")
        opaque = True
        text_ref = bg
    else:  # solid
        canvas = Image.new("RGBA", canvas_size, (*bg, 255))
        opaque = True
        text_ref = bg

    margin = max(0, int(opts.padding))
    caption = (opts.caption or "").strip()
    font = ImageFont.load_default()
    cap_h = 26 if caption else 0

    box = (size - 2 * margin, size - 2 * margin - cap_h)
    thumb = thumbnail_fit(img.convert("RGBA"), box)
    tw, th = thumb.size
    ox = (size - tw) // 2
    oy = margin + (size - cap_h - 2 * margin - th) // 2

    if opts.shadow:
        off = max(3, size // 60)
        alpha = thumb.getchannel("A").point(lambda a: int(a * 0.45))
        shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        black = Image.new("RGBA", (tw, th), (0, 0, 0, 255))
        shadow.paste(black, (ox + off, oy + off), alpha)
        shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, size // 80)))
        canvas = Image.alpha_composite(canvas, shadow)

    canvas.paste(thumb, (ox, oy), thumb)

    draw = ImageDraw.Draw(canvas)
    if caption:
        _draw_centered(draw, size // 2, size - cap_h + 4, caption, font,
                       _contrast_text(text_ref), max_w=size - 2 * margin)
    if opts.watermark:
        wt_col = (156, 163, 175, 255) if not opaque else _shade(_contrast_text(text_ref), 0.75)
        wt_w = draw.textlength(_WATERMARK, font=font)
        draw.text((size - wt_w - 6, size - 14), _WATERMARK, font=font, fill=wt_col)

    return canvas.convert("RGB") if opaque else canvas


def before_after(a, b, labels=("Before", "After"), *, cell: int = 512, pad: int = 20,
                 bg=(17, 24, 39), label_color=(243, 244, 246),
                 divider=(148, 163, 184)):
    """Pair images `a` and `b` side by side with a divider and top labels.
    Both are fit into a `cell` square box. Returns an RGB image whose width is
    roughly 2x a single panel plus the divider."""
    from PIL import Image, ImageDraw, ImageFont

    cell = max(32, int(cell))
    pad = max(0, int(pad))
    dv = 4
    ta = thumbnail_fit(a.convert("RGBA"), (cell, cell))
    tb = thumbnail_fit(b.convert("RGBA"), (cell, cell))
    band = 30 if labels else 0
    panel_h = max(ta.height, tb.height)

    W = pad + ta.width + pad + dv + pad + tb.width + pad
    H = band + pad + panel_h + pad
    canvas = Image.new("RGB", (W, H), tuple(bg[:3]))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    ax = pad
    ay = band + pad + (panel_h - ta.height) // 2
    canvas.paste(ta, (ax, ay), ta)
    bx = pad + ta.width + pad + dv + pad
    by = band + pad + (panel_h - tb.height) // 2
    canvas.paste(tb, (bx, by), tb)

    div_x = pad + ta.width + pad
    draw.rectangle([div_x, band, div_x + dv - 1, H - 1], fill=tuple(divider[:3]))

    if labels:
        _draw_centered(draw, ax + ta.width // 2, (band - 11) // 2, str(labels[0]),
                       font, tuple(label_color[:3]), max_w=ta.width)
        _draw_centered(draw, bx + tb.width // 2, (band - 11) // 2, str(labels[1]),
                       font, tuple(label_color[:3]), max_w=tb.width)
    return canvas


def spritesheet_grid(images, cols: int = 4, rows: int = 4, cell_size: int = 256, pad: int = 16,
                     bg_style: str = "transparent", bg_color: tuple = (17, 24, 39),
                     repeat_single: bool = True):
    """Assemble `images` into a `cols` x `rows` grid of `cell_size` x `cell_size` icons.

    pad            : gutter/margin between cells and outer border (in px).
    bg_style       : "transparent" (RGBA alpha 0), "match" (uses bg_color), "solid", "checker", "gradient".
    repeat_single  : if only 1 image is provided, tile it across all cols*rows cells.

    Returns PIL.Image (RGBA).
    """
    from PIL import Image

    cols = max(1, int(cols))
    rows = max(1, int(rows))
    cell_size = max(16, int(cell_size))
    pad = max(0, int(pad))

    sheet_w = pad + cols * (cell_size + pad)
    sheet_h = pad + rows * (cell_size + pad)

    if bg_style == "transparent" or bg_style == "none":
        canvas = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    elif bg_style == "checker":
        canvas = checkerboard((sheet_w, sheet_h), cell=max(8, cell_size // 16))
    elif bg_style == "gradient":
        canvas = vertical_gradient((sheet_w, sheet_h), bg_color, _shade(bg_color, 0.55)).convert("RGBA")
    else:  # solid or match
        canvas = Image.new("RGBA", (sheet_w, sheet_h), (*bg_color[:3], 255))

    total_cells = cols * rows
    for idx in range(total_cells):
        if idx < len(images):
            icon = images[idx]
        elif repeat_single and len(images) == 1:
            icon = images[0]
        else:
            break

        c = idx % cols
        r = idx // cols
        x = pad + c * (cell_size + pad)
        y = pad + r * (cell_size + pad)

        icon_rgba = icon.convert("RGBA")
        if icon_rgba.size != (cell_size, cell_size):
            icon_rgba = thumbnail_fit(icon_rgba, (cell_size, cell_size))

        canvas.paste(icon_rgba, (x, y), icon_rgba)

    return canvas


# --- options + result ---------------------------------------------------------

@dataclass
class ShowcaseOptions:
    mode: str = "contact"                    # contact | hero | before_after | spritesheet
    cols: int = 4
    rows: int = 4
    grid_preset: str = "4x4"                 # 4x4 | 3x3 | 2x2 | custom
    cell_size: int = 256
    padding: int = 16
    bg_style: str = "solid"                  # solid | gradient | checker | none
    bg_color: tuple = (17, 24, 39)
    caption: str = ""
    watermark: bool = True
    shadow: bool = True
    labels: bool = True                      # filename labels on the contact sheet
    title: str = ""                          # optional contact-sheet header
    ba_folder: Path | None = None            # before_after counterpart folder
    export_sheet: bool = True                # Export Spritesheet / Grid Presentation PNG
    export_icons: bool = True                # Export Individual Framed Icon PNGs
    sheet_bg_style: str = "transparent"      # transparent | match | checker | solid
    repeat_single: bool = True               # Repeat 1 image across full grid
    paginate_sheet: bool = False             # Export all inputs across grid pages
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    dry_run: bool = True


@dataclass
class Result:
    src: str
    action: str                              # rendered | failed | dry-run
    reason: str
    out_path: str | None = None
    detail: str = ""
    input_count: int = 0
    rendered_count: int = 0
    skipped_count: int = 0
    output_sha256: str = ""
    out_paths: list[str] | None = None
    output_sha256s: dict[str, str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _fail_result(src, details: str, etype: str, *, input_count: int = 1) -> Result:
    return Result(str(src), "failed", details, detail=etype, input_count=input_count)


# --- output planning ----------------------------------------------------------

def plan_output(src: Path, opts: ShowcaseOptions, suffix: str) -> Path:
    """Where a per-file render goes. Mirror preserves the input subtree under
    out_root; else flat under out_root; else a `showcase` folder beside src."""
    src = Path(src)
    name = src.stem + suffix + ".png"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    return src.parent / "showcase" / name


def _contact_output(paths, opts: ShowcaseOptions) -> Path:
    name = "contact_sheet.png"
    if opts.out_root:
        return Path(opts.out_root) / name
    first = Path(paths[0]) if paths else Path.cwd()
    return first.parent / "showcase" / name


def _spritesheet_output(paths, opts: ShowcaseOptions, page: int | None = None) -> Path:
    first = Path(paths[0]) if paths else Path.cwd()
    if page is None:
        stem = first.stem if len(paths) == 1 else "spritesheet"
        name = f"{stem}_spritesheet_{opts.cols}x{opts.rows}.png"
    else:
        name = f"presentation_sheet_{page:03d}_{opts.cols}x{opts.rows}.png"
    if opts.out_root:
        return Path(opts.out_root) / name
    return first.parent / "showcase" / name


def discover_presentation_library(path: Path | str) -> dict:
    """Discover a generic volume presentation library and its matching media."""
    selected = Path(path).expanduser().resolve()
    root = selected.parent if selected.name == PRESENTATION_LIBRARY_FOLDERS["heroes"] else selected
    heroes_dir = root / PRESENTATION_LIBRARY_FOLDERS["heroes"]
    animations_dir = root / PRESENTATION_LIBRARY_FOLDERS["animations"]
    if not heroes_dir.is_dir():
        raise ValueError(f"presentation library is missing '{heroes_dir.name}': {root}")
    heroes = sorted(heroes_dir.glob("*_Presentation.png"), key=lambda item: item.name.casefold())
    if not heroes:
        raise ValueError(f"presentation library has no hero renders: {heroes_dir}")
    names = [hero.name.removesuffix("_Presentation.png") for hero in heroes]
    gifs = [animations_dir / f"{name}_Turntable.gif" for name in names]
    mp4s = [animations_dir / f"{name}_Turntable.mp4" for name in names]
    return {
        "root": root,
        "heroes_dir": heroes_dir,
        "animations_dir": animations_dir,
        "combined_dir": root / PRESENTATION_LIBRARY_FOLDERS["combined"],
        "sheets_dir": root / PRESENTATION_LIBRARY_FOLDERS["sheets"],
        "heroes": heroes,
        "hero_count": len(heroes),
        "gif_count": sum(candidate.is_file() for candidate in gifs),
        "mp4_count": sum(candidate.is_file() for candidate in mp4s),
        "missing_gifs": [str(candidate) for candidate in gifs if not candidate.is_file()],
        "missing_mp4s": [str(candidate) for candidate in mp4s if not candidate.is_file()],
    }


def partner_candidates(src: Path, opts: ShowcaseOptions) -> tuple[Path, ...]:
    """All paths whose appearance could change before/after pair resolution."""
    if not opts.ba_folder:
        return ()
    folder = Path(opts.ba_folder)
    candidates = [folder / src.name]
    for ext in sorted(IMAGE_EXTS):
        candidates.append(folder / (src.stem + ext))
    return tuple(dict.fromkeys(candidates))


def _partner_path(src: Path, opts: ShowcaseOptions) -> Path | None:
    """The first existing same-stem counterpart under the configured folder."""
    for candidate in partner_candidates(src, opts):
        if candidate.is_file():
            return candidate
    return None


def _atomic_save_png(out, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    out.save(tmp, "PNG")
    tmp.replace(dst)


# --- fallible IO --------------------------------------------------------------

def process(path, opts: ShowcaseOptions) -> Result:
    """Render one file for hero / before_after mode: load -> compose -> save."""
    src = Path(path)
    if not src.is_file():
        return _fail_result(src, f"not a file: {src}", "file.missing")

    suffix = "_hero" if opts.mode == "hero" else "_ba"
    dst = plan_output(src, opts, suffix)

    if opts.mode == "before_after":
        partner = _partner_path(src, opts)
        if partner is None:
            return _fail_result(src, f"no matching counterpart for {src.name}"
                                + (" in second folder" if opts.ba_folder else " (set a second folder)"),
                                "pair.missing")

    if opts.dry_run:
        verb = "frame hero" if opts.mode == "hero" else "compose before/after"
        return Result(str(src), "dry-run", f"would {verb}", out_path=str(dst), input_count=1)

    try:
        from PIL import Image
        if opts.mode == "hero":
            with Image.open(src) as im:
                out = frame_hero(im, opts)
        else:
            partner = _partner_path(src, opts)      # re-resolve (dry_run path skipped it)
            with Image.open(src) as a, Image.open(partner) as b:
                out = before_after(a, b, labels=("Before", "After"),
                                   cell=opts.cell_size, pad=opts.padding, bg=opts.bg_color)
        _atomic_save_png(out, dst)
        digest = sha256_file(dst)
    except ImportError:
        return _fail_result(src, "Pillow not installed — pip install pillow", "dep.missing")
    except Exception as ex:                          # PIL raises many types on bad images
        return _fail_result(src, f"could not render {src.name}: {ex}", "render.failed")

    detail = opts.bg_style if opts.mode == "hero" else "before/after"
    return Result(
        str(src), "rendered", f"{opts.mode} render", out_path=str(dst), detail=detail,
        input_count=1, rendered_count=1, output_sha256=digest,
    )


def build_contact_sheet(paths, opts: ShowcaseOptions) -> Result:
    """Render the aggregate contact sheet from all `paths` into one PNG."""
    paths = list(paths)
    if not paths:
        return Result("(contact sheet)", "failed", "no images to place", detail="input.empty")

    dst = _contact_output(paths, opts)
    if opts.dry_run:
        return Result("(contact sheet)", "dry-run",
                      f"would place {len(paths)} images ({opts.cols} cols)", out_path=str(dst),
                      input_count=len(paths))

    try:
        from PIL import Image
        images, labels, skipped = [], [], []
        for p in paths:
            p = Path(p)
            try:
                with Image.open(p) as im:
                    images.append(im.convert("RGBA").copy())   # copy: handle closes on exit
                labels.append(p.stem)
            except Exception:                                  # one unreadable image, announced below
                skipped.append(p.name)
        if not images:
            return Result("(contact sheet)", "failed",
                          f"no readable images ({len(skipped)} unreadable)", detail="input.unreadable",
                          input_count=len(paths), skipped_count=len(skipped))
        sheet = contact_sheet(images, cols=opts.cols, cell=opts.cell_size, pad=opts.padding,
                              bg=opts.bg_color, labels=labels if opts.labels else None,
                              title=(opts.title.strip() or None))
        _atomic_save_png(sheet, dst)
        digest = sha256_file(dst)
    except ImportError:
        return Result("(contact sheet)", "failed",
                      "Pillow not installed — pip install pillow", detail="dep.missing",
                      input_count=len(paths))
    except Exception as ex:
        return Result("(contact sheet)", "failed", f"could not build contact sheet: {ex}",
                      detail="render.failed", input_count=len(paths))

    reason = f"{len(images)} images placed"
    detail = "ok"
    if skipped:                                            # a fallback that announces itself
        reason += f" — skipped {len(skipped)} unreadable: {', '.join(skipped[:5])}"
        detail = "degraded"
    return Result(
        "(contact sheet)", "rendered", reason, out_path=str(dst), detail=detail,
        input_count=len(paths), rendered_count=len(images), skipped_count=len(skipped),
        output_sha256=digest,
    )


def build_spritesheet(paths, opts: ShowcaseOptions) -> Result:
    """Compose transparent images into framed icons and/or an NxN spritesheet grid."""
    paths = list(paths)
    if not paths:
        return Result("(spritesheet)", "failed", "no images provided", detail="input.empty")

    if not opts.export_sheet and not opts.export_icons:
        return Result(
            "(spritesheet)", "failed",
            "at least one export target (spritesheet or individual icons) must be selected",
            detail="options.no_export_target", input_count=len(paths),
        )

    page_size = max(1, int(opts.cols) * int(opts.rows))
    page_count = (len(paths) + page_size - 1) // page_size if opts.paginate_sheet else 1
    sheet_destinations = [
        _spritesheet_output(paths, opts, page=index + 1 if opts.paginate_sheet else None)
        for index in range(page_count)
    ]
    sheet_dst = sheet_destinations[0]
    first_icon_dst = plan_output(Path(paths[0]), opts, "_icon")
    primary_dst = sheet_dst if opts.export_sheet else first_icon_dst

    if opts.dry_run:
        actions = []
        if opts.export_sheet:
            actions.append(
                f"{page_count} presentation sheet(s) ({opts.cols}x{opts.rows})"
                if opts.paginate_sheet else f"spritesheet ({opts.cols}x{opts.rows})"
            )
        if opts.export_icons:
            actions.append(f"{len(paths)} individual icon(s)")
        verb = " + ".join(actions)
        return Result(
            "(spritesheet)", "dry-run", f"would render {verb}",
            out_path=str(primary_dst), input_count=len(paths),
        )

    try:
        from PIL import Image
        framed_icons = []
        skipped = []
        icon_outputs = []

        for p in paths:
            path = Path(p)
            try:
                with Image.open(path) as im:
                    framed = frame_hero(im, opts)
                    framed_icons.append(framed.copy())
                if opts.export_icons:
                    out_path = plan_output(path, opts, "_icon")
                    _atomic_save_png(framed, out_path)
                    icon_outputs.append(out_path)
            except Exception as ex:
                skipped.append(path.name)

        if not framed_icons:
            return Result(
                "(spritesheet)", "failed",
                f"no readable images ({len(skipped)} unreadable)",
                detail="input.unreadable", input_count=len(paths), skipped_count=len(skipped),
            )

        digest = ""
        output_paths: list[str] = []
        output_hashes: dict[str, str] = {}
        if opts.export_sheet:
            sheet_bg = opts.sheet_bg_style
            if sheet_bg == "match":
                sheet_bg = opts.bg_style
            pages = (
                [framed_icons[index:index + page_size] for index in range(0, len(framed_icons), page_size)]
                if opts.paginate_sheet else [framed_icons]
            )
            for destination, page_icons in zip(sheet_destinations, pages):
                sheet = spritesheet_grid(
                    page_icons,
                    cols=opts.cols,
                    rows=opts.rows,
                    cell_size=opts.cell_size,
                    pad=opts.padding,
                    bg_style=sheet_bg,
                    bg_color=opts.bg_color,
                    repeat_single=opts.repeat_single,
                )
                _atomic_save_png(sheet, destination)
                page_digest = sha256_file(destination)
                output_paths.append(str(destination))
                output_hashes[str(destination)] = page_digest
            digest = output_hashes[str(sheet_dst)]
        elif icon_outputs:
            digest = sha256_file(icon_outputs[0])
            output_paths = [str(path) for path in icon_outputs]
            output_hashes = {str(path): sha256_file(path) for path in icon_outputs}

        parts = []
        if opts.export_sheet:
            parts.append(
                f"{len(sheet_destinations)} paginated {opts.cols}x{opts.rows} sheet(s)"
                if opts.paginate_sheet else f"{opts.cols}x{opts.rows} sheet"
            )
        if opts.export_icons:
            parts.append(f"{len(icon_outputs)} icon(s)")
        summary = " + ".join(parts) + " rendered"
        detail = "ok"
        if skipped:
            summary += f" — skipped {len(skipped)} unreadable: {', '.join(skipped[:5])}"
            detail = "degraded"

        rendered_items = len(framed_icons) if opts.paginate_sheet or opts.export_icons else min(
            len(framed_icons), opts.cols * opts.rows
        )
        return Result(
            "(spritesheet)", "rendered", summary,
            out_path=str(primary_dst), detail=detail,
            input_count=len(paths),
            rendered_count=rendered_items,
            skipped_count=len(skipped),
            output_sha256=digest,
            out_paths=output_paths,
            output_sha256s=output_hashes,
        )
    except ImportError:
        return Result("(spritesheet)", "failed", "Pillow not installed — pip install pillow", detail="dep.missing", input_count=len(paths))
    except Exception as ex:
        return Result("(spritesheet)", "failed", f"could not build spritesheet: {ex}", detail="render.failed", input_count=len(paths))


def validate_result(result: Result, opts: ShowcaseOptions) -> bool:
    """Verify exact output bytes and mode-specific geometry before reuse."""
    if result.action == "dry-run":
        return True
    if (
        result.action != "rendered"
        or not result.out_path
        or not result.output_sha256
        or result.rendered_count <= 0
    ):
        return False
    try:
        from PIL import Image

        output = Path(result.out_path)
        if sha256_file(output) != result.output_sha256:
            return False
        if result.out_paths and result.output_sha256s:
            for value in result.out_paths:
                candidate = Path(value)
                expected_hash = result.output_sha256s.get(value)
                if not expected_hash or not candidate.is_file() or sha256_file(candidate) != expected_hash:
                    return False
        with Image.open(output) as image:
            image.load()
            if image.format != "PNG" or image.width <= 0 or image.height <= 0:
                return False
            if opts.mode == "hero":
                expected = max(32, int(opts.cell_size))
                return image.size == (expected, expected)
            if opts.mode == "contact":
                cols = max(1, int(opts.cols))
                cell = max(8, int(opts.cell_size))
                pad = max(0, int(opts.padding))
                rows = max(1, (result.rendered_count + cols - 1) // cols)
                label_h = _LABEL_H if opts.labels else 0
                header_h = _HEADER_H if opts.title.strip() else 0
                expected = (
                    pad + cols * (cell + pad),
                    header_h + pad + rows * (cell + label_h + pad),
                )
                return image.size == expected
            if opts.mode == "spritesheet":
                if not opts.export_sheet and opts.export_icons:
                    expected = max(32, int(opts.cell_size))
                    return image.size == (expected, expected)
                cols = max(1, int(opts.cols))
                rows = max(1, int(opts.rows))
                cell = max(16, int(opts.cell_size))
                pad = max(0, int(opts.padding))
                expected = (pad + cols * (cell + pad), pad + rows * (cell + pad))
                return image.size == expected
            return opts.mode == "before_after"
    except (ImportError, OSError, TypeError, ValueError):
        return False

"""Pixel Art Studio presentation layer with live Before/After split preview canvas and palette swatches."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageTk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_panel import BaseBatchPanel
from toolbox.icons import Icons
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e
from . import palettes

_DITHER_MAP = {
    "Bayer 4x4 (Classic Console)": "bayer4",
    "Bayer 2x2 (Coarse Crosshatch)": "bayer2",
    "Bayer 8x8 (Fine Ordered)": "bayer8",
    "Floyd-Steinberg (Smooth Diffusion)": "floyd",
    "Atkinson (1984 Macintosh)": "atkinson",
    "None (Cel-Shaded / Flat)": "none",
}
_REVERSE_DITHER_MAP = {v: k for k, v in _DITHER_MAP.items()}


class PixelArtPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Convert Selected"

    def __init__(self, parent, queue_service):
        self._preview_image_orig: Image.Image | None = None
        self._preview_image_thumb: Image.Image | None = None
        self._preview_tk_img: ImageTk.PhotoImage | None = None
        self._split_pos: float = 0.50
        self._show_original_only: bool = False
        self._preview_scheduled: bool = False
        super().__init__(parent, queue_service=queue_service)

    def _build_options_card(self):
        card = c.Card(self, "Pixel Art & Retro Palette Studio", icon=Icons.GRID)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body

        # Row 1: Primary Palette and Dithering selection
        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x")

        ctk.CTkLabel(row1, text="Hardware Palette", text_color=t.TEXT_MAIN, font=t.font(12, "bold")).pack(side="left")
        self._palette_menu = ctk.CTkOptionMenu(
            row1, values=list(palettes.PALETTE_DISPLAY_NAMES.values()), width=240,
            command=self._palette_changed, fg_color=t.ACCENT_BLUE,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._palette_menu.pack(side="left", padx=(8, 16))
        self._palette_menu.set(palettes.PALETTE_DISPLAY_NAMES["pico8"])

        ctk.CTkLabel(row1, text="Dithering", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._dither_menu = ctk.CTkOptionMenu(
            row1, values=list(_DITHER_MAP.keys()), width=210,
            command=lambda _: self._schedule_preview(), fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._dither_menu.pack(side="left", padx=(8, 16))
        self._dither_menu.set("Bayer 4x4 (Classic Console)")

        # Color Swatch Bar
        self._swatch_frame = ctk.CTkFrame(body, fg_color=t.BG_COLOR, height=24, corner_radius=4, border_width=1, border_color=t.CARD_BORDER)
        self._swatch_frame.pack(fill="x", pady=(8, 0))
        self._refresh_swatches("pico8")

        # Row 2: Interactive Before/After Split Preview Canvas
        preview_frame = ctk.CTkFrame(body, fg_color=t.BG_COLOR, corner_radius=6, border_width=1, border_color=t.CARD_BORDER)
        preview_frame.pack(fill="x", pady=(10, 0))

        preview_header = ctk.CTkFrame(preview_frame, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(6, 2))

        ctk.CTkLabel(
            preview_header, text="Interactive Live Before / After Preview",
            text_color=t.TEXT_MAIN, font=t.font(11, "bold")
        ).pack(side="left")

        self._compare_btn = ctk.CTkButton(
            preview_header, text="👁️ Hold to Compare", width=140, height=24,
            fg_color=t.CARD_BORDER, hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN, font=t.font(10, "bold")
        )
        self._compare_btn.pack(side="right", padx=(8, 0))
        self._compare_btn.bind("<ButtonPress-1>", lambda e: self._set_compare(True))
        self._compare_btn.bind("<ButtonRelease-1>", lambda e: self._set_compare(False))

        ctk.CTkLabel(preview_header, text="Split:", text_color=t.TEXT_MUTED, font=t.font(10)).pack(side="right", padx=(0, 4))
        self._split_slider = ctk.CTkSlider(
            preview_header, from_=0.0, to=1.0, width=110, height=14,
            command=self._on_split_changed, fg_color=t.CARD_BORDER, progress_color=t.ACCENT_BLUE
        )
        self._split_slider.set(0.50)
        self._split_slider.pack(side="right", padx=(0, 8))

        self._canvas_width = 720
        self._canvas_height = 200
        self._canvas = ctk.CTkCanvas(
            preview_frame, width=self._canvas_width, height=self._canvas_height,
            bg=t.BG_COLOR, highlightthickness=0
        )
        self._canvas.pack(fill="x", padx=10, pady=(2, 8))
        self._draw_placeholder()

        # Row 3: Studio Sliders & Controls
        sliders_frame = ctk.CTkFrame(body, fg_color="transparent")
        sliders_frame.pack(fill="x", pady=(8, 0))

        r_sl1 = ctk.CTkFrame(sliders_frame, fg_color="transparent")
        r_sl1.pack(fill="x", pady=2)

        # Pixel Size Slider
        self._create_slider(r_sl1, "Pixel Block Size", "pixel_size", 1.0, 24.0, 4.0, format_fn=lambda v: f"{int(v)}px")
        self._create_slider(r_sl1, "Dither Strength", "dither_strength", 0.0, 1.5, 1.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_sl1, "Adaptive Colors", "num_colors", 2.0, 64.0, 16.0, format_fn=lambda v: f"{int(v)}")

        # Row 4: Finishing Toggles
        toggles = ctk.CTkFrame(body, fg_color="transparent")
        toggles.pack(fill="x", pady=(8, 0))

        self._lab_match = ctk.CTkCheckBox(toggles, text="CIELAB Perceptual Color Matching", font=t.font(11), fg_color=t.ACCENT_BLUE, command=self._schedule_preview)
        self._lab_match.select()
        self._lab_match.pack(side="left")

        self._pixel_perfect = ctk.CTkCheckBox(toggles, text="Pixel-Perfect Corners (Prune L-Shapes)", font=t.font(11), fg_color=t.ACCENT_BLUE, command=self._schedule_preview)
        self._pixel_perfect.pack(side="left", padx=16)

        self._outline = ctk.CTkCheckBox(toggles, text="Add 1px Sprite Outline", font=t.font(11), fg_color=t.ACCENT_BLUE, command=self._schedule_preview)
        self._outline.pack(side="left", padx=16)

        self._upscale = ctk.CTkCheckBox(toggles, text="Upscale to Original Resolution", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._upscale.select()
        self._upscale.pack(side="left", padx=16)

        # Row 5: Batch output & run
        self._build_output_row(body, "Output folder (blank = ./pixel_art beside each source)")

        row_bot = ctk.CTkFrame(body, fg_color="transparent")
        row_bot.pack(fill="x", pady=(6, 0))

        self._dry = ctk.CTkCheckBox(row_bot, text="Preview only (dry-run)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.pack(side="left")

        self._mirror = ctk.CTkCheckBox(row_bot, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select()
        self._mirror.pack(side="left", padx=20)

        self._build_run_row(body)

    def _create_slider(self, parent, label: str, key: str, from_val: float, to_val: float, default_val: float, format_fn=None):
        if not hasattr(self, "_slider_vars"):
            self._slider_vars: dict[str, ctk.DoubleVar] = {}
            self._slider_labels: dict[str, ctk.CTkLabel] = {}

        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(side="left", fill="x", expand=True, padx=4)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(header, text=label, text_color=t.TEXT_MUTED, font=t.font(10, "bold")).pack(side="left")
        val_lbl = ctk.CTkLabel(header, text=format_fn(default_val) if format_fn else f"{default_val:.2f}", text_color=t.TEXT_MAIN, font=t.font(10))
        val_lbl.pack(side="right")
        self._slider_labels[key] = val_lbl

        var = ctk.DoubleVar(value=default_val)
        self._slider_vars[key] = var

        def on_change(v):
            val_lbl.configure(text=format_fn(v) if format_fn else f"{v:.2f}")
            self._schedule_preview()

        slider = ctk.CTkSlider(
            frame, from_=from_val, to=to_val, variable=var,
            command=on_change, height=14,
            fg_color=t.CARD_BORDER, progress_color=t.ACCENT_BLUE
        )
        slider.pack(fill="x", pady=(2, 0))

    def _refresh_swatches(self, palette_key: str):
        for w in self._swatch_frame.winfo_children():
            w.destroy()

        hexes = palettes.PALETTES.get(palette_key, palettes.PALETTES["pico8"])
        for h in hexes[:32]:
            sw = ctk.CTkFrame(self._swatch_frame, fg_color=h, width=16, height=16, corner_radius=2)
            sw.pack(side="left", padx=2, pady=3)

    def _palette_changed(self, display_name: str):
        key = next((k for k, v in palettes.PALETTE_DISPLAY_NAMES.items() if v == display_name), "pico8")
        self._refresh_swatches(key)
        self._schedule_preview()

    def _draw_placeholder(self):
        self._canvas.delete("all")
        w, h = self._canvas_width, self._canvas_height
        self._canvas.create_rectangle(0, 0, w, h, fill="#181a20", outline="")
        self._canvas.create_text(
            w / 2, h / 2,
            text="📷 Drop or select an image from the queue to view live Pixel Art split preview",
            fill=t.TEXT_MUTED, font=t.font(11)
        )

    def _on_split_changed(self, val):
        self._split_pos = float(val)
        self._render_split_preview()

    def _set_compare(self, original_only: bool):
        self._show_original_only = original_only
        self._render_split_preview()

    def set_active_preview_image(self, img_path: Path):
        try:
            with Image.open(img_path) as im:
                self._preview_image_orig = im.convert("RGBA")
                thumb = self._preview_image_orig.copy()
                thumb.thumbnail((360, 240), Image.Resampling.BILINEAR)
                self._preview_image_thumb = thumb
            self._schedule_preview()
        except Exception:
            pass

    def _schedule_preview(self):
        if self._preview_scheduled or self._preview_image_thumb is None:
            return
        self._preview_scheduled = True
        self.after(20, self._render_preview_worker)

    def _render_preview_worker(self):
        self._preview_scheduled = False
        if self._preview_image_thumb is None:
            return

        opts = self._collect_options()
        try:
            pixelized = e.pixelize(
                self._preview_image_thumb,
                pixel_size=opts.pixel_size,
                palette=opts.palette,
                num_colors=opts.num_colors,
                dither_method=opts.dither_method,
                dither_strength=opts.dither_strength,
                use_lab=opts.use_lab,
                pixel_perfect=opts.pixel_perfect,
                outline=opts.outline,
                upscale=True
            )
            self._preview_pixelized_thumb = pixelized.convert("RGBA")
            self._render_split_preview()
        except Exception:
            pass

    def _render_split_preview(self):
        if self._preview_image_thumb is None or not hasattr(self, "_preview_pixelized_thumb"):
            return

        cw, ch = self._canvas_width, self._canvas_height
        orig = self._preview_image_thumb
        pix = self._preview_pixelized_thumb

        target_h = ch - 16
        scale = target_h / orig.height
        target_w = int(orig.width * scale)
        if target_w > cw - 20:
            target_w = cw - 20
            scale = target_w / orig.width
            target_h = int(orig.height * scale)

        orig_resized = orig.resize((target_w, target_h), Image.Resampling.BILINEAR)
        pix_resized = pix.resize((target_w, target_h), Image.Resampling.NEAREST)

        if self._show_original_only:
            composite = orig_resized
        else:
            split_x = int(target_w * self._split_pos)
            composite = Image.new("RGBA", (target_w, target_h))
            if split_x > 0:
                composite.paste(orig_resized.crop((0, 0, split_x, target_h)), (0, 0))
            if split_x < target_w:
                composite.paste(pix_resized.crop((split_x, 0, target_w, target_h)), (split_x, 0))

        self._preview_tk_img = ImageTk.PhotoImage(composite)
        self._canvas.delete("all")

        offset_x = (cw - target_w) // 2
        offset_y = (ch - target_h) // 2
        self._canvas.create_image(offset_x, offset_y, anchor="nw", image=self._preview_tk_img)

        if not self._show_original_only and 0 < self._split_pos < 1.0:
            line_x = offset_x + int(target_w * self._split_pos)
            self._canvas.create_line(line_x, offset_y, line_x, offset_y + target_h, fill="#ffffff", width=2)
            self._canvas.create_text(offset_x + 8, offset_y + 14, text="ORIGINAL", fill="#ffffff", anchor="w", font=t.font(9, "bold"))
            self._canvas.create_text(offset_x + target_w - 8, offset_y + 14, text="PIXEL ART", fill=t.ACCENT_BLUE, anchor="e", font=t.font(9, "bold"))

    def _collect_options(self) -> e.PixelOptions:
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None

        palette_name = self._palette_menu.get()
        palette_key = next((k for k, v in palettes.PALETTE_DISPLAY_NAMES.items() if v == palette_name), "pico8")

        dither_name = self._dither_menu.get()
        dither_key = _DITHER_MAP.get(dither_name, "bayer4")

        return e.PixelOptions(
            out_root=out_root,
            input_root=input_root,
            mirror=mirror,
            pixel_size=int(self._slider_vars["pixel_size"].get()),
            palette=palette_key,
            num_colors=int(self._slider_vars["num_colors"].get()),
            dither_method=dither_key,
            dither_strength=float(self._slider_vars["dither_strength"].get()),
            use_lab=bool(self._lab_match.get()),
            pixel_perfect=bool(self._pixel_perfect.get()),
            outline=bool(self._outline.get()),
            upscale=bool(self._upscale.get()),
            dry_run=bool(self._dry.get())
        )

    def _on_files_dropped_or_selected(self, files: list[Path]):
        if files:
            self.set_active_preview_image(files[0])

    def _build_submission(self, files: list[Path], opts: e.PixelOptions) -> QueueSubmission:
        if files and self._preview_image_orig is None:
            self.set_active_preview_image(files[0])

        definition = JobDefinition.create(
            tool_id="pixel_art",
            tool_version="2",
            workflow_version="pixel-studio.v2",
            inputs=files,
            settings=asdict(opts),
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "converted":
                return ItemOutcome.completed(data, res.reason)
            if res.action == "dry-run":
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Pixel Art Studio · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="pixel_art"
            ),
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined")

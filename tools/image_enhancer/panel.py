"""Image Enhancer presentation layer with live Before/After split preview canvas, Privacy Shield, and batch queue."""
from __future__ import annotations

import threading
import time
import webbrowser
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
from . import filter_stack
from . import model_rack
from . import smart

_PRESET_MAP = {
    "Auto (Recommended)": "auto",
    "Natural Skin / De-Gloss": "natural_skin",
    "De-Gloss / De-Shine": "de_gloss",
    "Vivid Pop": "vivid_pop",
    "Warm Sunset": "warm_sunset",
    "Cinematic Teal": "cinematic_teal",
    "Soft Glamour / Glow": "soft_glamour",
    "Moody Film": "moody_film",
    "🔍 CLAHE Texture Extractor": "clahe_texture",
    "✨ Auto White-Balance": "auto_white_balance",
    "🧹 Despeckle AI Artifacts": "despeckle_clean",
    "🎯 Radial / Tilt-Shift Focus": "radial_focus",
    "🛡️ Privacy Censor Blur": "privacy_censor",
    "🔲 Mosaic Pixelate Censor": "pixelate_censor",
    "Retinex Dehaze & Range": "retinex_dehaze",
    "Shadows & Highlights": "shadows_highlights",
    "Gentle Restore": "gentle_restore",
    "Clarity & Detail": "detail",
    "Portrait Polish": "portrait_polish",
    "Texture Cleanup": "texture_cleanup",
    "B&W Contrast": "bw_contrast",
    "Color Recovery": "colour_restore",
    "Custom (Manual)": "custom",
}
_REVERSE_PRESET_MAP = {v: k for k, v in _PRESET_MAP.items()}


class ModelRackDialog(ctk.CTkToplevel):
    """Interactive dialog displaying all specialist AI micro-models and download links."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("AI Filter Model Rack & Download Hub")
        self.geometry("780x560")
        self.configure(fg_color=t.BG_COLOR)
        self.attributes("-topmost", True)

        header = ctk.CTkFrame(self, fg_color=t.CARD_BG)
        header.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(
            header, text="Specialist Micro-Models Rack",
            text_color=t.TEXT_MAIN, font=t.font(16, "bold")
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            header,
            text="Tiny specialist models (~1–20 MB) that run beside deterministic filters. Download only what you need.",
            text_color=t.TEXT_MUTED, font=t.font(11)
        ).pack(anchor="w", padx=14, pady=(0, 10))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=16, pady=8)
        self._refresh_list()

    def _refresh_list(self):
        for widget in self._scroll.winfo_children():
            widget.destroy()

        specs = model_rack.list_models()
        for spec in specs:
            card = ctk.CTkFrame(self._scroll, fg_color=t.CARD_BG, corner_radius=6, border_width=1, border_color=t.CARD_BORDER)
            card.pack(fill="x", pady=5)

            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=12, pady=10)

            title_row = ctk.CTkFrame(info_frame, fg_color="transparent")
            title_row.pack(fill="x")

            ctk.CTkLabel(
                title_row, text=f" {spec.category} ",
                fg_color=t.ACCENT_BLUE if spec.is_installed() else t.NEUTRAL_HOVER,
                text_color="#ffffff", font=t.font(9, "bold"), corner_radius=4
            ).pack(side="left", padx=(0, 8))

            ctk.CTkLabel(
                title_row, text=spec.name,
                text_color=t.TEXT_MAIN, font=t.font(12, "bold")
            ).pack(side="left")

            ctk.CTkLabel(
                title_row, text=f"({spec.size_mb:.1f} MB)",
                text_color=t.TEXT_MUTED, font=t.font(11)
            ).pack(side="left", padx=(6, 0))

            ctk.CTkLabel(
                info_frame, text=spec.description,
                text_color=t.TEXT_MUTED, font=t.font(11), wraplength=480, justify="left"
            ).pack(anchor="w", pady=(4, 0))

            act_frame = ctk.CTkFrame(card, fg_color="transparent")
            act_frame.pack(side="right", padx=12, pady=10)

            if spec.is_installed():
                ctk.CTkLabel(
                    act_frame, text="✅ Installed",
                    text_color=t.STATE["done"][1], font=t.font(11, "bold")
                ).pack(pady=(0, 4))
            else:
                ctk.CTkLabel(
                    act_frame, text="⬇️ Not Installed",
                    text_color=t.TEXT_MUTED, font=t.font(11)
                ).pack(pady=(0, 4))

                dl_btn = ctk.CTkButton(
                    act_frame, text="Download", width=90, height=26,
                    fg_color=t.ACCENT_BLUE, font=t.font(10, "bold"),
                    command=lambda s=spec: self._start_download(s)
                )
                dl_btn.pack(pady=(0, 4))

            btn_row = ctk.CTkFrame(act_frame, fg_color="transparent")
            btn_row.pack()

            copy_btn = ctk.CTkButton(
                btn_row, text="Copy Link", width=70, height=22,
                fg_color=t.BG_COLOR, text_color=t.TEXT_MAIN, font=t.font(9),
                command=lambda url=spec.download_url: self._copy_link(url)
            )
            copy_btn.pack(side="left", padx=(0, 4))

            if spec.docs_url:
                docs_btn = ctk.CTkButton(
                    btn_row, text="Docs", width=50, height=22,
                    fg_color=t.BG_COLOR, text_color=t.TEXT_MAIN, font=t.font(9),
                    command=lambda url=spec.docs_url: webbrowser.open(url)
                )
                docs_btn.pack(side="left")

    def _copy_link(self, url: str):
        self.clipboard_clear()
        self.clipboard_append(url)
        self.update()

    def _start_download(self, spec: model_rack.ModelSpec):
        progress_win = ctk.CTkToplevel(self)
        progress_win.title(f"Downloading {spec.name}")
        progress_win.geometry("420x150")
        progress_win.attributes("-topmost", True)

        lbl = ctk.CTkLabel(progress_win, text=f"Downloading {spec.name}...\n({spec.size_mb:.1f} MB)", font=t.font(12))
        lbl.pack(pady=(20, 10))

        bar = ctk.CTkProgressBar(progress_win, width=320)
        bar.pack(pady=10)
        bar.set(0)

        def runner():
            def cb(frac, cur, total):
                self.after(0, lambda: bar.set(frac))

            ok, msg = model_rack.download_model(spec.id, progress_callback=cb)
            self.after(0, lambda: progress_win.destroy())
            self.after(0, self._refresh_list)

        threading.Thread(target=runner, daemon=True).start()


class ImageEnhancerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BOLT
    RUN_LABEL = "Enhance Selected"

    def __init__(self, parent, queue_service):
        self._preview_image_orig: Image.Image | None = None
        self._preview_image_thumb: Image.Image | None = None
        self._preview_tk_img: ImageTk.PhotoImage | None = None
        self._split_pos: float = 0.50  # 0.0 to 1.0
        self._show_original_only: bool = False
        self._privacy_shield_active: bool = False
        self._preview_scheduled: bool = False
        super().__init__(parent, queue_service=queue_service)

    def _build_options_card(self):
        card = c.Card(self, "Image Enhancement Studio & Batch Processor", icon=Icons.BOLT)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body

        # Row 1: Primary One-Button Preset, Mode, and Model Rack Hub
        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x")

        ctk.CTkLabel(row1, text="Preset", text_color=t.TEXT_MAIN, font=t.font(12, "bold")).pack(side="left")
        self._preset_display = ctk.CTkOptionMenu(
            row1, values=list(_PRESET_MAP.keys()), width=240,
            command=self._preset_changed, fg_color=t.ACCENT_BLUE,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._preset_display.pack(side="left", padx=(8, 16))
        self._preset_display.set("Auto (Recommended)")

        ctk.CTkLabel(row1, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._mode = ctk.CTkOptionMenu(
            row1, values=["Deterministic", "Hybrid", "AI only"], width=130,
            command=self._mode_changed, fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._mode.pack(side="left", padx=(8, 16))
        self._mode.set("Deterministic")

        ctk.CTkLabel(row1, text="Scale", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._scale = ctk.CTkOptionMenu(
            row1, values=["1", "2", "3", "4"], width=64,
            command=lambda _: self._schedule_preview(),
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._scale.pack(side="left", padx=(8, 16))
        self._scale.set("1")

        ctk.CTkLabel(row1, text="Model", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._model = ctk.CTkOptionMenu(
            row1, values=["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3-x2"], width=180,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._model.pack(side="left", padx=(8, 12))
        self._model.set("realesrgan-x4plus")

        rack_btn = ctk.CTkButton(
            row1, text="📦 Model Rack", width=110, height=28,
            fg_color=t.CARD_BORDER, hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN, font=t.font(11, "bold"),
            command=lambda: ModelRackDialog(self)
        )
        rack_btn.pack(side="left")

        # Row 2: Interactive Before/After Split Preview Canvas + Privacy Shield
        preview_frame = ctk.CTkFrame(body, fg_color=t.BG_COLOR, corner_radius=6, border_width=1, border_color=t.CARD_BORDER)
        preview_frame.pack(fill="x", pady=(10, 0))

        preview_header = ctk.CTkFrame(preview_frame, fg_color="transparent")
        preview_header.pack(fill="x", padx=10, pady=(6, 2))

        ctk.CTkLabel(
            preview_header, text="Interactive Live Before / After Preview",
            text_color=t.TEXT_MAIN, font=t.font(11, "bold")
        ).pack(side="left")

        # Privacy Shield Toggle
        self._shield_btn = ctk.CTkButton(
            preview_header, text="🛡️ Privacy Shield", width=120, height=24,
            fg_color=t.CARD_BORDER, hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN, font=t.font(10, "bold"),
            command=self._toggle_privacy_shield
        )
        self._shield_btn.pack(side="right", padx=(8, 0))

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

        # Canvas for split rendering
        self._canvas_width = 720
        self._canvas_height = 200
        self._canvas = ctk.CTkCanvas(
            preview_frame, width=self._canvas_width, height=self._canvas_height,
            bg=t.BG_COLOR, highlightthickness=0
        )
        self._canvas.pack(fill="x", padx=10, pady=(2, 8))
        self._draw_placeholder()

        # Row 3: Android-Style Studio Sliders
        sliders_card = ctk.CTkFrame(body, fg_color="transparent")
        sliders_card.pack(fill="x", pady=(8, 0))

        self._slider_vars: dict[str, ctk.DoubleVar] = {}
        self._slider_labels: dict[str, ctk.CTkLabel] = {}

        tabview = ctk.CTkTabview(sliders_card, height=140, fg_color=t.CARD_BG)
        tabview.pack(fill="x")
        t_light = tabview.add("Light & Color")
        t_skin = tabview.add("Skin & Texture")
        t_fx = tabview.add("Creative & Privacy Blur")
        t_opts = tabview.add("Target Regions & Repair")

        # Tab 1: Light & Color
        r_l1 = ctk.CTkFrame(t_light, fg_color="transparent")
        r_l1.pack(fill="x", pady=2)
        self._create_slider(r_l1, "Brightness", "brightness", 0.5, 1.5, 1.0, format_fn=lambda v: f"{v:.2f}x")
        self._create_slider(r_l1, "Contrast", "contrast", 0.5, 1.8, 1.0, format_fn=lambda v: f"{v:.2f}x")
        self._create_slider(r_l1, "Gamma", "gamma", 0.5, 2.0, 1.0, format_fn=lambda v: f"{v:.2f}")
        self._create_slider(r_l1, "CLAHE", "clahe_strength", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")

        r_l2 = ctk.CTkFrame(t_light, fg_color="transparent")
        r_l2.pack(fill="x", pady=2)
        self._create_slider(r_l2, "Saturation", "saturation", 0.0, 2.0, 1.0, format_fn=lambda v: f"{v:.2f}x")
        self._create_slider(r_l2, "Vibrance", "vibrance", -1.0, 1.0, 0.0, format_fn=lambda v: f"{v:+.2f}")
        self._create_slider(r_l2, "Auto-WB", "auto_wb_strength", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_l2, "Temp (K)", "temperature", -1.0, 1.0, 0.0, format_fn=lambda v: f"{v:+.2f}")
        self._create_slider(r_l2, "Tint", "tint", -1.0, 1.0, 0.0, format_fn=lambda v: f"{v:+.2f}")

        # Tab 2: Skin & Surface
        r_s1 = ctk.CTkFrame(t_skin, fg_color="transparent")
        r_s1.pack(fill="x", pady=2)
        self._create_slider(r_s1, "De-Gloss", "de_gloss_strength", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_s1, "Pore Texture", "micro_texture_amount", 0.0, 0.08, 0.0, format_fn=lambda v: f"{v:.3f}")
        self._create_slider(r_s1, "Despeckle", "despeckle_strength", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_s1, "Denoise", "denoise", 0.0, 2.0, 0.0, format_fn=lambda v: f"{v:.2f}px")

        r_s2 = ctk.CTkFrame(t_skin, fg_color="transparent")
        r_s2.pack(fill="x", pady=2)
        self._create_slider(r_s2, "Sharpen", "sharpen", 0.0, 2.5, 1.0, format_fn=lambda v: f"{v:.2f}x")
        self._create_slider(r_s2, "High-Pass", "high_pass", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_s2, "Edge Boost", "edge_boost", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")

        # Tab 3: Creative & Privacy Blur
        r_fx1 = ctk.CTkFrame(t_fx, fg_color="transparent")
        r_fx1.pack(fill="x", pady=2)
        self._create_slider(r_fx1, "Soft Glow", "soft_glow", 0.0, 0.8, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_fx1, "Clarity", "clarity", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_fx1, "Vignette", "vignette", 0.0, 0.8, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_fx1, "Film Grain", "film_grain", 0.0, 0.10, 0.0, format_fn=lambda v: f"{v:.3f}")

        r_fx2 = ctk.CTkFrame(t_fx, fg_color="transparent")
        r_fx2.pack(fill="x", pady=2)
        self._create_slider(r_fx2, "Tilt-Shift Focus", "radial_focus", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_fx2, "Privacy Blur", "privacy_blur", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")
        self._create_slider(r_fx2, "Mosaic Pixelate", "pixelate_block", 0.0, 32.0, 0.0, format_fn=lambda v: f"{int(v)}px")
        self._create_slider(r_fx2, "Split Tone", "split_tone", 0.0, 1.0, 0.0, format_fn=lambda v: f"{int(v*100)}%")

        # Tab 4: Target Regions & Repair
        r_reg = ctk.CTkFrame(t_opts, fg_color="transparent")
        r_reg.pack(fill="x", pady=4)
        ctk.CTkLabel(r_reg, text="Target Region", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._region = ctk.CTkOptionMenu(
            r_reg, values=["none", "faces", "subject_mask", "manual_box"], width=130,
            command=self._region_changed, fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._region.pack(side="left", padx=(8, 16))
        self._region.set("none")

        self._box_label = ctk.CTkLabel(r_reg, text="Box x,y,w,h (%)", text_color=t.TEXT_MUTED, font=t.font(11))
        self._box = c.entry(r_reg, width=140)
        self._box.insert(0, "25,25,50,50")

        self._face_detail = ctk.CTkCheckBox(r_reg, text="Face-detail Refinement", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._face_detail.pack(side="left", padx=(12, 16))

        self._repair = ctk.CTkCheckBox(r_reg, text="Repair Alpha Holes", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._repair.pack(side="left", padx=12)

        self._debug = ctk.CTkCheckBox(r_reg, text="Debug Masks", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._debug.pack(side="left", padx=12)

        # Bottom row options
        row_bot = ctk.CTkFrame(body, fg_color="transparent")
        row_bot.pack(fill="x", pady=(8, 0))

        self._auto_mode = ctk.CTkCheckBox(row_bot, text="Auto-detect flaw severity per image", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._auto_mode.select()
        self._auto_mode.pack(side="left")

        self._dry = ctk.CTkCheckBox(row_bot, text="Preview only (dry-run)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.pack(side="left", padx=20)

        self._mirror = ctk.CTkCheckBox(row_bot, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select()
        self._mirror.pack(side="left", padx=20)

        reset_btn = ctk.CTkButton(
            row_bot, text="↺ Reset Sliders", width=110, height=24,
            fg_color=t.BG_COLOR, hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN, font=t.font(10),
            command=self._reset_all_sliders
        )
        reset_btn.pack(side="right")

        # Telemetry & Status
        self._status = ctk.CTkLabel(body, text="", text_color=t.TEXT_MUTED, font=t.font(11), anchor="w")
        self._status.pack(fill="x", pady=(8, 0))
        self._refresh_status()

        self._build_output_row(body, "Output folder (blank = ./enhanced beside each source)")
        self._build_run_row(body)

        self._region_changed("none")
        self._mode_changed("Deterministic")

    def _create_slider(self, parent, label: str, key: str, from_val: float, to_val: float, default_val: float, format_fn=None):
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
        slider.bind("<Double-Button-1>", lambda e, k=key, d=default_val: self._reset_slider(k, d, format_fn))

    def _reset_slider(self, key: str, default_val: float, format_fn=None):
        if key in self._slider_vars:
            self._slider_vars[key].set(default_val)
            if key in self._slider_labels:
                self._slider_labels[key].configure(text=format_fn(default_val) if format_fn else f"{default_val:.2f}")
            self._schedule_preview()

    def _reset_all_sliders(self):
        defaults = {
            "brightness": 1.0, "contrast": 1.0, "gamma": 1.0, "saturation": 1.0,
            "vibrance": 0.0, "temperature": 0.0, "tint": 0.0, "de_gloss_strength": 0.0,
            "micro_texture_amount": 0.0, "denoise": 0.0, "sharpen": 1.0, "high_pass": 0.0,
            "edge_boost": 0.0, "soft_glow": 0.0, "clarity": 0.0, "vignette": 0.0,
            "film_grain": 0.0, "split_tone": 0.0, "privacy_blur": 0.0, "pixelate_block": 0.0,
            "radial_focus": 0.0, "clahe_strength": 0.0, "auto_wb_strength": 0.0, "despeckle_strength": 0.0,
        }
        for k, v in defaults.items():
            if k in self._slider_vars:
                self._slider_vars[k].set(v)
                if k in self._slider_labels:
                    self._slider_labels[k].configure(text=f"{v:.2f}")
        self._preset_display.set("Auto (Recommended)")
        self._schedule_preview()

    def _toggle_privacy_shield(self):
        self._privacy_shield_active = not self._privacy_shield_active
        if self._privacy_shield_active:
            self._shield_btn.configure(fg_color=t.ACCENT_BLUE, text="🛡️ Shield ACTIVE")
        else:
            self._shield_btn.configure(fg_color=t.CARD_BORDER, text="🛡️ Privacy Shield")
        self._render_split_preview()

    def _draw_placeholder(self):
        self._canvas.delete("all")
        w, h = self._canvas_width, self._canvas_height
        self._canvas.create_rectangle(0, 0, w, h, fill="#181a20", outline="")
        self._canvas.create_text(
            w / 2, h / 2,
            text="📷 Drop or select an image from the queue to view interactive Live Before / After split",
            fill=t.TEXT_MUTED, font=t.font(11)
        )

    def _on_split_changed(self, val):
        self._split_pos = float(val)
        self._render_split_preview()

    def _set_compare(self, original_only: bool):
        self._show_original_only = original_only
        self._render_split_preview()

    def set_active_preview_image(self, img_path: Path):
        """Called when user selects or adds files to preview."""
        try:
            with Image.open(img_path) as im:
                self._preview_image_orig = im.convert("RGB")
                im_thumb = self._preview_image_orig.copy()
                im_thumb.thumbnail((480, 320), Image.Resampling.BILINEAR)
                self._preview_image_thumb = im_thumb
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

        opts = self._collect_options_preview()
        if not opts:
            return

        try:
            enhanced, _ = e._apply_stack(self._preview_image_thumb, opts)
            self._preview_enhanced_thumb = enhanced.convert("RGB")
            self._render_split_preview()
        except Exception:
            pass

    def _render_split_preview(self):
        if self._preview_image_thumb is None or not hasattr(self, "_preview_enhanced_thumb"):
            return

        cw, ch = self._canvas_width, self._canvas_height
        orig = self._preview_image_thumb
        enh = self._preview_enhanced_thumb

        # Scale fit to canvas
        target_h = ch - 16
        scale = target_h / orig.height
        target_w = int(orig.width * scale)
        if target_w > cw - 20:
            target_w = cw - 20
            scale = target_w / orig.width
            target_h = int(orig.height * scale)

        orig_resized = orig.resize((target_w, target_h), Image.Resampling.BILINEAR)
        enh_resized = enh.resize((target_w, target_h), Image.Resampling.BILINEAR)

        # Composite split
        if self._privacy_shield_active:
            from PIL import ImageFilter
            composite = enh_resized.filter(ImageFilter.GaussianBlur(32.0))
        elif self._show_original_only:
            composite = orig_resized
        else:
            split_x = int(target_w * self._split_pos)
            composite = Image.new("RGB", (target_w, target_h))
            if split_x > 0:
                composite.paste(orig_resized.crop((0, 0, split_x, target_h)), (0, 0))
            if split_x < target_w:
                composite.paste(enh_resized.crop((split_x, 0, target_w, target_h)), (split_x, 0))

        self._preview_tk_img = ImageTk.PhotoImage(composite)
        self._canvas.delete("all")

        offset_x = (cw - target_w) // 2
        offset_y = (ch - target_h) // 2
        self._canvas.create_image(offset_x, offset_y, anchor="nw", image=self._preview_tk_img)

        if self._privacy_shield_active:
            self._canvas.create_text(
                cw / 2, ch / 2,
                text="🛡️ PRIVACY SHIELD ACTIVE (Image Blurred)",
                fill="#ffffff", font=t.font(12, "bold")
            )
        elif not self._show_original_only and 0 < self._split_pos < 1.0:
            line_x = offset_x + int(target_w * self._split_pos)
            self._canvas.create_line(line_x, offset_y, line_x, offset_y + target_h, fill="#ffffff", width=2)
            self._canvas.create_text(offset_x + 8, offset_y + 14, text="BEFORE", fill="#ffffff", anchor="w", font=t.font(9, "bold"))
            self._canvas.create_text(offset_x + target_w - 8, offset_y + 14, text="AFTER", fill=t.ACCENT_BLUE, anchor="e", font=t.font(9, "bold"))

    def _preset_changed(self, display_name: str):
        preset_key = _PRESET_MAP.get(display_name, "auto")
        if preset_key == "de_gloss":
            self._set_val("de_gloss_strength", 0.85)
        elif preset_key == "natural_skin":
            self._set_val("de_gloss_strength", 0.75)
            self._set_val("micro_texture_amount", 0.035)
        elif preset_key == "vivid_pop":
            self._set_val("contrast", 1.10)
            self._set_val("saturation", 1.18)
            self._set_val("vibrance", 0.15)
            self._set_val("clarity", 0.25)
        elif preset_key == "clahe_texture":
            self._set_val("clahe_strength", 0.80)
            self._set_val("clarity", 0.25)
        elif preset_key == "auto_white_balance":
            self._set_val("auto_wb_strength", 1.0)
        elif preset_key == "despeckle_clean":
            self._set_val("despeckle_strength", 0.85)
            self._set_val("denoise", 0.25)
        elif preset_key == "soft_glamour":
            self._set_val("de_gloss_strength", 0.65)
            self._set_val("soft_glow", 0.28)
        elif preset_key == "warm_sunset":
            self._set_val("temperature", 0.22)
            self._set_val("soft_glow", 0.18)
            self._set_val("vignette", 0.25)
        elif preset_key == "cinematic_teal":
            self._set_val("split_tone", 0.35)
            self._set_val("clarity", 0.20)
            self._set_val("vignette", 0.30)
            self._set_val("film_grain", 0.025)
        elif preset_key == "radial_focus":
            self._set_val("radial_focus", 0.80)
            self._set_val("vignette", 0.25)
        elif preset_key == "privacy_censor":
            self._set_val("privacy_blur", 0.85)
        elif preset_key == "pixelate_censor":
            self._set_val("pixelate_block", 20.0)
        elif preset_key == "bw_contrast":
            self._set_val("saturation", 0.0)
            self._set_val("contrast", 1.25)
            self._set_val("clarity", 0.35)
            self._set_val("vignette", 0.35)
        elif preset_key == "auto":
            pass
        self._schedule_preview()

    def _set_val(self, key: str, val: float):
        if key in self._slider_vars:
            self._slider_vars[key].set(val)
            if key in self._slider_labels:
                self._slider_labels[key].configure(text=f"{val:.2f}")

    def _region_changed(self, mode: str):
        if mode == "manual_box":
            self._box_label.pack(side="left", padx=(0, 6))
            self._box.pack(side="left")
        else:
            self._box_label.pack_forget()
            self._box.pack_forget()
        if mode != "faces":
            self._face_detail.deselect()

    def _mode_changed(self, mode: str):
        deterministic = mode == "Deterministic"
        self._scale.configure(state="disabled" if deterministic else "normal")
        self._model.configure(state="disabled" if deterministic else "normal")
        self._face_detail.configure(state="disabled" if deterministic else "normal")
        if deterministic:
            self._scale.set("1")
            self._face_detail.deselect()
        if hasattr(self, "_status"):
            self._refresh_status()

    def _refresh_status(self):
        state = e.utility_status()
        rack = model_rack.get_rack_status()
        ready = all(bool(state[key]) for key in ("upscale_ready", "segmentation_ready", "faces_ready", "repair_ready"))
        contract = "Deterministic: Fast CPU frequency separation" if self._mode.get() == "Deterministic" else "Hybrid: Utility models on demand" if self._mode.get() == "Hybrid" else "AI-only: Model restoration pass"
        self._status.configure(
            text=f"{contract} · {state['details']} · Model Rack: {rack['installed_count']}/{rack['total']} installed",
            text_color=t.STATE["done"][1] if ready else t.STATE["waiting"][1]
        )

    def _collect_options_preview(self) -> e.EnhanceOptions:
        preset_key = _PRESET_MAP.get(self._preset_display.get(), "auto")
        vals = {k: var.get() for k, var in self._slider_vars.items()}
        return e.EnhanceOptions(
            preset=preset_key,
            mode="deterministic",
            **vals
        )

    def _collect_options(self):
        preset_key = _PRESET_MAP.get(self._preset_display.get(), "auto")
        region = self._region.get()
        if self._face_detail.get() and region == "none":
            region = "faces"

        box = None
        if region == "manual_box":
            try:
                box = tuple(float(value.strip()) for value in self._box.get().split(","))
                if len(box) != 4:
                    raise ValueError
            except ValueError:
                self._logline("Manual box must be x,y,width,height percentages.", t.STATE["error"][1])
                return None

        try:
            scale = int(self._scale.get())
        except ValueError:
            return None

        advanced = {key: float(var.get()) for key, var in self._slider_vars.items()}

        state = e.utility_status()
        chosen_mode = {"Deterministic": "deterministic", "Hybrid": "hybrid", "AI only": "ai"}[self._mode.get()]
        may_use_model = chosen_mode == "ai" or (chosen_mode == "hybrid" and (scale > 1 or self._auto_mode.get()))

        if may_use_model and not state["upscale_ready"]:
            self._logline("AI upscale is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if region == "subject_mask" and not state["segmentation_ready"]:
            self._logline("Subject segmentation is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if (region == "faces" or self._face_detail.get()) and not state["faces_ready"]:
            self._logline("Face detail is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if self._repair.get() and not state["repair_ready"]:
            self._logline("Local repair is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None

        out = self._out_entry.get().strip()
        return e.EnhanceOptions(
            out_root=Path(out) if out else None,
            input_root=self._resolve_input_root() if self._mirror.get() else None,
            mirror=bool(self._mirror.get()),
            preset=preset_key,
            scale_factor=scale,
            ai_model=self._model.get(),
            mode=chosen_mode,
            auto_select_mode=bool(self._auto_mode.get()),
            region_mode=region,
            manual_box=box,
            face_detail=bool(self._face_detail.get()),
            repair_alpha_holes=bool(self._repair.get()),
            debug_outputs=bool(self._debug.get()),
            dry_run=bool(self._dry.get()),
            **advanced,
        )

    def _on_files_dropped_or_selected(self, files: list[Path]):
        if files:
            self.set_active_preview_image(files[0])

    def _build_submission(self, files: list[Path], opts: e.EnhanceOptions) -> QueueSubmission:
        if files and self._preview_image_orig is None:
            self.set_active_preview_image(files[0])

        dependencies = []
        if opts.mode == "ai" or opts.auto_select_mode or opts.scale_factor > 1 or opts.face_detail:
            dependencies.extend([
                e._REAL_ESRGAN / "realesrgan-ncnn-vulkan.exe",
                e._REAL_ESRGAN / "models" / f"{opts.ai_model}.bin",
                e._REAL_ESRGAN / "models" / f"{opts.ai_model}.param"
            ])
        if opts.region_mode == "subject_mask":
            dependencies.append(e._U2NETP)
        if opts.region_mode == "faces" or opts.face_detail:
            dependencies.append(e._YUNET)

        definition = JobDefinition.create(
            tool_id="image_enhancer", tool_version="3", workflow_version="smart-enhance.v3",
            inputs=files, settings=asdict(opts), identity_dependencies=dependencies
        )

        def classify(result: e.Result) -> ItemOutcome:
            if result.action == "enhanced":
                return ItemOutcome.completed(result.to_dict(), result.reason)
            if result.action == "needs-review":
                return ItemOutcome.warning(result.to_dict(), result.reason)
            if result.action == "dry-run":
                return ItemOutcome.skipped(result.to_dict(), result.reason)
            return ItemOutcome.failed(result.reason, data=result.to_dict())

        return QueueSubmission(
            definition=definition, label=f"Enhance · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts, cancelled=lambda: token.is_cancelled),
            classify=classify,
            validate_stored=lambda item: e.validate_result(self._result_from_record(item)),
            finalize=lambda report: self._prepare_queue_completion(report, opts, tool_id="image_enhancer"),
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined")

    def _queue_complete(self, completion: QueueCompletion) -> None:
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        completed = sum(item.action == "enhanced" for item in results)
        review = sum(item.action == "needs-review" for item in results)
        skipped = sum(item.action == "dry-run" for item in results)
        failed = len(results) - completed - skipped - review

        self._finish_queue_ui(
            f"enhanced {completed} · review {review} · previewed {skipped} · failed {failed}",
            job_state=self._queue_completion_state(completion),
            recovered=completion.report.recovered,
            reused=completion.report.reused,
            manifest=payload.manifest,
            report_path=payload.report_path
        )

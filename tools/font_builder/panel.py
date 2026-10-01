"""Font Builder — the tool's UI.

Thin presentation layer over engine.py via BaseBatchPanel:
Collects glyph image/SVG files, font metadata and metric options,
and compiles them into an installable TrueType (.ttf) font.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_panel import BaseBatchPanel
from toolbox.icons import Icons
from . import engine as e


class FontBuilderPanel(BaseBatchPanel):
    FILE_EXTS = e.SUPPORTED_EXTS
    FILE_LABEL = "glyph"
    RESULTS_ICON = Icons.LAYERS
    RUN_LABEL = "Preview & Build Font"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Font Metadata & Metrics", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # Row 1: Font Name + Style
        row1 = ctk.CTkFrame(b, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))
        
        ctk.CTkLabel(row1, text="Font Name", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._font_name = c.entry(row1, width=180)
        self._font_name.insert(0, "MyCustomFont")
        self._font_name.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(row1, text="Style", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._style_menu = ctk.CTkOptionMenu(
            row1,
            values=["Regular", "Bold", "Italic", "Light"],
            width=100,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._style_menu.set("Regular")
        self._style_menu.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(row1, text="Units Per Em", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._upm_menu = ctk.CTkOptionMenu(
            row1,
            values=["1000", "2048"],
            width=80,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._upm_menu.set("1000")
        self._upm_menu.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # Row 2: Metrics (Cap Height, Spacing, Ascent, Descent)
        row2 = ctk.CTkFrame(b, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(row2, text="Cap Height", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._cap_height = c.entry(row2, width=80)
        self._cap_height.insert(0, "700")
        self._cap_height.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(row2, text="Side Spacing (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._spacing = c.entry(row2, width=100)
        self._spacing.insert(0, "40")
        self._spacing.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(row2, text="Speckle Filter", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._speckle = c.entry(row2, width=80)
        self._speckle.insert(0, "4")
        self._speckle.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # Output folder row
        self._build_output_row(b, "Output folder (blank = folder beside source glyphs)")

        # Toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent")
        toggles.pack(fill="x", pady=(10, 0))

        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (inspect detected glyphs)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select()
        self._dry.pack(side="left")

        self._auto_desc = ctk.CTkCheckBox(toggles, text="Auto-align descenders (g, y, p, q)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._auto_desc.select()
        self._auto_desc.pack(side="left", padx=16)

        self._monospace = ctk.CTkCheckBox(toggles, text="Monospace (fixed width)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._monospace.pack(side="left", padx=16)

        # Run row (Start, Pause, Cancel, Progress)
        self._build_run_row(b)

    def _collect_options(self) -> e.FontOptions | None:
        font_name = self._font_name.get().strip() or "CustomFont"
        style_name = self._style_menu.get()
        try:
            upm = int(self._upm_menu.get() or "1000")
            cap_h = int(self._cap_height.get() or "700")
            spacing = int(self._spacing.get() or "40")
            speckle = int(self._speckle.get() or "4")
        except ValueError:
            self._logline("Metrics and speckle filter must be valid integers.", t.STATE["error"][1])
            return None

        out_str = self._out_entry.get().strip()
        out_root = Path(out_str) if out_str else None

        return e.FontOptions(
            font_name=font_name,
            family_name=font_name,
            style_name=style_name,
            units_per_em=upm,
            cap_height=cap_h,
            ascent=int(upm * 0.8),
            descent=-int(upm * 0.2),
            side_bearing=spacing,
            filter_speckle=speckle,
            monospace=bool(self._monospace.get()),
            auto_descenders=bool(self._auto_desc.get()),
            out_root=out_root,
            dry_run=bool(self._dry.get()),
        )

    def _pre_run_check(self, opts: e.FontOptions) -> bool:
        if not self._files:
            self._logline("No glyph image or SVG files selected.", t.STATE["error"][1])
            return False

        if importlib.util.find_spec("fontTools") is None:
            self._logline("fontTools is not installed — pip install fonttools.", t.STATE["error"][1])
            return False

        if opts.dry_run:
            return True

        # Scan for recognized glyphs
        recognized = [f for f in self._files if e.parse_character_from_name(f.stem) is not None]
        if not recognized:
            self._logline(
                "None of the selected files match character names (e.g. 'A.png', 'small_b.png', '1.png', 'comma.png').",
                t.STATE["error"][1],
            )
            return False

        first_src = self._files[0]
        out_path = e.plan_output(first_src, opts)

        msg = (
            f"{len(recognized)} recognized glyphs will be compiled into:\n"
            f"{out_path}\n\n"
            f"Font Name: {opts.font_name} ({opts.style_name})\n"
            f"Units Per Em: {opts.units_per_em}\n\n"
            "Proceed with font compilation?"
        )
        return messagebox.askyesno("Confirm Font Compilation", msg, icon="question")

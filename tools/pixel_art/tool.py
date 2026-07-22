"""Registers Pixel Art Converter as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class PixelArtTool:
    meta = ToolMeta(
        id="pixel_art",
        title="Pixel Art Converter",
        icon=Icons.GRID,
        subtitle="Turn images into clean, palettized pixel art (batch)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import PixelArtPanel
        return PixelArtPanel(parent)

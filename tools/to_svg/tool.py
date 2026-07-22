"""Registers To SVG as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class ToSvgTool:
    meta = ToolMeta(
        id="to_svg",
        title="To SVG",
        icon=Icons.EXPAND,
        subtitle="Vectorize raster images to SVG (batch)",
    )

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        # Lazy import: the panel/engine pull vtracer at run time. Keeping it out
        # of module import means discovery/the sidebar work even before vtracer
        # is installed — the missing dep is reported when the tool is used.
        from .panel import ToSvgPanel
        return ToSvgPanel(parent)

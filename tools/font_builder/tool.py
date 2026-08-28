"""Registers Font Builder as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class FontBuilderTool:
    meta = ToolMeta(
        id="font_builder",
        title="Font Builder",
        icon=Icons.LAYERS,
        category="images",
        subtitle="Compile images and SVGs into installable .TTF fonts",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: keep heavy dependencies (vtracer, fonttools) out of module import
        # so discovery and the sidebar always load fast and cleanly.
        from .panel import FontBuilderPanel
        return FontBuilderPanel(parent, services.queue)

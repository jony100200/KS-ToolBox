"""Registers Showcase as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class ShowcaseTool:
    meta = ToolMeta(
        id="showcase",
        title="Showcase",
        icon=Icons.GRID,
        subtitle="Contact sheets, framed heroes & before/after renders",
    )

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import ShowcasePanel
        return ShowcasePanel(parent)

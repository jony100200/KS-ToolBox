"""Registers Icon/Sprite Normalizer as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class IconNormalizerTool:
    meta = ToolMeta(
        id="icon_normalizer",
        title="Icon Normalizer",
        icon=Icons.GRID,
        subtitle="Trim, square-pad and resize icons/sprites (batch)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import IconNormalizerPanel
        return IconNormalizerPanel(parent)

"""Registers Image Rescale as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class ImageRescaleTool:
    meta = ToolMeta(
        id="image_rescale",
        title="Image Rescale",
        icon=Icons.EXPAND,
        category="images",
        subtitle="Batch-resize images (longest-side, megapixels, factor, fit)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import ImageRescalePanel
        return ImageRescalePanel(parent, queue_service=services.queue)

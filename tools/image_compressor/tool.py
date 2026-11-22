"""Registers Image Compressor as a ToolBox tool."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.icons import Icons
from toolbox.tool import ToolMeta


class ImageCompressorTool:
    meta = ToolMeta(
        id="image_compressor",
        title="Image Compressor",
        icon=Icons.GRID,
        category="images",
        subtitle="Shrink images with lossless pixel or PSNR proof before removing originals",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import ImageCompressorPanel
        return ImageCompressorPanel(parent, queue_service=services.queue)

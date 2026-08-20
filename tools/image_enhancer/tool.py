"""Registers the local Image Enhancer plugin."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.icons import Icons
from toolbox.tool import ToolMeta


class ImageEnhancerTool:
    meta = ToolMeta(
        id="image_enhancer", title="Image Enhancer", icon=Icons.BOLT,
        category="images", subtitle="Smart deterministic, hybrid, or AI restoration — local and batch-safe",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import ImageEnhancerPanel
        return ImageEnhancerPanel(parent, queue_service=services.queue)

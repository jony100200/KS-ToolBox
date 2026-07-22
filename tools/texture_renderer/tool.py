"""Registers Texture Renderer as a ToolBox tool (the LEGO plug).

`meta` must be readable by discovery without importing the panel/engine (which
pull customtkinter / Pillow), so `build_panel` imports the panel lazily. This
keeps the sidebar working on a machine that hasn't installed the deps yet.
"""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class TextureRendererTool:
    meta = ToolMeta(
        id="texture_renderer",
        title="Texture Renderer",
        icon=Icons.LAYERS,
        subtitle="Batch-export textures from Substance & Material Maker",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import TextureRendererPanel   # lazy: keeps discovery light
        return TextureRendererPanel(parent)

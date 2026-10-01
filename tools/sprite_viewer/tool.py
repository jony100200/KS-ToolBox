"""Registers Sprite Viewer as a ToolBox tool (the LEGO plug).

`meta` must be readable by discovery without importing the panel/engine (which
pull customtkinter / Pillow), so `build_panel` imports the panel lazily. This
keeps the sidebar working on a machine that hasn't installed Pillow yet.
"""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class SpriteViewerTool:
    meta = ToolMeta(
        id="sprite_viewer",
        title="Sprite Viewer",
        icon=Icons.PLAY,
        category="game_assets",
        subtitle="View & play sprite sheets and animations",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import SpriteViewerPanel   # lazy: keeps discovery light
        return SpriteViewerPanel(parent)

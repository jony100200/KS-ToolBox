"""Registers Tileset Checker as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class TilesetCheckerTool:
    meta = ToolMeta(
        id="tileset_checker",
        title="Tileset Checker",
        icon=Icons.GRID,
        category="game_assets",
        subtitle="Score & preview how seamlessly textures tile",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel/engine pull numpy + Pillow. Keeping them out of
        # module import means discovery/the sidebar work before they're installed.
        from .panel import TilesetCheckerPanel
        return TilesetCheckerPanel(parent, services.queue)

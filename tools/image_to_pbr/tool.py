"""Registers Image to PBR as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class ImageToPbrTool:
    meta = ToolMeta(
        id="image_to_pbr",
        title="Image to PBR",
        icon=Icons.LAYERS,
        category="game_assets",
        subtitle="Generate full PBR texture sets (Normal, Height, Roughness, Metal, AO, ORM) from 2D images",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: keep heavy dependencies out of module import
        from .panel import ImageToPbrPanel
        return ImageToPbrPanel(parent, services.queue)

"""Registers Material Converter as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class MaterialConverterTool:
    meta = ToolMeta(
        id="material_converter",
        title="Material Converter",
        icon=Icons.LAYERS,
        subtitle="Pack/convert/rename PBR texture-map sets (Unity/Unreal/Godot/Blender)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel/engine pull numpy + Pillow. Keeping them out of
        # module import means discovery/the sidebar work before those are installed.
        from .panel import MaterialConverterPanel
        return MaterialConverterPanel(parent, services.queue)

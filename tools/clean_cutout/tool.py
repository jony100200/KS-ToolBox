"""Registers Clean Cutout as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class CleanCutoutTool:
    meta = ToolMeta(
        id="clean_cutout",
        title="Clean Cutout",
        icon=Icons.BROOM,
        subtitle="Remove backgrounds and clean the edge fringe (batch)",
    )

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        # Imported lazily: the panel pulls numpy/Pillow (and rembg at run time).
        # Keeping it out of module import means discovery/the sidebar work even
        # on a machine that hasn't installed those yet — the missing dep is
        # reported when the tool is opened/used, never at startup.
        from .panel import CleanCutoutPanel
        return CleanCutoutPanel(parent)

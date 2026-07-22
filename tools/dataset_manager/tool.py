"""Registers Dataset Manager as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class DatasetManagerTool:
    meta = ToolMeta(
        id="dataset_manager",
        title="Dataset Manager",
        icon=Icons.CHART,
        subtitle="Pair, split, bucket & audit image/caption datasets (copy-only)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import DatasetManagerPanel
        return DatasetManagerPanel(parent)

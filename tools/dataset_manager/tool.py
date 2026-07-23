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
        category="files_data",
        subtitle="Pair, split, bucket & audit image/caption training datasets (copy-only)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import keeps presentation and engine modules out of discovery.
        # The engine resolves optional Pillow support only when needed.
        from .panel import DatasetManagerPanel
        return DatasetManagerPanel(parent, services.queue)

"""Registers Asset Auditor as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class AssetAuditorTool:
    meta = ToolMeta(
        id="asset_auditor",
        title="Asset Auditor",
        icon=Icons.CHART,
        category="files_data",
        subtitle="Find duplicates, corrupt files, and asset issues (report)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: discovery/sidebar stay independent from processing;
        # Pillow and NumPy load only when an audit actually inspects pixels.
        from .panel import AssetAuditorPanel
        return AssetAuditorPanel(parent, services.queue)

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
        subtitle="Find duplicates, corrupt files, and asset issues (report)",
    )

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow + numpy. Keeping them out of module
        # import means discovery/the sidebar work even before they're installed.
        from .panel import AssetAuditorPanel
        return AssetAuditorPanel(parent)

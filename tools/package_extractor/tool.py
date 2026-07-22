"""Registers Package Extractor as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class PackageExtractorTool:
    meta = ToolMeta(
        id="package_extractor",
        title="Package Extractor",
        icon=Icons.FOLDER,
        subtitle="Extract Unity packages & archives, rebuild folders (safe)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import keeps discovery/the sidebar working without loading the
        # panel; the engine is pure stdlib so there is no heavy dependency here.
        from .panel import PackageExtractorPanel
        return PackageExtractorPanel(parent)

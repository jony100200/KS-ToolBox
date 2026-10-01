"""Registers Unity Packager as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class UnityPackagerTool:
    meta = ToolMeta(
        id="unity_packager",
        title="Unity Packager",
        icon=Icons.TOOLBOX,
        category="game_assets",
        subtitle="Build a .unitypackage from a project folder — Unity does not need to be open",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import keeps discovery and the sidebar independent of the panel.
        from .panel import UnityPackagerPanel
        return UnityPackagerPanel(parent)

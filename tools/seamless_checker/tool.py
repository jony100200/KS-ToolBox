"""Registers Seamless Checker as a ToolBox tool (the LEGO plug).

Keeps discovery fast and lightweight by importing the panel lazily inside build_panel.
"""
from __future__ import annotations

import customtkinter as ctk

from toolbox.icons import Icons
from toolbox.tool import ToolMeta


class SeamlessCheckerTool:
    meta = ToolMeta(
        id="seamless_checker",
        title="Seamless Checker",
        icon=Icons.GRID,
        category="game_assets",
        subtitle="Interactive 3×3 wrap inspector & redo triage",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import SeamlessCheckerPanel
        return SeamlessCheckerPanel(parent, services)

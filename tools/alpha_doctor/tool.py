"""Registers Alpha Doctor as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class AlphaDoctorTool:
    meta = ToolMeta(
        id="alpha_doctor",
        title="Alpha Doctor",
        icon=Icons.BROOM,
        subtitle="Remove backgrounds & repair alpha (deterministic; AI optional)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: panel/engine pull numpy/Pillow (and onnxruntime only for the
        # opt-in AI method). Discovery/the sidebar work without them.
        from .panel import AlphaDoctorPanel
        return AlphaDoctorPanel(parent, services.queue)

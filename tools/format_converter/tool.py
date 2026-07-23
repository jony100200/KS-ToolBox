"""Registers Format Converter as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class FormatConverterTool:
    meta = ToolMeta(
        id="format_converter",
        title="Format Converter",
        icon=Icons.ARROW,
        subtitle="Convert images, audio/video and documents (batch)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: discovery/sidebar stay independent from the processing
        # module; Pillow and optional document engines load only during work.
        from .panel import FormatConverterPanel
        return FormatConverterPanel(parent, services.queue)

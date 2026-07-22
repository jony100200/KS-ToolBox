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

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        # Lazy import: the panel/engine pull Pillow (and optional doc libs).
        # Discovery/the sidebar work without them; missing deps announce per-file.
        from .panel import FormatConverterPanel
        return FormatConverterPanel(parent)

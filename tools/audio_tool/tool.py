"""Registers Audio Tool as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class AudioTool:
    meta = ToolMeta(
        id="audio_tool",
        title="Audio Tool",
        icon=Icons.PLAY,
        category="video_audio",
        subtitle="Batch convert, trim, fade & normalize audio",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: keeps discovery/the sidebar working before the panel loads.
        from .panel import AudioToolPanel
        return AudioToolPanel(parent, services.queue)

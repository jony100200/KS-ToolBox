"""Registers Video Compressor as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class VideoCompressorTool:
    meta = ToolMeta(
        id="video_compressor",
        title="Video Compressor",
        icon=Icons.VIDEO,
        subtitle="Shrink videos without losing quality (VMAF-verified)",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import VideoCompressorPanel
        return VideoCompressorPanel(parent, queue_service=services.queue)

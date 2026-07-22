"""Registers Video Chopper as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class VideoChopperTool:
    meta = ToolMeta(
        id="video_chopper",
        title="Video Chopper",
        icon=Icons.SCISSORS,
        subtitle="Split a video into clips at black-frame gaps",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import VideoChopperPanel
        return VideoChopperPanel(parent)

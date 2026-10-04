"""Registers YouTube Downloader as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class YouTubeDownloaderTool:
    meta = ToolMeta(
        id="youtube_downloader",
        title="YouTube Downloader",
        icon=Icons.VIDEO,
        category="video_audio",
        subtitle="Download thumbnails, videos, audio & subtitles from playlists, channels, or videos",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        from .panel import YouTubeDownloaderPanel
        return YouTubeDownloaderPanel(parent, services)

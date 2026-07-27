"""Registers Metadata Scrubber as a ToolBox tool (the LEGO plug)."""
from __future__ import annotations

import customtkinter as ctk

from toolbox.tool import ToolMeta
from toolbox.icons import Icons


class MetadataScrubberTool:
    meta = ToolMeta(
        id="metadata_scrubber",
        title="Metadata Scrubber",
        icon=Icons.BROOM,
        category="images",
        subtitle="Strip EXIF / XMP / AI-recipe metadata into a clean delivery copy",
    )

    def build_panel(self, parent: ctk.CTkFrame, services) -> ctk.CTkBaseClass:
        # Lazy import: the panel pulls Pillow. Keeping it out of module import
        # means discovery/the sidebar work even before Pillow is installed.
        from .panel import MetadataScrubberPanel
        return MetadataScrubberPanel(parent, queue_service=services.queue)

"""YouTube Downloader plugin. Exposing `TOOL` is the entire contract with the
ToolBox — discovery finds it, the shell shows it.
"""
from .tool import YouTubeDownloaderTool

TOOL = YouTubeDownloaderTool()

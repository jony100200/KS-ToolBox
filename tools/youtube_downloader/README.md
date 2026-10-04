# YouTube Downloader

A unified media downloader and asset scraper for YouTube playlists, channels, and individual videos.

## Features

- **Selective Asset Checkboxes:**
  - **Thumbnails:** Downloads full-resolution (`maxresdefault.jpg` / `sddefault.jpg` / `hqdefault.jpg`) thumbnails directly over HTTP with zero video download overhead.
  - **Video:** Full MP4 video streams at chosen resolution (Best, 1080p, 720p, 480p, 360p).
  - **Audio Only:** Audio track extracted into MP3, M4A, WAV, or original stream.
  - **Subtitles / CC:** Closed captions and subtitles downloaded in `.srt` format.
- **Source Types:**
  - Full Playlists (`https://www.youtube.com/playlist?list=...`)
  - Channel Feeds (`https://www.youtube.com/@channel/videos`)
  - Single Videos / Shorts (`https://www.youtube.com/watch?v=...`, `https://youtu.be/...`)
- **Interactive Collection Inspector:**
  - Inspects and lists all items with index, title, and duration before downloading.
  - Select / deselect individual videos from the collection.
- **Fast & Responsive:**
  - Pure Python HTTP direct thumbnail scraping requires no video downloading or transcoding.
  - Non-blocking background worker threads keep the UI fluid.
  - Live progress bar, ETA / speed display, and status log.
  - Cancellation token support for stopping mid-download gracefully.

## Architecture

- `tool.py`: Exposes `YouTubeDownloaderTool` conforming to KS-ToolBox's `Tool` protocol.
- `engine.py`: Pure extraction logic with `yt-dlp` integration and fallback native HTML parsing.
- `panel.py`: CustomTkinter interface matching the PromptSequencer design system tokens.

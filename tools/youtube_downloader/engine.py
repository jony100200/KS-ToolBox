"""YouTube Downloader engine — metadata scraping and asset downloading.

Supports:
  - Playlists, channels, and single videos
  - Checkbox selection: Thumbnails, Video, Audio, Subtitles
  - Ultra-fast native thumbnail fetching (direct HTTP, no video payload)
  - yt-dlp integration for video, audio, and subtitle streams
  - Cancellation token support on all operations
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

# Try importing yt-dlp; fallback if absent
try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    yt_dlp = None
    HAS_YTDLP = True if False else False


def has_ytdlp() -> bool:
    return HAS_YTDLP


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize strings for Windows/macOS/Linux filenames."""
    # Replace forbidden chars \ / : * ? " < > | with clean substitutes
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', name)
    # Strip non-printable / trailing spaces and dots
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .')
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_length]


def format_duration(seconds: int | float | None) -> str:
    """Convert seconds to mm:ss or hh:mm:ss."""
    if not seconds or seconds <= 0:
        return "--:--"
    sec = int(seconds)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@dataclass
class VideoItem:
    index: int
    video_id: str
    title: str
    duration: int
    duration_str: str
    thumbnail_url: str
    url: str
    channel: str = ""
    status: str = "Queued"


@dataclass
class FetchResult:
    title: str = ""
    url_type: str = "video"  # "playlist", "channel", "video"
    channel: str = ""
    items: list[VideoItem] = field(default_factory=list)
    error: str = ""


@dataclass
class DownloadOptions:
    download_thumbnails: bool = True
    download_video: bool = False
    download_audio: bool = False
    download_subtitles: bool = False
    video_quality: str = "best"        # "best", "1080p", "720p", "480p", "360p"
    audio_format: str = "mp3"          # "mp3", "m4a", "wav", "best"
    subtitle_lang: str = "en"          # "en", "all"
    thumbnail_quality: str = "max"     # "max", "hq", "sd"
    prefix_number: bool = True
    create_subfolder: bool = True


# --- Native YouTube HTML Scraper (Fallback & Quick Playlist Extractor) --------

def _fetch_playlist_native(url: str, cancel_check: Callable[[], bool] | None = None) -> FetchResult:
    """Native YouTube playlist scraper using ytInitialData (zero dependencies)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg;"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        if cancel_check and cancel_check():
            return FetchResult(error="Cancelled")
        html = resp.read().decode("utf-8", "ignore")

    m = re.search(r'ytInitialData\s*=\s*(\{.+?\});</script>', html)
    if not m:
        return FetchResult(error="Could not parse YouTube playlist structure.")

    try:
        data = json.loads(m.group(1))
    except Exception as ex:
        return FetchResult(error=f"JSON parse error: {ex}")

    # Extract playlist title
    pl_title = "YouTube Playlist"
    try:
        header = data.get("header", {})
        if "playlistHeaderRenderer" in header:
            pl_title = header["playlistHeaderRenderer"]["title"]["runs"][0]["text"]
        elif "pageHeaderRenderer" in header:
            pl_title = header["pageHeaderRenderer"]["pageTitle"]
    except Exception:
        pass

    videos: list[tuple[str, str, int]] = []

    def walk(obj):
        if cancel_check and cancel_check():
            return
        if isinstance(obj, dict):
            # Format 1: Modern lockupViewModel
            if "lockupViewModel" in obj:
                lum = obj["lockupViewModel"]
                vid = None
                try:
                    vid = lum["rendererContext"]["commandContext"]["onTap"]["innertubeCommand"]["watchEndpoint"]["videoId"]
                except Exception:
                    pass
                if not vid:
                    vid = lum.get("contentId")
                title = ""
                try:
                    title = lum["metadata"]["lockupMetadataViewModel"]["title"]["content"]
                except Exception:
                    pass
                if vid and len(vid) == 11:
                    videos.append((vid, title, 0))

            # Format 2: Classic playlistVideoRenderer
            elif "playlistVideoRenderer" in obj:
                pvr = obj["playlistVideoRenderer"]
                vid = pvr.get("videoId")
                t_obj = pvr.get("title", {})
                title = ""
                if "runs" in t_obj and t_obj["runs"]:
                    title = t_obj["runs"][0].get("text", "")
                elif "simpleText" in t_obj:
                    title = t_obj["simpleText"]
                length_sec = int(pvr.get("lengthSeconds", 0) or 0)
                if vid:
                    videos.append((vid, title, length_sec))

            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for itm in obj:
                walk(itm)

    walk(data)

    seen = set()
    items: list[VideoItem] = []
    idx = 1
    for vid, title, dur in videos:
        if vid not in seen:
            seen.add(vid)
            items.append(VideoItem(
                index=idx,
                video_id=vid,
                title=title or f"Video {vid}",
                duration=dur,
                duration_str=format_duration(dur),
                thumbnail_url=f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
                url=f"https://www.youtube.com/watch?v={vid}",
            ))
            idx += 1

    return FetchResult(
        title=pl_title,
        url_type="playlist",
        items=items
    )


# --- Unified Info Fetcher -----------------------------------------------------

def fetch_url_info(url: str, cancel_check: Callable[[], bool] | None = None) -> FetchResult:
    """Extract metadata for a playlist, channel, or video URL."""
    url = url.strip()
    if not url:
        return FetchResult(error="URL cannot be empty.")

    # 1. If yt-dlp is available, try yt-dlp flat extraction
    if HAS_YTDLP:
        try:
            ydl_opts = {
                'extract_flat': True,
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if cancel_check and cancel_check():
                    return FetchResult(error="Cancelled")
                if not info:
                    return FetchResult(error="No information returned from URL.")

                raw_entries = info.get("entries")
                if raw_entries is not None:
                    # Playlist or channel
                    items: list[VideoItem] = []
                    idx = 1
                    for entry in raw_entries:
                        if not entry:
                            continue
                        vid = entry.get("id") or ""
                        title = entry.get("title") or f"Video {vid}"
                        dur = int(entry.get("duration") or 0)
                        thumb = entry.get("thumbnail") or f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
                        webpage = entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
                        if not webpage.startswith("http"):
                            webpage = f"https://www.youtube.com/watch?v={vid}"
                        items.append(VideoItem(
                            index=idx,
                            video_id=vid,
                            title=title,
                            duration=dur,
                            duration_str=format_duration(dur),
                            thumbnail_url=thumb,
                            url=webpage,
                            channel=entry.get("channel") or info.get("channel") or "",
                        ))
                        idx += 1
                    url_type = "channel" if ("/@" in url or "/channel/" in url or "/c/" in url) else "playlist"
                    return FetchResult(
                        title=info.get("title") or "YouTube Collection",
                        url_type=url_type,
                        channel=info.get("uploader") or info.get("channel") or "",
                        items=items
                    )
                else:
                    # Single video
                    vid = info.get("id") or ""
                    title = info.get("title") or f"Video {vid}"
                    dur = int(info.get("duration") or 0)
                    thumb = info.get("thumbnail") or f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"
                    item = VideoItem(
                        index=1,
                        video_id=vid,
                        title=title,
                        duration=dur,
                        duration_str=format_duration(dur),
                        thumbnail_url=thumb,
                        url=url,
                        channel=info.get("uploader") or info.get("channel") or "",
                    )
                    return FetchResult(
                        title=title,
                        url_type="video",
                        channel=item.channel,
                        items=[item]
                    )
        except Exception as ex:
            # If yt-dlp failed, fall through to native scraper if it's a playlist
            pass

    # 2. Native scraper fallback
    if "list=" in url:
        return _fetch_playlist_native(url, cancel_check)
    
    # Single video ID extraction fallback
    vid_match = re.search(r'(?:v=|\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', url)
    if vid_match:
        vid = vid_match.group(1)
        item = VideoItem(
            index=1,
            video_id=vid,
            title=f"YouTube Video ({vid})",
            duration=0,
            duration_str="--:--",
            thumbnail_url=f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
            url=f"https://www.youtube.com/watch?v={vid}",
        )
        return FetchResult(
            title=item.title,
            url_type="video",
            items=[item]
        )

    return FetchResult(error="Unable to extract video information from this URL.")


# --- Thumbnail Downloader (Direct, Lightweight & Blazing Fast) ----------------

def download_thumbnail_direct(video_id: str, dest_path: Path, quality: str = "max") -> tuple[bool, str, int]:
    """Download the thumbnail directly via HTTP image endpoint.
    
    Returns (success, message, file_size).
    """
    quality_urls = []
    if quality == "max":
        quality_urls = [
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        ]
    elif quality == "sd":
        quality_urls = [
            f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        ]
    else:  # hq / default
        quality_urls = [
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix(".tmp")

    last_error = "Unknown error"
    for u in quality_urls:
        try:
            req = urllib.request.Request(u, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                # YouTube returns a small ~1097 byte placeholder image when maxres is not available
                if len(data) > 1500:
                    with open(temp_path, "wb") as f:
                        f.write(data)
                    temp_path.replace(dest_path)
                    return True, str(dest_path), len(data)
        except Exception as e:
            last_error = str(e)
            continue

    if temp_path.exists():
        temp_path.unlink()
    return False, f"Failed to fetch thumbnail: {last_error}", 0


# --- Unified Asset Downloader -------------------------------------------------

def download_item(
    item: VideoItem,
    out_dir: Path,
    options: DownloadOptions,
    progress_cb: Callable[[str, float], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Download selected assets for one VideoItem.
    
    Returns (success, message).
    """
    if cancel_check and cancel_check():
        return False, "Cancelled"

    out_dir.mkdir(parents=True, exist_ok=True)

    # Base stem
    clean_title = sanitize_filename(item.title)
    if options.prefix_number:
        base_stem = f"{item.index:02d} - {clean_title}"
    else:
        base_stem = clean_title

    downloaded_assets: list[str] = []

    # 1. Thumbnail Download (direct fast path)
    if options.download_thumbnails:
        thumb_file = out_dir / f"{base_stem}.jpg"
        if progress_cb:
            progress_cb("Fetching thumbnail...", 0.1)
        ok, msg, sz = download_thumbnail_direct(item.video_id, thumb_file, quality=options.thumbnail_quality)
        if ok:
            downloaded_assets.append(f"Thumbnail ({sz // 1024} KB)")
        else:
            downloaded_assets.append(f"Thumbnail failed ({msg})")

    # If only thumbnails were requested, we are done!
    if not (options.download_video or options.download_audio or options.download_subtitles):
        if any("failed" not in a for a in downloaded_assets):
            return True, ", ".join(downloaded_assets)
        return False, ", ".join(downloaded_assets)

    # 2. Video / Audio / Subtitles via yt-dlp
    if not HAS_YTDLP:
        return False, "yt-dlp is required for video/audio/subtitles downloads. Run: pip install yt-dlp"

    # Configure yt-dlp options
    ydl_opts: dict = {
        'windowsfilenames': True,
        'no_warnings': True,
        'quiet': True,
        'outtmpl': str(out_dir / f"{base_stem}.%(ext)s"),
    }

    # Format selection
    if options.download_video:
        if options.video_quality == "1080p":
            ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best'
        elif options.video_quality == "720p":
            ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best'
        elif options.video_quality == "480p":
            ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best'
        elif options.video_quality == "360p":
            ydl_opts['format'] = 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best'
        else:
            ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif options.download_audio:
        # Audio only
        ydl_opts['format'] = 'bestaudio/best'
        if options.audio_format == "mp3":
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif options.audio_format == "m4a":
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
            }]
        elif options.audio_format == "wav":
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
            }]
    else:
        # Neither video nor audio requested, only subtitles
        ydl_opts['skip_download'] = True

    # Subtitles
    if options.download_subtitles:
        ydl_opts['writesubtitles'] = True
        ydl_opts['writeautomaticsub'] = True
        ydl_opts['subtitleslangs'] = [options.subtitle_lang]
        ydl_opts['subtitlesformat'] = 'srt'

    # Progress hook
    def ydl_hook(d):
        if cancel_check and cancel_check():
            raise KeyboardInterrupt("Cancelled by user")
        if progress_cb and d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes') or 0
            pct = (downloaded / total) if total > 0 else 0.0
            spd = d.get('speed') or 0
            spd_str = f"{spd / (1024*1024):.1f} MB/s" if spd else ""
            status_text = f"Downloading: {int(pct*100)}% {spd_str}".strip()
            progress_cb(status_text, pct)

    ydl_opts['progress_hooks'] = [ydl_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([item.url])
            if options.download_video:
                downloaded_assets.append("Video")
            if options.download_audio:
                downloaded_assets.append(f"Audio ({options.audio_format})")
            if options.download_subtitles:
                downloaded_assets.append("Subtitles")
            return True, ", ".join(downloaded_assets)
    except KeyboardInterrupt:
        return False, "Cancelled by user"
    except Exception as ex:
        err_msg = str(ex)
        if "ffmpeg" in err_msg.lower() and options.download_audio:
            err_msg += " (Note: Audio conversion requires ffmpeg)"
        return False, f"Error: {err_msg}"

"""Smoke test for YouTube Downloader tool."""
from __future__ import annotations

import tempfile
from pathlib import Path

from tools.youtube_downloader import TOOL
from tools.youtube_downloader import engine as e


def test_tool_meta():
    assert TOOL.meta.id == "youtube_downloader"
    assert TOOL.meta.title == "YouTube Downloader"
    assert TOOL.meta.category == "video_audio"


def test_sanitize_filename():
    raw = 'How to: Test "Everything" / <Special> | Characters?'
    clean = e.sanitize_filename(raw)
    for forbidden in r'\/*?:"<>|':
        assert forbidden not in clean
    assert clean == "How to_ Test _Everything_ _ _Special_ _ Characters_"


def test_format_duration():
    assert e.format_duration(592) == "9:52"
    assert e.format_duration(3665) == "1:01:05"
    assert e.format_duration(0) == "--:--"
    assert e.format_duration(None) == "--:--"


def test_direct_thumbnail_download():
    # Test downloading thumbnail for 3Blue1Brown linear algebra chapter 1
    video_id = "fNk_zzaMoSs"
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "thumb.jpg"
        ok, msg, sz = e.download_thumbnail_direct(video_id, dest, quality="max")
        assert ok is True
        assert dest.exists()
        assert sz > 5000  # Should be ~60 KB


def test_fetch_url_info_playlist():
    url = "https://www.youtube.com/playlist?list=PLZHQObOWTQDPD3MizzM2xVFitgF8hE_ab"
    res = e.fetch_url_info(url)
    assert res.error == ""
    assert res.url_type == "playlist"
    assert len(res.items) >= 15
    assert res.items[0].video_id == "fNk_zzaMoSs"


if __name__ == "__main__":
    test_tool_meta()
    test_sanitize_filename()
    test_format_duration()
    test_direct_thumbnail_download()
    print("Smoke tests passed successfully!")

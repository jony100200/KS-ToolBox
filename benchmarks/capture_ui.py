"""Capture real shell views for manual visual regression review.

Usage:
    python -m benchmarks.capture_ui [output-directory]
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageGrab

from toolbox.discovery import discover
from toolbox.shell import ToolBoxShell


def _capture(app: ToolBoxShell, destination: Path) -> None:
    app.deiconify()
    app.lift()
    app.focus_force()
    for _ in range(4):
        app.update_idletasks()
        app.update()
        time.sleep(0.1)
    x = app.winfo_rootx()
    y = app.winfo_rooty()
    width = app.winfo_width()
    height = app.winfo_height()
    if sys.platform == "win32":
        _grab_windows_window(app.winfo_id(), width, height).save(destination)
    else:
        ImageGrab.grab((x, y, x + width, y + height)).save(destination)


def _grab_windows_window(handle: int, width: int, height: int) -> Image.Image:
    """Capture the app surface directly, independent of overlapping windows."""
    import ctypes
    from ctypes import wintypes

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    window_dc = user32.GetWindowDC(handle)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(handle, memory_dc, 2):
            raise RuntimeError("PrintWindow could not capture KS ToolBox")
        header = BitmapInfoHeader()
        header.biSize = ctypes.sizeof(BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height  # top-down pixels
        header.biPlanes = 1
        header.biBitCount = 32
        pixels = (ctypes.c_ubyte * (width * height * 4))()
        rows = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(header),
            0,
        )
        if rows != height:
            raise RuntimeError(f"GetDIBits returned {rows}/{height} rows")
        return Image.frombuffer(
            "RGB", (width, height), bytes(pixels), "raw", "BGRX", 0, 1
        )
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(handle, window_dc)


def main() -> int:
    output = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(tempfile.gettempdir()) / "ks-toolbox-ui"
    )
    output.mkdir(parents=True, exist_ok=True)
    app = ToolBoxShell(discover())
    try:
        app.geometry("1200x780+40+40")
        _capture(app, output / "home-default.png")
        app._open_category("game_assets")
        _capture(app, output / "category-game-assets.png")
        app._show_search("convert")
        _capture(app, output / "search-convert.png")
        app._select("image_rescale")
        _capture(app, output / "tool-image-rescale.png")
        app.geometry("980x640+40+40")
        app._select("sprite_viewer")
        _capture(app, output / "tool-sprite-minimum.png")
        app._select(app.QUEUE_ID)
        _capture(app, output / "queue-minimum.png")
    finally:
        app._on_close()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

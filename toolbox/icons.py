"""Icon font - FontAwesome 6 Solid, bundled in assets/, used for every UI icon.

Why a font, not emoji: emoji render differently across Windows/Linux/macOS; a
bundled icon font is pixel-identical everywhere - which matters for a
cross-platform release. Registration is best-effort and degrades gracefully.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import customtkinter as ctk

_FA_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "fa-solid-900.ttf"
FA_FAMILY = "Font Awesome 6 Free Solid"


class Icons:
    """FontAwesome 6 Solid codepoints."""
    LAYERS   = ""
    GRID     = ""
    EXPAND   = ""
    SCISSORS = ""
    TOOLBOX = ""
    VIDEO   = ""
    GEAR    = ""
    CHART   = ""
    FOLDER  = ""
    PLAY    = ""
    STOP    = ""
    PLUS    = ""
    BROOM   = ""
    TRASH   = ""
    CHECK   = ""
    TIMES   = ""
    ARROW   = ""
    CIRCLE  = ""
    WARN    = ""
    QUEUE   = ""
    PAUSE   = ""
    CLOCK   = ""
    ROTATE  = ""


def get_icon_font(size: int = 14) -> ctk.CTkFont:
    return ctk.CTkFont(family=FA_FAMILY, size=size)


def setup_fonts() -> bool:
    """Register the bundled TTF for this process. Call once at startup."""
    if not _FA_PATH.is_file():
        return False
    try:
        from pyglet import font as _pf
        _pf.add_file(str(_FA_PATH))
        return True
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.gdi32.AddFontResourceExW(
                ctypes.create_unicode_buffer(str(_FA_PATH)), 0x10, 0)
            return True
        except Exception:
            return False
    try:
        import shutil
        dest_dir = Path.home() / (".local/share/fonts" if sys.platform.startswith("linux") else "Library/Fonts")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _FA_PATH.name
        if not dest.exists():
            shutil.copy2(_FA_PATH, dest)
            if sys.platform.startswith("linux"):
                import subprocess
                subprocess.run(["fc-cache", "-f"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False

"""Icon font - Tabler Icons (MIT Licensed), bundled in assets/, used for every UI icon.

Why a font, not emoji: emoji render differently across Windows/Linux/macOS; a
bundled icon font is pixel-identical everywhere - which matters for a
cross-platform release. Registration is best-effort and degrades gracefully.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import customtkinter as ctk

_ICON_FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "tabler-icons.ttf"
_FA_PATH = _ICON_FONT_PATH  # legacy alias
ICON_FAMILY = "tabler-icons"
FA_FAMILY = ICON_FAMILY  # legacy alias


class Icons:
    """Tabler Icons (MIT Licensed) codepoints."""
    SEARCH   = "\ueb1c"  # search
    BOLT     = "\uea38"  # bolt
    LAYERS   = "\ueac9"  # layers-intersect
    GRID     = "\uedba"  # layout-grid
    EXPAND   = "\uea28"  # arrows-maximize
    SCISSORS = "\ueb1b"  # scissors
    TOOLBOX  = "\uebca"  # tools
    VIDEO    = "\ued22"  # video
    GEAR     = "\ueb20"  # settings
    CHART    = "\uea59"  # chart-bar
    FOLDER   = "\ueaad"  # folder
    PLAY     = "\ued46"  # player-play
    STOP     = "\ued4a"  # player-stop
    PLUS     = "\ueb0b"  # plus
    BROOM    = "\uebb8"  # brush
    TRASH    = "\ueb41"  # trash
    CHECK    = "\uea5e"  # check
    TIMES    = "\ueb55"  # x
    ARROW    = "\uea1f"  # arrow-right
    CIRCLE   = "\ufee5"  # circle
    WARN     = "\uea06"  # alert-triangle
    QUEUE    = "\ueb6a"  # list-check
    PAUSE    = "\ued45"  # player-pause
    CLOCK    = "\uea70"  # clock
    ROTATE   = "\ueb16"  # rotate


def get_icon_font(size: int = 14) -> ctk.CTkFont:
    return ctk.CTkFont(family=ICON_FAMILY, size=size)


def setup_fonts() -> bool:
    """Register the bundled TTF for this process. Call once at startup."""
    if not _ICON_FONT_PATH.is_file():
        return False
    try:
        from pyglet import font as _pf
        _pf.add_file(str(_ICON_FONT_PATH))
        return True
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.gdi32.AddFontResourceExW(
                ctypes.create_unicode_buffer(str(_ICON_FONT_PATH)), 0x10, 0)
            return True
        except Exception:
            return False
    try:
        import shutil
        dest_dir = Path.home() / (".local/share/fonts" if sys.platform.startswith("linux") else "Library/Fonts")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _ICON_FONT_PATH.name
        if not dest.exists():
            shutil.copy2(_ICON_FONT_PATH, dest)
            if sys.platform.startswith("linux"):
                import subprocess
                subprocess.run(["fc-cache", "-f"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False


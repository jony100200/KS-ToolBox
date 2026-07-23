"""Capture real shell views for manual visual regression review.

Usage:
    python -m benchmarks.capture_ui [output-directory]
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from PIL import ImageGrab

from toolbox.discovery import discover
from toolbox.shell import ToolBoxShell


def _capture(app: ToolBoxShell, destination: Path) -> None:
    app.deiconify()
    app.attributes("-topmost", True)
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
    ImageGrab.grab((x, y, x + width, y + height)).save(destination)
    app.attributes("-topmost", False)


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

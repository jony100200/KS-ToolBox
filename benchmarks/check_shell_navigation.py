"""Exercise grouped navigation through the real CustomTkinter shell."""
from __future__ import annotations

import os
import tempfile

from toolbox.catalog import CATEGORIES, tools_in_category
from toolbox.discovery import discover
from toolbox.shell import ToolBoxShell


def main() -> int:
    with tempfile.TemporaryDirectory() as state_dir:
        os.environ["KS_TOOLBOX_STATE_DIR"] = state_dir
        app = ToolBoxShell(discover())
        app.withdraw()
        try:
            app.update()
            assert app._active == app.HOME_ID
            expected_nav = {
                app.HOME_ID,
                app.QUEUE_ID,
                *(app._category_view_id(category.id) for category in CATEGORIES),
            }
            assert set(app._nav_buttons) == expected_nav
            assert set(app._nav_buttons).isdisjoint(
                tool.meta.id for tool in app._registry.all()
            )

            for category in CATEGORIES:
                view_id = app._category_view_id(category.id)
                app._select(view_id)
                app.update()
                assert app._active == view_id
                assert tools_in_category(app._registry.all(), category.id)

            assert "image_rescale" not in app._panels
            app._select("image_rescale")
            app.update()
            assert "image_rescale" in app._panels
            assert app._recent_ids[0] == "image_rescale"
            assert (
                app._nav_buttons[app._category_view_id("images")].cget("fg_color")
                == "#2563EB"
            )

            app._show_search("background")
            app.update()
            assert app._active == app.SEARCH_ID
            assert [tool.meta.id for tool in app._search_results] == ["alpha_doctor"]
            app._show_search(" ")
            assert app._active == app.HOME_ID

            app.geometry("980x640")
            app._select("sprite_viewer")
            app.update()
            page = app._pages["sprite_viewer"]
            assert page._tool_host.__class__.__name__ == "CTkScrollableFrame"
            app._go_back()
            assert app._active == app._category_view_id("game_assets")
        finally:
            app._on_close()
    print("PASS: grouped shell navigation, search, lazy tools, and scroll host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

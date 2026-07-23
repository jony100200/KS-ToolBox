"""Professional grouped shell for the KS ToolBox first-party tool catalog.

The shell owns navigation, lazy panel composition, and app-level shortcuts. It
does not know how any tool processes files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import icons
from toolbox import theme as t
from toolbox.application import AppServices, create_services
from toolbox.batch_core import JobState
from toolbox.catalog import (
    CATEGORIES,
    category_by_id,
    search_tools,
    tools_in_category,
    validate_catalog,
)
from toolbox.catalog_panel import CategoryPanel, DashboardPanel, SearchResultsPanel
from toolbox.tool import Tool, ToolRegistry


def _asset(name: str) -> Path:
    """Return an asset path in source and PyInstaller builds."""
    base = (
        Path(getattr(sys, "_MEIPASS", ""))
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    return base / "assets" / name


def _set_win_app_id() -> None:
    """Give Windows the product identity before creating the window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KS.ToolBox")
    except Exception:
        pass  # cosmetic only


class ToolBoxShell(ctk.CTk):
    HOME_ID = "__home__"
    QUEUE_ID = "__queue__"
    SEARCH_ID = "__search__"
    CATEGORY_PREFIX = "category:"

    def __init__(self, registry: ToolRegistry):
        _set_win_app_id()
        super().__init__()
        self._registry = registry
        self._tools = registry.all()
        validate_catalog(self._tools)
        self._services: AppServices = create_services()
        self._panels: dict[str, ctk.CTkBaseClass] = {}
        self._pages: dict[str, ctk.CTkBaseClass] = {}
        self._active: str | None = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._recent_ids: list[str] = []
        self._search_results: list[Tool] = []
        self._search_after: str | None = None
        self._icon_img = None

        self.title("KS ToolBox")
        self.geometry("1200x780")
        self.minsize(980, 640)
        t.apply_appearance()
        icons.setup_fonts()
        self._set_window_icon()
        self.configure(fg_color=t.BG_COLOR)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_sidebar()
        self._content = ctk.CTkFrame(self, fg_color=t.BG_COLOR, corner_radius=0)
        self._content.pack(side="left", fill="both", expand=True)
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)
        self._bind_shortcuts()
        self._queue_poll = self.after(250, self._poll_queue_button)
        self._select(self.HOME_ID)

    def _set_window_icon(self) -> None:
        ico, png = _asset("KSToolBox.ico"), _asset("KSToolBox.png")
        if sys.platform == "win32" and ico.is_file():
            try:
                self.iconbitmap(str(ico))
                return
            except Exception:
                pass
        if png.is_file():
            try:
                import tkinter as tk

                self._icon_img = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass

    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=220, fg_color=t.CARD_BG, corner_radius=0)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        self._sidebar = bar

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(18, 12))
        ctk.CTkLabel(
            brand,
            text=icons.Icons.TOOLBOX,
            font=icons.get_icon_font(19),
            text_color=t.ACCENT_SOFT,
        ).pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=(9, 0))
        ctk.CTkLabel(
            words,
            text="KS TOOLBOX",
            font=t.font(14, bold=True),
            text_color=t.TEXT_MAIN,
            height=18,
        ).pack(anchor="w")
        ctk.CTkLabel(
            words,
            text="BATCH WORKSTATION",
            font=t.font(8, bold=True),
            text_color=t.TEXT_MUTED,
            height=12,
        ).pack(anchor="w")

        self._search_entry = c.entry(
            bar,
            placeholder_text="Search tools",
            height=36,
            font=t.font(11),
        )
        self._search_entry.pack(fill="x", padx=12, pady=(0, 12))
        self._search_entry.bind("<Return>", self._submit_sidebar_search)
        self._search_entry.bind("<Escape>", self._clear_search)
        self._search_entry.bind("<KeyRelease>", self._schedule_live_search, add="+")

        ctk.CTkLabel(
            bar,
            text="WORKSPACES",
            font=t.font(9, bold=True),
            text_color=t.TEXT_MUTED,
        ).pack(anchor="w", padx=16, pady=(2, 5))

        self._add_nav(self.HOME_ID, "Home", lambda: self._select(self.HOME_ID))
        self._queue_button = self._add_nav(
            self.QUEUE_ID, "Queue", lambda: self._select(self.QUEUE_ID)
        )
        divider = ctk.CTkFrame(bar, height=1, fg_color=t.CARD_BORDER)
        divider.pack(fill="x", padx=14, pady=8)
        for category in CATEGORIES:
            view_id = self._category_view_id(category.id)
            self._add_nav(
                view_id,
                category.title,
                lambda cid=category.id: self._open_category(cid),
            )

        help_text = ctk.CTkLabel(
            bar,
            text="Ctrl+K  Search\nCtrl+1  Home  ·  Ctrl+2  Queue",
            justify="left",
            font=t.font(9),
            text_color=t.TEXT_MUTED,
        )
        help_text.pack(side="bottom", anchor="w", padx=16, pady=(0, 14))

    def _add_nav(
        self, view_id: str, label: str, command
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(
            self._sidebar,
            text=label,
            anchor="w",
            height=40,
            corner_radius=7,
            fg_color="transparent",
            hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MUTED,
            font=t.font(11, bold=False),
            command=command,
        )
        button.pack(fill="x", padx=10, pady=1)
        self._nav_buttons[view_id] = button
        return button

    @staticmethod
    def _category_view_id(category_id: str) -> str:
        return f"{ToolBoxShell.CATEGORY_PREFIX}{category_id}"

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-k>", self._focus_search)
        self.bind_all("<Control-K>", self._focus_search)
        self.bind_all("<Control-Key-1>", lambda _event: self._select(self.HOME_ID))
        self.bind_all("<Control-Key-2>", lambda _event: self._select(self.QUEUE_ID))
        self.bind_all("<Alt-Left>", self._go_back)
        if sys.platform == "darwin":
            self.bind_all("<Command-k>", self._focus_search)
            self.bind_all("<Command-Key-1>", lambda _event: self._select(self.HOME_ID))
            self.bind_all("<Command-Key-2>", lambda _event: self._select(self.QUEUE_ID))

    def _focus_search(self, _event=None) -> str:
        self._search_entry.focus_set()
        self._search_entry.select_range(0, "end")
        return "break"

    def _submit_sidebar_search(self, _event=None) -> str:
        self._cancel_pending_search()
        self._show_search(self._search_entry.get())
        return "break"

    def _clear_search(self, _event=None) -> str:
        self._cancel_pending_search()
        self._search_entry.delete(0, "end")
        self._select(self.HOME_ID)
        return "break"

    def _schedule_live_search(self, event) -> None:
        if event.keysym in {"Return", "Escape", "Up", "Down", "Left", "Right"}:
            return
        self._cancel_pending_search()
        self._search_after = self.after(
            140, self._run_live_search
        )

    def _run_live_search(self) -> None:
        self._search_after = None
        self._show_search(self._search_entry.get())

    def _cancel_pending_search(self) -> None:
        if self._search_after is not None:
            self.after_cancel(self._search_after)
            self._search_after = None

    def _show_search(self, query: str) -> None:
        normalized = " ".join(query.split())
        if not normalized:
            self._select(self.HOME_ID)
            return
        self._search_results = search_tools(self._tools, normalized)
        old = self._pages.pop(self.SEARCH_ID, None)
        if old is not None:
            old.destroy()
        page = SearchResultsPanel(
            self._content,
            normalized,
            self._search_results,
            self._open_tool,
        )
        self._pages[self.SEARCH_ID] = page
        self._select(self.SEARCH_ID)

    def _go_back(self, _event=None) -> str:
        if self._active and self._registry.get(self._active):
            self._open_category(self._registry.get(self._active).meta.category)
        elif self._active == self.SEARCH_ID:
            self._select(self.HOME_ID)
        return "break"

    def _open_category(self, category_id: str) -> None:
        self._select(self._category_view_id(category_id))

    def _open_tool(self, tool_id: str) -> None:
        self._select(tool_id)

    def _select(self, view_id: str) -> None:
        if view_id == self._active:
            return
        tool = self._registry.get(view_id)
        if not self._is_valid_view(view_id, tool):
            return
        if self._active and self._active in self._pages:
            self._pages[self._active].grid_remove()

        if view_id == self.HOME_ID:
            self._rebuild_home()
        elif view_id not in self._pages:
            self._pages[view_id] = self._build_page(view_id, tool)
        self._pages[view_id].grid(row=0, column=0, sticky="nsew")
        self._active = view_id
        if tool:
            self._remember_tool(tool.meta.id)
        self._update_nav_highlight(view_id, tool)

    def _is_valid_view(self, view_id: str, tool: Tool | None) -> bool:
        if tool is not None or view_id in {self.HOME_ID, self.QUEUE_ID, self.SEARCH_ID}:
            return view_id != self.SEARCH_ID or view_id in self._pages
        if view_id.startswith(self.CATEGORY_PREFIX):
            try:
                category_by_id(view_id.removeprefix(self.CATEGORY_PREFIX))
                return True
            except ValueError:
                return False
        return False

    def _rebuild_home(self) -> None:
        old = self._pages.pop(self.HOME_ID, None)
        if old is not None:
            old.destroy()
        self._pages[self.HOME_ID] = DashboardPanel(
            self._content,
            self._tools,
            self._open_tool,
            self._open_category,
            self._show_search,
            lambda: self._select(self.QUEUE_ID),
            self._recent_ids,
        )

    def _build_page(
        self, view_id: str, tool: Tool | None
    ) -> ctk.CTkBaseClass:
        if view_id == self.QUEUE_ID:
            from toolbox.queue_panel import QueuePanel

            panel = QueuePanel(self._content, self._services.queue)
            self._panels[view_id] = panel
            return panel
        if view_id.startswith(self.CATEGORY_PREFIX):
            category_id = view_id.removeprefix(self.CATEGORY_PREFIX)
            return CategoryPanel(
                self._content,
                category_by_id(category_id),
                tools_in_category(self._tools, category_id),
                self._open_tool,
            )
        if tool is not None:
            return self._build_tool_page(tool)
        raise ValueError(f"cannot build unknown view: {view_id}")

    def _build_tool_page(self, tool: Tool) -> ctk.CTkBaseClass:
        page = ctk.CTkFrame(self._content, fg_color=t.BG_COLOR, corner_radius=0)
        page.grid_rowconfigure(1, weight=1)
        page.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(page, fg_color=t.CARD_BG, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        category = category_by_id(tool.meta.category)
        c.ghost_button(
            header,
            f"Back to {category.title}",
            lambda: self._open_category(category.id),
            width=126,
        ).grid(row=0, column=0, rowspan=2, padx=(16, 14), pady=14)
        ctk.CTkLabel(
            header,
            text=tool.meta.title,
            font=t.font(18, bold=True),
            text_color=t.TEXT_MAIN,
            anchor="w",
        ).grid(row=0, column=1, sticky="sew", pady=(12, 1))
        ctk.CTkLabel(
            header,
            text=tool.meta.subtitle,
            font=t.font(10),
            text_color=t.TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=1, sticky="new", pady=(1, 12))

        host = ctk.CTkScrollableFrame(page, fg_color=t.BG_COLOR, corner_radius=0)
        host.grid(row=1, column=0, sticky="nsew")
        host.grid_columnconfigure(0, weight=1)
        page._tool_host = host  # retained explicitly for lifecycle and diagnostics
        panel = self._safe_build(tool, host)
        panel.grid(row=0, column=0, sticky="nsew")
        self._panels[tool.meta.id] = panel
        return page

    def _safe_build(self, tool: Tool, parent) -> ctk.CTkBaseClass:
        """Isolate optional tool failures and announce the degraded state."""
        try:
            return tool.build_panel(parent, self._services)
        except Exception as ex:  # noqa: BLE001 - plugin boundary must isolate
            frame = ctk.CTkFrame(parent, fg_color=t.BG_COLOR)
            card = c.Card(
                frame, f"{tool.meta.title} — unavailable", icon=icons.Icons.WARN
            )
            card.pack(fill="x", padx=t.PAD_GRID, pady=t.PAD_GRID)
            msg = f"This tool could not start:\n\n{type(ex).__name__}: {ex}"
            if isinstance(ex, ModuleNotFoundError):
                msg += "\n\nInstall its dependencies (see the tool README), then reopen."
            ctk.CTkLabel(
                card.body,
                text=msg,
                justify="left",
                anchor="w",
                text_color=t.TEXT_MUTED,
                font=t.font(12),
            ).pack(anchor="w")
            return frame

    def _remember_tool(self, tool_id: str) -> None:
        if tool_id in self._recent_ids:
            self._recent_ids.remove(tool_id)
        self._recent_ids.insert(0, tool_id)
        del self._recent_ids[8:]

    def _update_nav_highlight(self, view_id: str, tool: Tool | None) -> None:
        selected_nav = view_id
        if tool is not None:
            selected_nav = self._category_view_id(tool.meta.category)
        for nav_id, button in self._nav_buttons.items():
            active = nav_id == selected_nav
            button.configure(
                fg_color=t.ACCENT_BLUE if active else "transparent",
                text_color=t.TEXT_MAIN if active else t.TEXT_MUTED,
                font=t.font(11, bold=active),
            )

    def _poll_queue_button(self) -> None:
        active = sum(
            item.state
            in {
                JobState.QUEUED,
                JobState.PREPARING,
                JobState.RUNNING,
                JobState.PAUSED,
                JobState.CANCELLING,
            }
            for item in self._services.queue.history()
        )
        suffix = f"  ·  {active}" if active else ""
        self._queue_button.configure(text=f"Queue{suffix}")
        self._queue_poll = self.after(250, self._poll_queue_button)

    def _on_close(self) -> None:
        self._cancel_pending_search()
        self.after_cancel(self._queue_poll)
        self._services.queue.close()
        self.destroy()

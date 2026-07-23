"""Catalog views for grouped, searchable access to first-party tools.

These views know metadata and navigation callbacks only. They never import a
tool panel, touch the queue, or perform file processing.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.catalog import CATEGORIES, ToolCategory, tools_in_category
from toolbox.icons import Icons, get_icon_font
from toolbox.tool import Tool


OpenTool = Callable[[str], None]
OpenCategory = Callable[[str], None]
Search = Callable[[str], None]


class ToolCard(ctk.CTkFrame):
    """Keyboard-reachable catalog card with a single, explicit action."""

    def __init__(self, parent, tool: Tool, open_tool: OpenTool):
        super().__init__(
            parent,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        self.grid_columnconfigure(1, weight=1)
        icon_box = ctk.CTkFrame(
            self, width=42, height=42, fg_color=t.ACCENT_TINT, corner_radius=8
        )
        icon_box.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=14, sticky="n")
        icon_box.grid_propagate(False)
        ctk.CTkLabel(
            icon_box,
            text=tool.meta.icon,
            font=get_icon_font(16),
            text_color=t.ACCENT_SOFT,
        ).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(
            self,
            text=tool.meta.title,
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
            anchor="w",
        ).grid(row=0, column=1, sticky="sew", pady=(13, 1))
        ctk.CTkLabel(
            self,
            text=tool.meta.subtitle,
            font=t.font(10),
            text_color=t.TEXT_MUTED,
            anchor="nw",
            justify="left",
            wraplength=270,
        ).grid(row=1, column=1, sticky="new", pady=(1, 13))
        self.open_button = c.ghost_button(
            self,
            "Open",
            lambda: open_tool(tool.meta.id),
            width=68,
        )
        self.open_button.grid(
            row=0, column=2, rowspan=2, sticky="e", padx=(10, 14), pady=14
        )


class CategoryCard(ctk.CTkFrame):
    def __init__(
        self,
        parent,
        category: ToolCategory,
        tool_count: int,
        open_category: OpenCategory,
    ):
        super().__init__(
            parent,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self,
            text=category.icon,
            font=get_icon_font(18),
            text_color=t.ACCENT_SOFT,
            width=38,
        ).grid(row=0, column=0, rowspan=3, padx=(14, 10), pady=14, sticky="n")
        ctk.CTkLabel(
            self,
            text=category.title,
            font=t.font(14, bold=True),
            text_color=t.TEXT_MAIN,
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", pady=(13, 1))
        ctk.CTkLabel(
            self,
            text=category.description,
            font=t.font(10),
            text_color=t.TEXT_MUTED,
            anchor="nw",
            justify="left",
            wraplength=270,
        ).grid(row=1, column=1, sticky="new")
        ctk.CTkLabel(
            self,
            text=f"{tool_count} tools",
            font=t.font(10, bold=True),
            text_color=t.ACCENT_SOFT,
            anchor="w",
        ).grid(row=2, column=1, sticky="ew", pady=(5, 13))
        c.ghost_button(
            self,
            "Browse",
            lambda: open_category(category.id),
            width=76,
        ).grid(row=0, column=2, rowspan=3, sticky="e", padx=(8, 14), pady=14)


class DashboardPanel(ctk.CTkScrollableFrame):
    def __init__(
        self,
        parent,
        tools: Sequence[Tool],
        open_tool: OpenTool,
        open_category: OpenCategory,
        run_search: Search,
        open_queue: Callable[[], None],
        recent_ids: Sequence[str],
    ):
        super().__init__(parent, fg_color=t.BG_COLOR, corner_radius=0)
        self._tools = list(tools)
        self._open_tool = open_tool
        self._recent_ids = list(recent_ids)
        self.grid_columnconfigure((0, 1), weight=1, uniform="dashboard")

        hero = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=12,
        )
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", padx=18, pady=(18, 10))
        hero.grid_columnconfigure(0, weight=1)
        c.PageHeader(
            hero,
            "Batch work without babysitting",
            "Preview once, queue the repetitive work, and return to validated outputs.",
            icon=Icons.BOLT,
            eyebrow="KS ToolBox",
        ).grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 15))
        search = c.entry(
            hero,
            placeholder_text=(
                f"Search {len(self._tools)} tools — try “resize”, “audio”, or “dataset”"
            ),
            height=42,
            font=t.font(12),
        )
        search.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 22))
        search.bind("<Return>", lambda _event: run_search(search.get()))

        section = _section_label(self, "Work areas", "Choose a focused workspace")
        section.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(10, 7))
        for index, category in enumerate(CATEGORIES):
            CategoryCard(
                self,
                category,
                len(tools_in_category(self._tools, category.id)),
                open_category,
            ).grid(
                row=2 + index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(18 if index % 2 == 0 else 5, 5 if index % 2 == 0 else 18),
                pady=5,
            )

        recent_row = 4
        recent_tools = [
            tool
            for tool_id in self._recent_ids[:4]
            if (tool := next((item for item in self._tools if item.meta.id == tool_id), None))
        ]
        title = "Recent tools" if recent_tools else "Start with a tool"
        hint = "Your most recently opened tools" if recent_tools else "Popular batch workflows"
        _section_label(self, title, hint).grid(
            row=recent_row, column=0, columnspan=2, sticky="ew", padx=18, pady=(18, 7)
        )
        if not recent_tools:
            preferred = ("image_rescale", "video_compressor")
            recent_tools = [
                tool
                for tool_id in preferred
                if (tool := next((item for item in self._tools if item.meta.id == tool_id), None))
            ]
        for index, tool in enumerate(recent_tools):
            ToolCard(self, tool, open_tool).grid(
                row=recent_row + 1 + index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(18 if index % 2 == 0 else 5, 5 if index % 2 == 0 else 18),
                pady=5,
            )
        queue_row = recent_row + 1 + ((len(recent_tools) + 1) // 2)
        queue_card = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        queue_card.grid(
            row=queue_row,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=18,
            pady=(14, 20),
        )
        queue_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            queue_card,
            text="Queue and history",
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(13, 1))
        ctk.CTkLabel(
            queue_card,
            text="Monitor active work, completed runs, warnings, and failures.",
            font=t.font(10),
            text_color=t.TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=15, pady=(1, 13))
        c.secondary_button(queue_card, "Open queue", open_queue, width=100).grid(
            row=0, column=1, rowspan=2, padx=15, pady=13
        )


class CategoryPanel(ctk.CTkScrollableFrame):
    def __init__(
        self,
        parent,
        category: ToolCategory,
        tools: Sequence[Tool],
        open_tool: OpenTool,
    ):
        super().__init__(parent, fg_color=t.BG_COLOR, corner_radius=0)
        self.grid_columnconfigure((0, 1), weight=1, uniform="catalog")
        c.PageHeader(
            self,
            category.title,
            category.description,
            icon=category.icon,
            eyebrow=f"{len(tools)} tools",
        ).grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(22, 14)
        )
        for index, tool in enumerate(tools):
            ToolCard(self, tool, open_tool).grid(
                row=1 + index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(20 if index % 2 == 0 else 5, 5 if index % 2 == 0 else 20),
                pady=5,
            )


class SearchResultsPanel(ctk.CTkScrollableFrame):
    def __init__(
        self,
        parent,
        query: str,
        tools: Sequence[Tool],
        open_tool: OpenTool,
    ):
        super().__init__(parent, fg_color=t.BG_COLOR, corner_radius=0)
        self.grid_columnconfigure((0, 1), weight=1, uniform="search")
        count = len(tools)
        result_word = "result" if count == 1 else "results"
        c.PageHeader(
            self,
            f"Search results",
            f'{count} {result_word} for “{query}”',
            icon=Icons.SEARCH,
            eyebrow="Tool catalog",
        ).grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(22, 14)
        )
        if not tools:
            empty = c.Card(self, "No matching tools", icon=Icons.SEARCH)
            empty.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=5)
            ctk.CTkLabel(
                empty.body,
                text="Try a file type, task, or category such as image, convert, game, or dataset.",
                font=t.font(11),
                text_color=t.TEXT_MUTED,
                anchor="w",
            ).pack(fill="x")
            return
        for index, tool in enumerate(tools):
            ToolCard(self, tool, open_tool).grid(
                row=1 + index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(20 if index % 2 == 0 else 5, 5 if index % 2 == 0 else 20),
                pady=5,
            )


def _section_label(parent, title: str, hint: str) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, fg_color="transparent")
    frame.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        frame,
        text=title,
        font=t.font(14, bold=True),
        text_color=t.TEXT_MAIN,
    ).grid(row=0, column=0, sticky="w")
    ctk.CTkLabel(
        frame,
        text=hint,
        font=t.font(10),
        text_color=t.TEXT_MUTED,
    ).grid(row=0, column=1, sticky="e")
    return frame

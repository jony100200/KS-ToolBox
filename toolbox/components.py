"""Reusable themed widgets — the shared vocabulary every tool builds from.

Mirrors PromptSequencer's DashboardCard + button/pill conventions so the whole
KS family reads identically. Tools compose these; they don't reinvent styling.
"""
from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from . import icons
from . import theme as t


class PageHeader(ctk.CTkFrame):
    """Consistent page identity without putting processing logic in views."""

    def __init__(
        self,
        parent,
        title: str,
        subtitle: str = "",
        icon: str = "",
        eyebrow: str = "",
        **kwargs,
    ):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(1 if icon else 0, weight=1)
        column = 0
        if icon:
            icon_box = ctk.CTkFrame(
                self,
                width=48,
                height=48,
                fg_color=t.ACCENT_TINT,
                corner_radius=t.RADIUS_CARD,
            )
            icon_box.grid(row=0, column=0, rowspan=3, sticky="nw", padx=(0, 14))
            icon_box.grid_propagate(False)
            ctk.CTkLabel(
                icon_box,
                text=icon,
                font=icons.get_icon_font(19),
                text_color=t.ACCENT_SOFT,
            ).place(relx=0.5, rely=0.5, anchor="center")
            column = 1
        if eyebrow:
            ctk.CTkLabel(
                self,
                text=eyebrow.upper(),
                font=t.font(10, bold=True),
                text_color=t.ACCENT_SOFT,
            ).grid(row=0, column=column, sticky="w", pady=(0, 2))
        ctk.CTkLabel(
            self,
            text=title,
            font=t.font(24, bold=True),
            text_color=t.TEXT_MAIN,
            anchor="w",
        ).grid(row=1, column=column, sticky="ew")
        if subtitle:
            ctk.CTkLabel(
                self,
                text=subtitle,
                font=t.font(12),
                text_color=t.TEXT_MUTED,
                anchor="w",
                justify="left",
                wraplength=720,
            ).grid(row=2, column=column, sticky="ew", pady=(4, 0))


class Card(ctk.CTkFrame):
    """Bordered rounded card with an icon + uppercase title header and a
    content frame subclasses/callers build into (`self.body`)."""

    def __init__(self, parent, title: str, icon: str = "", **kwargs):
        super().__init__(parent, fg_color=t.CARD_BG, border_color=t.CARD_BORDER,
                         border_width=t.BORDER_W, corner_radius=t.RADIUS_CARD, **kwargs)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=t.PAD_CARD, pady=(t.PAD_CARD, 10))
        if icon:
            ctk.CTkLabel(header, text=icon, font=icons.get_icon_font(16), text_color=t.ACCENT_BLUE
                         ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text=title.upper(), font=t.font(13, bold=True),
                     text_color=t.TEXT_MAIN).pack(side="left")
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=t.PAD_CARD, pady=(0, t.PAD_CARD))


def primary_button(parent, text: str, command: Callable, **kw) -> ctk.CTkButton:
    height = kw.pop("height", 32)
    font = kw.pop("font", t.font(12, bold=True))
    return ctk.CTkButton(parent, text=text, command=command, fg_color=t.ACCENT_BLUE,
                         hover_color=t.ACCENT_HOVER, height=height, font=font, **kw)


def secondary_button(parent, text: str, command: Callable, **kw) -> ctk.CTkButton:
    height = kw.pop("height", 32)
    font = kw.pop("font", t.font(12))
    return ctk.CTkButton(parent, text=text, command=command, fg_color=t.CARD_BORDER,
                         hover_color=t.NEUTRAL_HOVER, height=height, font=font, **kw)


def ghost_button(parent, text: str, command: Callable, **kw) -> ctk.CTkButton:
    height = kw.pop("height", 32)
    font = kw.pop("font", t.font(12))
    return ctk.CTkButton(parent, text=text, command=command, fg_color="transparent",
                         border_color=t.CARD_BORDER, border_width=1, hover_color=t.NEUTRAL_HOVER,
                         height=height, font=font, **kw)


def danger_button(parent, text: str, command: Callable, **kw) -> ctk.CTkButton:
    height = kw.pop("height", 32)
    font = kw.pop("font", t.font(12, bold=True))
    return ctk.CTkButton(parent, text=text, command=command, fg_color=t.DANGER,
                         hover_color=t.DANGER_HOVER, height=height, font=font, **kw)


class Pill(ctk.CTkLabel):
    """A colored status pill; call set_state() to recolor by state name."""

    def __init__(self, parent, text: str = "IDLE", state: str = "idle", **kw):
        bg, fg = t.STATE[state]
        super().__init__(parent, text=f"● {text}", font=t.font(11, bold=True),
                         fg_color=bg, text_color=fg, corner_radius=t.RADIUS_PILL,
                         padx=10, pady=2, **kw)

    def set_state(self, text: str, state: str) -> None:
        bg, fg = t.STATE.get(state, t.STATE["idle"])
        self.configure(text=f"● {text}", fg_color=bg, text_color=fg)


def entry(parent, **kw) -> ctk.CTkEntry:
    """Input that reads darker than its card, matching PromptSequencer forms."""
    return ctk.CTkEntry(parent, fg_color=t.BG_COLOR, border_color=t.CARD_BORDER, **kw)

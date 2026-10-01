"""Visual theme — the single source of truth for colors, fonts, spacing.

Lifted to match KS PromptSequencer exactly so every KS app reads as one family.
Import these constants; never hardcode a hex value in a widget.
"""
from __future__ import annotations

import customtkinter as ctk

# --- palette (PromptSequencer) ---
BG_COLOR     = "#0B0F19"   # app / page background (near-black navy)
CARD_BG      = "#111827"   # card & top-bar fill
CARD_BORDER  = "#1F2937"   # 1px borders, dividers, inactive chips, secondary buttons
ACCENT_BLUE  = "#2563EB"   # primary accent (buttons, active progress, icons)
ACCENT_HOVER = "#1D4ED8"   # primary button hover
ACCENT_SOFT  = "#60A5FA"   # readable accent text and icons on dark surfaces
ACCENT_TINT  = "#172554"   # low-emphasis accent surface
NEUTRAL_HOVER= "#374151"   # secondary button hover
TEXT_MAIN    = "#F3F4F6"   # primary text (near-white)
TEXT_MUTED   = "#9CA3AF"   # secondary / label text (gray)

# --- state triad (dark bg + bright fg) ---
STATE = {
    "idle":    ("#374151", TEXT_MUTED),
    "running": ("#064E3B", "#34D399"),   # green
    "done":    ("#064E3B", "#34D399"),
    "waiting": ("#78350F", "#FBBF24"),   # amber
    "paused":  ("#78350F", "#FBBF24"),
    "error":   ("#7F1D1D", "#FCA5A5"),   # red
}
DANGER    = "#991B1B"
DANGER_HOVER = "#7F1D1D"

# --- geometry ---
RADIUS_CARD  = 8
RADIUS_PILL  = 4
BORDER_W     = 1
PAD_CARD     = 15          # internal card padding
PAD_GRID     = 10          # spacing between cards

# --- fonts (lazy: CTk fonts need a root window first) ---
def font(size: int = 12, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight="bold" if bold else "normal")

def mono(size: int = 12) -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size)


def apply_appearance() -> None:
    """Call once at startup, before building widgets."""
    ctk.set_appearance_mode("dark")
    # No set_default_color_theme — we override colors explicitly (PromptSequencer convention).

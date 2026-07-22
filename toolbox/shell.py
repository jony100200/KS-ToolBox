"""The ToolBox shell — fixed sidebar (tool list) + content area.

Knows nothing about any tool's internals: it lists the registry and asks the
selected tool to build its panel, lazily (nothing idle is constructed). Matches
PromptSequencer's sidebar + content layout and theme.
"""
from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

from toolbox.tool import Tool, ToolRegistry
from . import components as c
from . import theme as t
from . import icons


def _asset(name: str) -> Path:
    """Path to a file under assets/, working from source and frozen (PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return base / "assets" / name


def _set_win_app_id() -> None:
    """Windows: give the process an explicit AppUserModelID so the taskbar shows
    *our* icon and groups under 'KS ToolBox' instead of the python.exe host.
    No-op / harmless everywhere else."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KS.ToolBox")
    except Exception:
        pass  # cosmetic only — never block startup on it


class ToolBoxShell(ctk.CTk):
    def __init__(self, registry: ToolRegistry):
        _set_win_app_id()               # before the window exists, so the taskbar picks up our icon
        super().__init__()
        self._registry = registry
        self._panels: dict[str, ctk.CTkBaseClass] = {}   # built-once cache
        self._active: str | None = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._icon_img = None           # keep a ref so the PhotoImage isn't GC'd

        self.title("KS ToolBox")
        self.geometry("1200x780")
        self.minsize(980, 640)
        t.apply_appearance()
        icons.setup_fonts()
        self._set_window_icon()
        self.configure(fg_color=t.BG_COLOR)

        self._build_sidebar()
        self._content = ctk.CTkFrame(self, fg_color=t.BG_COLOR, corner_radius=0)
        self._content.pack(side="left", fill="both", expand=True)
        self._content.grid_rowconfigure(0, weight=1); self._content.grid_columnconfigure(0, weight=1)

        tools = self._registry.all()
        if tools:
            self._select(tools[0].meta.id)

    def _set_window_icon(self):
        """App icon for the titlebar/taskbar. Prefer the multi-size .ico on
        Windows (crisp at every size); fall back to the .png via iconphoto,
        which is what Linux/macOS use. Purely cosmetic — degrade silently."""
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
                self._icon_img = tk.PhotoImage(file=str(png))   # Tk 8.6+ reads PNG
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=230, fg_color=t.CARD_BG, corner_radius=0)
        bar.pack(side="left", fill="y"); bar.pack_propagate(False)

        brand = ctk.CTkFrame(bar, fg_color="transparent"); brand.pack(fill="x", padx=15, pady=(18, 6))
        ctk.CTkLabel(brand, text=icons.Icons.TOOLBOX, font=icons.get_icon_font(20)).pack(side="left")
        ctk.CTkLabel(brand, text="KS TOOLBOX", font=t.font(15, bold=True),
                     text_color=t.TEXT_MAIN).pack(side="left", padx=8)
        ctk.CTkFrame(bar, height=1, fg_color=t.CARD_BORDER).pack(fill="x", padx=15, pady=(6, 10))
        ctk.CTkLabel(bar, text="TOOLS", font=t.font(10, bold=True), text_color=t.TEXT_MUTED
                     ).pack(anchor="w", padx=15, pady=(0, 4))

        for tool in self._registry.all():
            btn = ctk.CTkButton(bar, text=f"  {tool.meta.icon}  {tool.meta.title}",
                                anchor="w", height=36, corner_radius=6,
                                fg_color="transparent", hover_color=t.CARD_BORDER,
                                text_color=t.TEXT_MUTED, font=icons.get_icon_font(12),
                                command=lambda tid=tool.meta.id: self._select(tid))
            btn.pack(fill="x", padx=10, pady=2)
            self._nav_buttons[tool.meta.id] = btn

        ctk.CTkLabel(bar, text="one shell · many tools", font=t.font(10),
                     text_color=t.CARD_BORDER).pack(side="bottom", pady=12)

    def _select(self, tool_id: str):
        if tool_id == self._active:
            return
        tool: Tool | None = self._registry.get(tool_id)
        if not tool:
            return
        # lazy build + cache; hide the previous panel
        if self._active and self._active in self._panels:
            self._panels[self._active].grid_remove()
        if tool_id not in self._panels:
            self._panels[tool_id] = self._safe_build(tool)
        self._panels[tool_id].grid(row=0, column=0, sticky="nsew")
        # nav highlight
        for tid, btn in self._nav_buttons.items():
            active = tid == tool_id
            btn.configure(fg_color=(t.ACCENT_BLUE if active else "transparent"),
                          text_color=(t.TEXT_MAIN if active else t.TEXT_MUTED))
        self._active = tool_id

    def _safe_build(self, tool: Tool) -> ctk.CTkBaseClass:
        """Build a tool's panel, but never let one tool's failure (e.g. an
        uninstalled optional dependency like rembg) crash the whole app —
        show the reason in-place instead. Fallbacks must announce themselves."""
        try:
            return tool.build_panel(self._content)
        except Exception as ex:                 # noqa: BLE001 — any build failure, degrade gracefully
            frame = ctk.CTkFrame(self._content, fg_color=t.BG_COLOR)
            card = c.Card(frame, f"{tool.meta.title} — unavailable", icon=icons.Icons.WARN)
            card.pack(fill="x", padx=t.PAD_GRID, pady=t.PAD_GRID)
            msg = f"This tool could not start:\n\n{type(ex).__name__}: {ex}"
            if isinstance(ex, ModuleNotFoundError):
                msg += "\n\nInstall its dependencies (see the tool's README.md), then reopen."
            ctk.CTkLabel(card.body, text=msg, justify="left", anchor="w",
                         text_color=t.TEXT_MUTED, font=t.font(12)).pack(anchor="w")
            return frame

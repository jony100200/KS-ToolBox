"""Seamless Checker UI — interactive 3x3 wrap inspector and triage workstation.

Thin presentation layer over engine.py.
Features:
    - Zero top chrome: edge-to-edge canvas with mouse-wheel zoom, pan, fit, 1:1.
    - Consolidated 2-tier bottom dock: telemetry on top, action controls below.
    - Max View (F / Tab) and collapsible Folders sidebar (S).
    - Multi-map PBR set detection + standalone image detection.
    - Single image direct transfer vs multi-map set subfolder transfer to Redo root.
    - Remembers last used input and redo directories across restarts.
    - Bounded async worker threads for loading, scanning, and safe transfers.
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageTk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e

_LOG = logging.getLogger(__name__)


class InteractiveCanvas(ctk.CTkFrame):
    """Tkinter Canvas embedded inside CTk with smooth zoom and pan."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(
            parent,
            fg_color=t.BG_COLOR,
            border_color=t.CARD_BORDER,
            border_width=t.BORDER_W,
            corner_radius=t.RADIUS_CARD,
            **kwargs,
        )
        self._current_pil: Image.Image | None = None
        self._photo_ref: ImageTk.PhotoImage | None = None
        self._scale: float = 1.0
        self._pan: list[float] = [0.0, 0.0]
        self._drag: dict[str, Any] | None = None
        self._has_custom_zoom: bool = False

        self._viewport = tk.Canvas(
            self,
            bg=t.BG_COLOR,
            highlightthickness=0,
            borderwidth=0,
        )
        self._viewport.pack(fill="both", expand=True, padx=2, pady=2)

        self._viewport.bind("<Configure>", lambda _e: self._on_resize())
        self._viewport.bind("<ButtonPress-1>", self._on_pan_start)
        self._viewport.bind("<B1-Motion>", self._on_pan_drag)
        self._viewport.bind("<ButtonRelease-1>", self._on_pan_end)
        self._viewport.bind("<MouseWheel>", self._on_mousewheel)
        self._viewport.bind("<Button-4>", lambda e: self._zoom(1.15, e.x, e.y))
        self._viewport.bind("<Button-5>", lambda e: self._zoom(1 / 1.15, e.x, e.y))
        self._viewport.bind("<Double-Button-1>", lambda _e: self.fit())

    def set_image(self, pil_image: Image.Image | None) -> None:
        self._current_pil = pil_image
        self._has_custom_zoom = False
        self._pan = [0.0, 0.0]
        self._render()

    def fit(self) -> None:
        self._has_custom_zoom = False
        self._pan = [0.0, 0.0]
        self._render()

    def actual_size(self) -> None:
        if self._current_pil is None:
            return
        vw = max(1, self._viewport.winfo_width())
        vh = max(1, self._viewport.winfo_height())
        iw, ih = self._current_pil.size
        self._scale = 1.0
        self._has_custom_zoom = True
        self._pan = [(vw - iw) / 2.0, (vh - ih) / 2.0]
        self._render_view()

    def _on_resize(self) -> None:
        if not self._has_custom_zoom:
            self._render()

    def _on_mousewheel(self, event: Any) -> None:
        factor = 1.15 if event.delta > 0 else (1.0 / 1.15)
        self._zoom(factor, event.x, event.y)

    def _zoom(self, factor: float, cx: int, cy: int) -> None:
        if self._current_pil is None:
            return
        old_scale = self._scale
        new_scale = max(0.05, min(30.0, old_scale * factor))
        if new_scale == old_scale:
            return
        self._has_custom_zoom = True
        # Adjust pan so the pixel under cursor stays under cursor
        self._pan[0] = cx - (cx - self._pan[0]) * (new_scale / old_scale)
        self._pan[1] = cy - (cy - self._pan[1]) * (new_scale / old_scale)
        self._scale = new_scale
        self._render_view()

    def _on_pan_start(self, event: Any) -> None:
        self._drag = {"x": event.x, "y": event.y, "pan_x": self._pan[0], "pan_y": self._pan[1]}

    def _on_pan_drag(self, event: Any) -> None:
        if not self._drag or self._current_pil is None:
            return
        dx = event.x - self._drag["x"]
        dy = event.y - self._drag["y"]
        self._pan[0] = self._drag["pan_x"] + dx
        self._pan[1] = self._drag["pan_y"] + dy
        self._render_view()

    def _on_pan_end(self, _event: Any) -> None:
        self._drag = None

    def _render(self) -> None:
        if self._current_pil is None:
            self._viewport.delete("all")
            self._photo_ref = None
            vw = self._viewport.winfo_width() or 400
            vh = self._viewport.winfo_height() or 400
            self._viewport.create_text(
                vw // 2,
                vh // 2,
                text="No texture loaded.\nSelect an input folder with textures.",
                fill=t.TEXT_MUTED,
                font=t.font(13),
                justify="center",
            )
            return

        vw = max(1, self._viewport.winfo_width())
        vh = max(1, self._viewport.winfo_height())
        iw, ih = self._current_pil.size

        if not self._has_custom_zoom:
            scale_x = vw / iw
            scale_y = vh / ih
            self._scale = min(scale_x, scale_y) * 0.98
            scaled_w = iw * self._scale
            scaled_h = ih * self._scale
            self._pan = [(vw - scaled_w) / 2.0, (vh - scaled_h) / 2.0]

        self._render_view()

    def _render_view(self) -> None:
        if self._current_pil is None:
            return
        iw, ih = self._current_pil.size
        sw = max(1, int(iw * self._scale))
        sh = max(1, int(ih * self._scale))

        # Use nearest neighbor for high zoom (pixel inspection), bilinear for zoom-out
        resample = Image.Resampling.NEAREST if self._scale >= 2.0 else Image.Resampling.BILINEAR
        display_img = self._current_pil.resize((sw, sh), resample=resample)
        self._photo_ref = ImageTk.PhotoImage(display_img)

        self._viewport.delete("all")
        self._viewport.create_image(
            int(self._pan[0]),
            int(self._pan[1]),
            anchor="nw",
            image=self._photo_ref,
        )


class SeamlessCheckerPanel(ctk.CTkFrame):
    """The interactive Seamless Checker view panel."""

    def __init__(self, parent: Any, services: Any) -> None:
        super().__init__(parent, fg_color=t.BG_COLOR)
        self._services = services
        self._items: list[dict[str, Any]] = []
        self._index: int = 0
        self._busy: bool = False
        self._input_root: str = ""
        self._redo_root: str = ""
        self._seam_guide: bool = False
        self._right_panel_visible: bool = True
        self._worker: threading.Thread | None = None
        self._worker_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._poll_job = None
        self._token: int = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)  # canvas work area
        self.grid_rowconfigure(1, weight=0)  # bottom dock

        self._build_work_area()
        self._build_bottom_dock()
        self._load_saved_settings()

        self._bind_keys()
        self._poll_worker()

    # ------------------------------------------------------------------ UI Layout
    def _build_work_area(self) -> None:
        area = ctk.CTkFrame(self, fg_color="transparent")
        area.grid(row=0, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 4))
        self._work_area = area

        # Left/Center: Canvas
        self.canvas = InteractiveCanvas(area)
        self.canvas.pack(side="left", fill="both", expand=True)

        # Right: Collapsible Folders & Settings panel
        self._right_panel = ctk.CTkFrame(
            area,
            width=280,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=t.BORDER_W,
            corner_radius=t.RADIUS_CARD,
        )
        self._right_panel.pack(side="right", fill="y", padx=(10, 0))
        self._right_panel.pack_propagate(False)
        self._build_sidebar_contents(self._right_panel)

    def _build_sidebar_contents(self, parent: ctk.CTkFrame) -> None:
        p = ctk.CTkFrame(parent, fg_color="transparent")
        p.pack(fill="both", expand=True, padx=14, pady=14)

        # Title
        ctk.CTkLabel(
            p,
            text="FOLDERS & WORKSPACE",
            font=t.font(11, bold=True),
            text_color=t.ACCENT_SOFT,
        ).pack(anchor="w", pady=(0, 10))

        # Input folder
        ctk.CTkLabel(p, text="Texture Input Folder:", font=t.font(11), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._in_entry = c.entry(p)
        self._in_entry.pack(fill="x", pady=(2, 6))

        in_btns = ctk.CTkFrame(p, fg_color="transparent")
        in_btns.pack(fill="x", pady=(0, 12))
        c.secondary_button(in_btns, "Browse...", self._browse_input, width=85).pack(side="left")
        c.primary_button(in_btns, "Scan Folder", self._rescan, width=95).pack(side="right")

        # Redo folder
        ctk.CTkLabel(p, text="Redo Triage Folder:", font=t.font(11), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._redo_entry = c.entry(p)
        self._redo_entry.pack(fill="x", pady=(2, 6))
        c.secondary_button(p, "Browse Redo...", self._browse_redo, width=110).pack(anchor="w", pady=(0, 14))

        # Transfer mode
        ctk.CTkLabel(p, text="Redo Transfer Action:", font=t.font(11), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._mode_opt = ctk.CTkOptionMenu(
            p,
            values=["copy", "move"],
            command=self._on_mode_change,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._mode_opt.set("copy")
        self._mode_opt.pack(fill="x", pady=(2, 16))

        # Divider
        ctk.CTkFrame(p, height=1, fg_color=t.CARD_BORDER).pack(fill="x", pady=(0, 14))

        # Progress Stats
        ctk.CTkLabel(p, text="SESSION STATS", font=t.font(11, bold=True), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._stats_lbl = ctk.CTkLabel(
            p,
            text="Total: 0\nPending: 0\nMarked OK: 0\nSent Redo: 0",
            font=t.mono(11),
            text_color=t.TEXT_MAIN,
            justify="left",
            anchor="w",
        )
        self._stats_lbl.pack(fill="x", pady=(6, 14))

        # Tips
        tips = (
            "Shortcuts:\n"
            "• Left/Right: Prev/Next\n"
            "• Space/Enter: Mark OK\n"
            "• R: Send to Redo\n"
            "• G: Toggle Guides\n"
            "• F / Tab: Max View\n"
            "• S: Toggle Folders\n"
            "• 0: Fit  ·  1: 100% 1:1"
        )
        ctk.CTkLabel(
            p,
            text=tips,
            font=t.font(10),
            text_color=t.TEXT_MUTED,
            justify="left",
            anchor="w",
        ).pack(side="bottom", anchor="w")

    def _build_bottom_dock(self) -> None:
        dock = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=t.BORDER_W,
            corner_radius=t.RADIUS_CARD,
        )
        dock.grid(row=1, column=0, sticky="ew", padx=t.PAD_GRID, pady=(0, t.PAD_GRID))
        body = ctk.CTkFrame(dock, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=8)

        # Row 1: Telemetry bar
        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 6))

        self._title_lbl = ctk.CTkLabel(
            row1,
            text="Ready — Select input folder to begin review",
            font=t.font(12, bold=True),
            text_color=t.TEXT_MAIN,
            anchor="w",
        )
        self._title_lbl.pack(side="left")

        self._res_lbl = ctk.CTkLabel(
            row1,
            text="",
            font=t.font(11),
            text_color=t.TEXT_MUTED,
        )
        self._res_lbl.pack(side="left", padx=14)

        self._status_pill = c.Pill(row1, text="IDLE", state="idle")
        self._status_pill.pack(side="right")

        self._counter_lbl = ctk.CTkLabel(
            row1,
            text="0 / 0",
            font=t.mono(11),
            text_color=t.TEXT_MUTED,
        )
        self._counter_lbl.pack(side="right", padx=14)

        # Row 2: Action Controls Row
        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x")

        c.secondary_button(row2, "◀ Prev", lambda: self._step(-1), width=75).pack(side="left", padx=(0, 6))
        c.secondary_button(row2, "Next ▶", lambda: self._step(1), width=75).pack(side="left", padx=(0, 14))

        self._btn_ok = c.primary_button(row2, "✓ Mark OK [Space]", lambda: self._decide("ok"), width=130)
        self._btn_ok.pack(side="left", padx=(0, 6))

        self._btn_redo = c.danger_button(row2, "✗ Send Redo [R]", lambda: self._decide("redo"), width=125)
        self._btn_redo.pack(side="left", padx=(0, 14))

        self._guide_btn = c.secondary_button(row2, "Grid [G]", self._toggle_guides, width=70)
        self._guide_btn.pack(side="left", padx=(0, 6))

        c.secondary_button(row2, "Fit [0]", self.canvas.fit, width=55).pack(side="left", padx=(0, 6))
        c.secondary_button(row2, "1:1 [1]", self.canvas.actual_size, width=55).pack(side="left", padx=(0, 14))

        self._folders_btn = c.secondary_button(row2, "📁 Folders [S]", self._toggle_right_panel, width=105)
        self._folders_btn.pack(side="right")

        self._focus_btn = c.secondary_button(row2, "⛶ Max View [F]", self._toggle_focus_mode, width=105)
        self._focus_btn.pack(side="right", padx=(0, 6))

    # ------------------------------------------------------------------ Settings & State
    def _load_saved_settings(self) -> None:
        settings = e.load_settings()
        self._input_root = settings.get("input_root", "")
        self._redo_root = settings.get("redo_root", "")
        mode = settings.get("mode", "copy")

        if self._input_root:
            self._in_entry.delete(0, "end")
            self._in_entry.insert(0, self._input_root)
        if self._redo_root:
            self._redo_entry.delete(0, "end")
            self._redo_entry.insert(0, self._redo_root)
        if mode in ("copy", "move"):
            self._mode_opt.set(mode)

        if self._input_root and Path(self._input_root).is_dir():
            self._rescan()

    def _save_settings(self) -> None:
        e.save_settings(
            input_root=self._in_entry.get().strip(),
            redo_root=self._redo_entry.get().strip(),
            mode=self._mode_opt.get().strip(),
        )

    def _on_mode_change(self, mode: str) -> None:
        self._save_settings()

    # ------------------------------------------------------------------ View Modes
    def _toggle_right_panel(self) -> None:
        self.set_right_panel_visible(not self._right_panel_visible)

    def set_right_panel_visible(self, visible: bool) -> None:
        self._right_panel_visible = visible
        if visible:
            self._right_panel.pack(side="right", fill="y", padx=(10, 0))
            self._folders_btn.configure(
                text="📁 Folders [S]",
                fg_color=t.CARD_BORDER,
                text_color=t.TEXT_MAIN,
            )
        else:
            self._right_panel.pack_forget()
            self._folders_btn.configure(
                text="📁 Folders [S]",
                fg_color="transparent",
                text_color=t.TEXT_MUTED,
            )

    def _toggle_focus_mode(self) -> None:
        if self._right_panel_visible:
            self.set_right_panel_visible(False)
            self._focus_btn.configure(
                text="⛶ Normal View",
                fg_color=t.ACCENT_TINT,
                text_color=t.ACCENT_SOFT,
            )
        else:
            self.set_right_panel_visible(True)
            self._focus_btn.configure(
                text="⛶ Max View [F]",
                fg_color="transparent",
                text_color=t.TEXT_MUTED,
            )

    def _toggle_guides(self) -> None:
        self._seam_guide = not self._seam_guide
        self._guide_btn.configure(
            fg_color=t.ACCENT_BLUE if self._seam_guide else "transparent",
            text_color="#ffffff" if self._seam_guide else t.TEXT_MUTED,
        )
        self._reload_current()

    # ------------------------------------------------------------------ Scanning & Navigation
    def _browse_input(self) -> None:
        path = tk.filedialog.askdirectory(
            title="Select Texture Input Folder",
            initialdir=self._in_entry.get().strip() or None,
            parent=self,
        )
        if path:
            self._in_entry.delete(0, "end")
            self._in_entry.insert(0, path)
            self._save_settings()
            self._rescan()

    def _browse_redo(self) -> None:
        path = tk.filedialog.askdirectory(
            title="Select Redo Folder",
            initialdir=self._redo_entry.get().strip() or None,
            parent=self,
        )
        if path:
            self._redo_entry.delete(0, "end")
            self._redo_entry.insert(0, path)
            self._save_settings()

    def _rescan(self) -> None:
        root_path = self._in_entry.get().strip()
        if not root_path or not Path(root_path).is_dir():
            self._title_lbl.configure(text="Please specify a valid input folder.")
            return

        redo_path = self._redo_entry.get().strip()
        self._save_settings()
        self._status_pill.set_state("SCANNING", "idle")
        self._title_lbl.configure(text=f"Scanning {Path(root_path).name}…")

        def task():
            res = e.scan_checker_sets(root_path, redo_path)
            self._worker_queue.put(("scan", res))

        threading.Thread(target=task, daemon=True).start()

    def _on_scan_completed(self, res: dict[str, Any]) -> None:
        if res.get("error"):
            self._status_pill.set_state("ERROR", "error")
            self._title_lbl.configure(text=f"Scan failed: {res.get('details')}")
            return

        data = res["data"]
        self._items = data["items"]
        total = len(self._items)
        if total == 0:
            self._index = 0
            self._status_pill.set_state("EMPTY", "idle")
            self._title_lbl.configure(text="No texture sets found in this folder.")
            self._counter_lbl.configure(text="0 / 0")
            self.canvas.set_image(None)
            self._update_stats_display(data["progress"])
            return

        # Restore prior position
        pos_key = data.get("position_key")
        self._index = 0
        if pos_key:
            for idx, item in enumerate(self._items):
                if item["key"] == pos_key:
                    self._index = idx
                    break

        self._update_stats_display(data["progress"])
        self._load_current()

    def _update_stats_display(self, progress: dict[str, int]) -> None:
        total = progress.get("total", 0)
        ok_count = progress.get("ok", 0)
        redo_count = progress.get("redo", 0)
        pending = progress.get("pending", 0)
        self._stats_lbl.configure(
            text=f"Total: {total}\nPending: {pending}\nMarked OK: {ok_count}\nSent Redo: {redo_count}"
        )

    def _step(self, delta: int) -> None:
        if not self._items or self._busy:
            return
        new_index = self._index + delta
        if 0 <= new_index < len(self._items):
            self._index = new_index
            self._load_current()

    def _load_current(self) -> None:
        if not self._items or not (0 <= self._index < len(self._items)):
            return
        item = self._items[self._index]
        self._token += 1
        curr_token = self._token
        self._busy = True

        name = item["name"]
        files = item["files"]
        map_count = len(files)
        type_str = f"PBR Set ({map_count} maps)" if map_count > 1 else "Single Texture"
        base_path = Path(item["base_map"])

        self._title_lbl.configure(text=f"[{self._index + 1}/{len(self._items)}] {name} · {type_str}")
        self._counter_lbl.configure(text=f"{self._index + 1} / {len(self._items)}")

        # Decision Pill
        decision = item.get("decision", "pending")
        if decision == "ok":
            self._status_pill.set_state("OK", "done")
        elif decision == "redo":
            self._status_pill.set_state("REDO", "error")
        else:
            self._status_pill.set_state("PENDING", "idle")

        # Save active position
        root_path = self._in_entry.get().strip()
        e.save_position(root_path, item["key"])

        # Async preview generation
        guide = self._seam_guide

        def task():
            try:
                pil_preview = e.build_wrap_preview(base_path, max_tile=768, repeats=3, seam_guide=guide)
                with Image.open(base_path) as orig:
                    orig_res = f"{orig.width}×{orig.height}"
                self._worker_queue.put(("preview", curr_token, pil_preview, orig_res))
            except Exception as ex:
                self._worker_queue.put(("preview_error", curr_token, str(ex)))

        threading.Thread(target=task, daemon=True).start()

    def _reload_current(self) -> None:
        if self._items and 0 <= self._index < len(self._items):
            self._load_current()

    # ------------------------------------------------------------------ Decisions & Transfer
    def _decide(self, decision: str) -> None:
        if not self._items or not (0 <= self._index < len(self._items)) or self._busy:
            return
        item = self._items[self._index]
        root_path = self._in_entry.get().strip()
        redo_path = self._redo_entry.get().strip()
        mode = self._mode_opt.get().strip()

        if decision == "redo" and not redo_path:
            tk.messagebox.showwarning(
                "Redo Folder Missing",
                "Please choose a Redo destination folder before marking Redo.",
                parent=self,
            )
            return

        self._busy = True
        self._status_pill.set_state("TRANSFERRING" if decision == "redo" else "RECORDING", "idle")

        def task():
            res = e.decide_set(root_path, item["key"], decision, redo_path=redo_path, mode=mode)
            self._worker_queue.put(("decision", self._index, decision, res))

        threading.Thread(target=task, daemon=True).start()

    def _on_decision_completed(self, target_idx: int, decision: str, res: dict[str, Any]) -> None:
        self._busy = False
        if res.get("error"):
            self._status_pill.set_state("ERROR", "error")
            tk.messagebox.showerror("Action Failed", res.get("details", "Operation failed"), parent=self)
            return

        if 0 <= target_idx < len(self._items):
            self._items[target_idx]["decision"] = decision

        # Refresh stats count
        counts = [row["decision"] for row in self._items]
        self._update_stats_display({
            "total": len(self._items),
            "ok": counts.count("ok"),
            "redo": counts.count("redo"),
            "pending": counts.count("pending"),
        })

        # Advance to next pending item if available
        if target_idx < len(self._items) - 1:
            self._index = target_idx + 1
            self._load_current()
        else:
            self._load_current()

    # ------------------------------------------------------------------ Worker Polling
    def _poll_worker(self) -> None:
        try:
            while not self._worker_queue.empty():
                msg = self._worker_queue.get_nowait()
                kind = msg[0]
                if kind == "scan":
                    self._on_scan_completed(msg[1])
                elif kind == "preview":
                    token, pil_img, orig_res = msg[1], msg[2], msg[3]
                    if token == self._token:
                        self._busy = False
                        self.canvas.set_image(pil_img)
                        self._res_lbl.configure(text=f"Resolution: {orig_res}")
                elif kind == "preview_error":
                    token, err_msg = msg[1], msg[2]
                    if token == self._token:
                        self._busy = False
                        self.canvas.set_image(None)
                        self._res_lbl.configure(text=f"Error loading: {err_msg}")
                elif kind == "decision":
                    idx, decision, res = msg[1], msg[2], msg[3]
                    self._on_decision_completed(idx, decision, res)
        except Exception as ex:
            _LOG.error("Worker poll exception: %s", ex)
        finally:
            self._poll_job = self.after(25, self._poll_worker)

    # ------------------------------------------------------------------ Key Bindings
    def _bind_keys(self) -> None:
        top = self.winfo_toplevel()
        bindings = [
            ("<Left>", lambda: self._step(-1)),
            ("<Right>", lambda: self._step(1)),
            ("<space>", lambda: self._decide("ok")),
            ("<Return>", lambda: self._decide("ok")),
            ("<r>", lambda: self._decide("redo")),
            ("<R>", lambda: self._decide("redo")),
            ("<g>", self._toggle_guides),
            ("<G>", self._toggle_guides),
            ("<f>", self._toggle_focus_mode),
            ("<F>", self._toggle_focus_mode),
            ("<Tab>", self._toggle_focus_mode),
            ("<s>", self._toggle_right_panel),
            ("<S>", self._toggle_right_panel),
            ("<0>", self.canvas.fit),
            ("<1>", self.canvas.actual_size),
        ]
        for seq, handler in bindings:
            top.bind(seq, lambda e, fn=handler: self._dispatch_key(e, fn), add="+")

    def _dispatch_key(self, event: Any, fn: Any) -> None:
        # Ignore hotkey if a text entry widget has focus
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, ctk.CTkEntry)):
            return
        fn()

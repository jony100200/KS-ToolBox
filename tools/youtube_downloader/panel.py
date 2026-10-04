"""YouTube Downloader UI panel — 3-Panel High-Density Widescreen Dashboard.

Features:
  - Full-height adaptive viewport expansion (zero vertical dead space)
  - Visual video cards with live 16:9 thumbnail previews and burned duration badges
  - Left Panel:   Source link input, structured asset & format inspector, save destination
  - Center Panel: Full-height, clutter-free scanned collection table with search filter
  - Right Panel:  Queue execution, high-contrast action button, and color-coded activity terminal
"""
from __future__ import annotations

import io
import os
import threading
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e

# Thumbnail dimensions (16:9 ratio)
THUMB_W = 76
THUMB_H = 43


class YouTubeDownloaderPanel(ctk.CTkFrame):
    def __init__(self, parent, services=None):
        super().__init__(parent, fg_color=t.BG_COLOR)

        self._host = parent
        self._services = services
        self._items: list[e.VideoItem] = []
        self._item_rows: list[dict] = []
        self._current_result: e.FetchResult | None = None
        self._thumb_cache: dict[str, ctk.CTkImage] = {}

        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._is_working = False

        # Default output folder: ~/Downloads/YouTube_Downloads
        self._default_out_dir = Path.home() / "Downloads" / "YouTube_Downloads"

        # 3-Panel Grid Configuration:
        # Col 0: Source & Asset Inspector (~340px)
        # Col 1: Expansive Scanned Video Table (Center, weight=5)
        # Col 2: Queue & Color-Coded Log Console (~340px)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=3, minsize=320)
        self.grid_columnconfigure(1, weight=5, minsize=440)
        self.grid_columnconfigure(2, weight=3, minsize=320)

        # Build 3 Panels
        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

        # Dynamic Full-Height Viewport Auto-Expansion:
        # Ensures the 3 panels stretch 100% to the bottom of the window on any monitor resolution
        self.bind("<Configure>", self._on_configure_expand)
        if hasattr(parent, "bind"):
            parent.bind("<Configure>", self._on_configure_expand)
        if hasattr(parent, "_parent_canvas"):
            parent._parent_canvas.bind("<Configure>", self._on_configure_expand)

    def _on_configure_expand(self, event=None):
        try:
            # Expand host CTkScrollableFrame canvas window to 100% viewport height
            if hasattr(self._host, "grid_rowconfigure"):
                self._host.grid_rowconfigure(0, weight=1)

            canvas = getattr(self._host, "_parent_canvas", None)
            create_win = getattr(self._host, "_create_window_id", None)
            if canvas is not None and create_win is not None:
                h = canvas.winfo_height()
                if h > 200:
                    canvas.itemconfigure(create_win, height=max(h, 560))
        except Exception:
            pass

    # =========================================================================
    # Panel 1 (Left): Setup, Source Link, Asset Inspector, Destination
    # =========================================================================
    def _build_left_panel(self):
        card = c.Card(self, "1. Source & Assets", icon=Icons.GEAR)
        card.grid(row=0, column=0, sticky="nsew", padx=(t.PAD_GRID, t.PAD_GRID // 2), pady=t.PAD_GRID)
        b = card.body

        scroll = ctk.CTkScrollableFrame(b, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # --- Section A: YouTube URL Input ---
        ctk.CTkLabel(
            scroll, text="YOUTUBE URL (VIDEO / PLAYLIST / CHANNEL)",
            font=t.font(10, bold=True), text_color=t.ACCENT_SOFT
        ).pack(anchor="w", pady=(0, 4))

        self._url_entry = c.entry(
            scroll,
            placeholder_text="Paste YouTube URL here...",
            height=32,
        )
        self._url_entry.pack(fill="x", pady=(0, 6))
        self._url_entry.bind("<Return>", lambda _: self._on_inspect())

        # Link Buttons Row
        link_btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        link_btn_row.pack(fill="x", pady=(0, 8))
        link_btn_row.grid_columnconfigure(0, weight=3)
        link_btn_row.grid_columnconfigure(1, weight=2)
        link_btn_row.grid_columnconfigure(2, weight=2)

        # Vibrant action button for scanning
        self._scan_btn = ctk.CTkButton(
            link_btn_row, text="Scan Link", command=self._on_inspect, height=30,
            fg_color="#0D9488", hover_color="#0F766E", font=t.font(11, bold=True)
        )
        self._scan_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        paste_btn = c.secondary_button(link_btn_row, text="Paste", command=self._on_paste, height=30)
        paste_btn.grid(row=0, column=1, sticky="ew", padx=2)

        clear_btn = c.ghost_button(link_btn_row, text="Clear", command=self._on_clear, height=30)
        clear_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # Status badge & info
        status_row = ctk.CTkFrame(scroll, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 10))
        self._status_pill = c.Pill(status_row, text="IDLE", state="idle")
        self._status_pill.pack(side="left")

        self._info_label = ctk.CTkLabel(
            status_row,
            text="Ready for link",
            text_color=t.TEXT_MUTED,
            font=t.font(10),
            anchor="w",
        )
        self._info_label.pack(side="left", padx=8, fill="x", expand=True)

        self._add_divider(scroll)

        # --- Section B: What to Download (Structured Asset & Format Inspector) ---
        ctk.CTkLabel(
            scroll, text="WHAT TO DOWNLOAD (CHECK ASSETS)",
            font=t.font(10, bold=True), text_color=t.ACCENT_SOFT
        ).pack(anchor="w", pady=(6, 4))

        # Table container matching Mock Image 2
        opt_box = ctk.CTkFrame(scroll, fg_color=t.BG_COLOR, corner_radius=t.RADIUS_CARD)
        opt_box.pack(fill="x", pady=(0, 10), ipady=4, padx=1)

        # Table Sub-Header
        th = ctk.CTkFrame(opt_box, fg_color="transparent")
        th.pack(fill="x", padx=8, pady=(4, 2))
        th.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(th, text="Asset", font=t.font(9, bold=True), text_color=t.TEXT_MUTED, width=85, anchor="w").grid(row=0, column=0)
        ctk.CTkLabel(th, text="Format", font=t.font(9, bold=True), text_color=t.TEXT_MUTED, anchor="w").grid(row=0, column=1, padx=4)
        ctk.CTkLabel(th, text="Est. Size", font=t.font(9, bold=True), text_color=t.TEXT_MUTED, width=55, anchor="e").grid(row=0, column=2)

        # 1. Thumbnails Row
        t_row = ctk.CTkFrame(opt_box, fg_color="transparent")
        t_row.pack(fill="x", padx=8, pady=3)
        t_row.grid_columnconfigure(1, weight=1)

        self._cb_thumb = ctk.CTkCheckBox(
            t_row, text="Thumbnails", font=t.font(11, bold=True), fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled, width=85
        )
        self._cb_thumb.select()
        self._cb_thumb.grid(row=0, column=0, sticky="w")

        self._opt_thumb_q = ctk.CTkOptionMenu(
            t_row, values=["MaxRes (1080p)", "Standard (640x480)", "HQ (480x360)"],
            height=24, font=t.font(10), fg_color=t.CARD_BG, button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER
        )
        self._opt_thumb_q.set("MaxRes (1080p)")
        self._opt_thumb_q.grid(row=0, column=1, sticky="ew", padx=4)

        self._thumb_size_lbl = ctk.CTkLabel(t_row, text="~1.2 MB", font=t.mono(10), text_color=t.TEXT_MUTED, width=55, anchor="e")
        self._thumb_size_lbl.grid(row=0, column=2)

        # 2. Video (MP4) Row
        v_row = ctk.CTkFrame(opt_box, fg_color="transparent")
        v_row.pack(fill="x", padx=8, pady=3)
        v_row.grid_columnconfigure(1, weight=1)

        self._cb_video = ctk.CTkCheckBox(
            v_row, text="Video (MP4)", font=t.font(11, bold=True), fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled, width=85
        )
        self._cb_video.grid(row=0, column=0, sticky="w")

        self._opt_video_q = ctk.CTkOptionMenu(
            v_row, values=["Best Available", "1080p Full HD", "720p HD", "480p", "360p"],
            height=24, font=t.font(10), fg_color=t.CARD_BG, button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER
        )
        self._opt_video_q.set("Best Available")
        self._opt_video_q.configure(state="disabled")
        self._opt_video_q.grid(row=0, column=1, sticky="ew", padx=4)

        self._video_size_lbl = ctk.CTkLabel(v_row, text="~150 MB", font=t.mono(10), text_color=t.TEXT_MUTED, width=55, anchor="e")
        self._video_size_lbl.grid(row=0, column=2)

        # 3. Audio Only Row
        a_row = ctk.CTkFrame(opt_box, fg_color="transparent")
        a_row.pack(fill="x", padx=8, pady=3)
        a_row.grid_columnconfigure(1, weight=1)

        self._cb_audio = ctk.CTkCheckBox(
            a_row, text="Audio Only", font=t.font(11, bold=True), fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled, width=85
        )
        self._cb_audio.grid(row=0, column=0, sticky="w")

        self._opt_audio_f = ctk.CTkOptionMenu(
            a_row, values=["MP3 (192k)", "M4A (AAC)", "WAV", "Original Best"],
            height=24, font=t.font(10), fg_color=t.CARD_BG, button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER
        )
        self._opt_audio_f.set("MP3 (192k)")
        self._opt_audio_f.configure(state="disabled")
        self._opt_audio_f.grid(row=0, column=1, sticky="ew", padx=4)

        self._audio_size_lbl = ctk.CTkLabel(a_row, text="~8 MB", font=t.mono(10), text_color=t.TEXT_MUTED, width=55, anchor="e")
        self._audio_size_lbl.grid(row=0, column=2)

        # 4. Subtitles Row
        s_row = ctk.CTkFrame(opt_box, fg_color="transparent")
        s_row.pack(fill="x", padx=8, pady=3)
        s_row.grid_columnconfigure(1, weight=1)

        self._cb_sub = ctk.CTkCheckBox(
            s_row, text="Subtitles", font=t.font(11, bold=True), fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled, width=85
        )
        self._cb_sub.grid(row=0, column=0, sticky="w")

        self._opt_sub_l = ctk.CTkOptionMenu(
            s_row, values=["English (en)", "All Available"],
            height=24, font=t.font(10), fg_color=t.CARD_BG, button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER
        )
        self._opt_sub_l.set("English (en)")
        self._opt_sub_l.configure(state="disabled")
        self._opt_sub_l.grid(row=0, column=1, sticky="ew", padx=4)

        self._sub_size_lbl = ctk.CTkLabel(s_row, text="~25 KB", font=t.mono(10), text_color=t.TEXT_MUTED, width=55, anchor="e")
        self._sub_size_lbl.grid(row=0, column=2)

        self._add_divider(scroll)

        # --- Section C: Save Destination ---
        ctk.CTkLabel(
            scroll, text="SAVE DESTINATION",
            font=t.font(10, bold=True), text_color=t.ACCENT_SOFT
        ).pack(anchor="w", pady=(6, 4))

        self._out_entry = c.entry(scroll, height=28)
        self._out_entry.insert(0, str(self._default_out_dir))
        self._out_entry.pack(fill="x", pady=(0, 4))

        dest_btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        dest_btn_row.pack(fill="x", pady=(0, 6))
        dest_btn_row.grid_columnconfigure(0, weight=1)
        dest_btn_row.grid_columnconfigure(1, weight=1)

        browse_btn = c.secondary_button(dest_btn_row, text="Browse...", command=self._on_browse_out, height=26, font=t.font(10))
        browse_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        open_btn = c.ghost_button(dest_btn_row, text="Open Folder", command=self._on_open_out, height=26, font=t.font(10))
        open_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # Organization Checkboxes
        self._cb_subfolder = ctk.CTkCheckBox(
            scroll, text="Subfolder per collection", font=t.font(10), fg_color=t.ACCENT_BLUE
        )
        self._cb_subfolder.select()
        self._cb_subfolder.pack(anchor="w", pady=(1, 2))

        self._cb_prefix_num = ctk.CTkCheckBox(
            scroll, text="Number sequentially (01 - Title)", font=t.font(10), fg_color=t.ACCENT_BLUE
        )
        self._cb_prefix_num.select()
        self._cb_prefix_num.pack(anchor="w", pady=(0, 4))

    # =========================================================================
    # Panel 2 (Center): Expansive Visual Scanned Video Collection with Previews
    # =========================================================================
    def _build_center_panel(self):
        card = c.Card(self, "2. Scanned Collection", icon=Icons.LAYERS)
        card.grid(row=0, column=1, sticky="nsew", padx=t.PAD_GRID // 2, pady=t.PAD_GRID)
        b = card.body

        # Top Toolbar: Count, Filter, Select / Deselect
        tool_bar = ctk.CTkFrame(b, fg_color="transparent")
        tool_bar.pack(fill="x", pady=(0, 6))
        tool_bar.grid_columnconfigure(1, weight=1)

        self._items_count_label = ctk.CTkLabel(
            tool_bar, text="0 items loaded", font=t.font(11, bold=True), text_color=t.TEXT_MAIN
        )
        self._items_count_label.grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._filter_entry = c.entry(tool_bar, placeholder_text="Filter videos...", height=26)
        self._filter_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._filter_entry.bind("<KeyRelease>", lambda _: self._apply_filter())

        self._select_all_btn = c.ghost_button(
            tool_bar, text="Select All", command=self._on_select_all, width=70, height=26, font=t.font(10)
        )
        self._select_all_btn.grid(row=0, column=2, padx=2)

        self._deselect_all_btn = c.ghost_button(
            tool_bar, text="Deselect", command=self._on_deselect_all, width=65, height=26, font=t.font(10)
        )
        self._deselect_all_btn.grid(row=0, column=3, padx=(2, 0))

        # Main Table Header Row matching Mock Image 2
        th_row = ctk.CTkFrame(b, fg_color=t.CARD_BG, height=26, corner_radius=t.RADIUS_CARD)
        th_row.pack(fill="x", pady=(0, 4))
        th_row.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(th_row, text="#", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, width=28).grid(row=0, column=0, padx=4)
        ctk.CTkLabel(th_row, text="PREVIEW", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, width=THUMB_W).grid(row=0, column=1, padx=4)
        ctk.CTkLabel(th_row, text="TITLE", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, anchor="w").grid(row=0, column=2, sticky="w", padx=6)
        ctk.CTkLabel(th_row, text="DURATION", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, width=60).grid(row=0, column=3, padx=4)
        ctk.CTkLabel(th_row, text="SELECT", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, width=50).grid(row=0, column=4, padx=4)
        ctk.CTkLabel(th_row, text="STATUS", font=t.font(10, bold=True), text_color=t.TEXT_MUTED, width=70).grid(row=0, column=5, padx=(4, 8))

        # Full-height scrollable list container
        self._items_container = ctk.CTkScrollableFrame(b, fg_color=t.BG_COLOR, corner_radius=t.RADIUS_CARD)
        self._items_container.pack(fill="both", expand=True)
        self._items_container.grid_columnconfigure(2, weight=1)

        # Placeholder
        self._empty_label = ctk.CTkLabel(
            self._items_container,
            text="No collection scanned yet.\n\nPaste a YouTube playlist, channel, or video link on the left\nand click 'Scan Link' to view thumbnail previews and videos here.",
            text_color=t.TEXT_MUTED,
            font=t.font(11),
            justify="center",
        )
        self._empty_label.pack(pady=100)

    # =========================================================================
    # Panel 3 (Right): Queue Execution, Progress Stats, Color-Coded Activity Log
    # =========================================================================
    def _build_right_panel(self):
        card = c.Card(self, "3. Queue & Logs", icon=Icons.PLAY)
        card.grid(row=0, column=2, sticky="nsew", padx=(t.PAD_GRID // 2, t.PAD_GRID), pady=t.PAD_GRID)
        b = card.body

        # Action Buttons Row
        act_row = ctk.CTkFrame(b, fg_color="transparent")
        act_row.pack(fill="x", pady=(0, 8))
        act_row.grid_columnconfigure(0, weight=3)
        act_row.grid_columnconfigure(1, weight=1)

        # Vibrant teal/gradient primary download button matching Image 2
        self._download_btn = ctk.CTkButton(
            act_row,
            text="Start Download",
            command=self._on_start_download,
            height=36,
            fg_color="#0D9488",
            hover_color="#14B8A6",
            font=t.font(12, bold=True),
        )
        self._download_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._cancel_btn = c.danger_button(
            act_row,
            text="Cancel",
            command=self._on_cancel,
            height=36,
            font=t.font(11, bold=True),
        )
        self._cancel_btn.grid(row=0, column=1, sticky="ew")
        self._cancel_btn.configure(state="disabled")

        # Progress Stats Box
        stats_box = ctk.CTkFrame(b, fg_color=t.BG_COLOR, corner_radius=t.RADIUS_CARD)
        stats_box.pack(fill="x", pady=(0, 8), padx=1, ipady=4)

        self._progress_status_label = ctk.CTkLabel(
            stats_box,
            text="Queue Idle — Waiting for download",
            font=t.font(11),
            text_color=t.TEXT_MAIN,
            anchor="w",
        )
        self._progress_status_label.pack(fill="x", padx=10, pady=(4, 2))

        # Vibrant progress bar matching Image 2
        self._progress_bar = ctk.CTkProgressBar(
            stats_box, height=8, fg_color=t.CARD_BORDER, progress_color="#14B8A6"
        )
        self._progress_bar.pack(fill="x", padx=10, pady=(2, 4))
        self._progress_bar.set(0)

        self._progress_metrics_label = ctk.CTkLabel(
            stats_box,
            text="0 / 0 completed",
            font=t.mono(10),
            text_color=t.TEXT_MUTED,
            anchor="w",
        )
        self._progress_metrics_label.pack(fill="x", padx=10, pady=(0, 4))

        # Color-Coded Activity Console
        log_header = ctk.CTkFrame(b, fg_color="transparent")
        log_header.pack(fill="x", pady=(4, 2))

        ctk.CTkLabel(log_header, text="ACTIVITY LOG", font=t.font(10, bold=True), text_color=t.TEXT_MUTED).pack(side="left")
        c.ghost_button(log_header, text="Clear Log", command=self._on_clear_log, width=60, height=20, font=t.font(9)).pack(side="right")

        self._log_box = ctk.CTkTextbox(
            b, fg_color=t.BG_COLOR, text_color=t.TEXT_MUTED, font=t.mono(10), corner_radius=t.RADIUS_CARD
        )
        self._log_box.pack(fill="both", expand=True, pady=(2, 0))

        # Configure Terminal Tags for Syntax-Colored Console matching Mock Image 2
        tb = self._log_box._textbox
        tb.tag_config("INFO", foreground="#38BDF8")     # Sky Blue
        tb.tag_config("SCAN", foreground="#4ADE80")     # Mint Green
        tb.tag_config("QUEUED", foreground="#FBBF24")   # Amber
        tb.tag_config("DONE", foreground="#34D399")     # Emerald Green
        tb.tag_config("ERROR", foreground="#F87171")    # Coral Red
        tb.tag_config("WARN", foreground="#FB923C")     # Orange
        tb.tag_config("TEXT", foreground="#D1D5DB")     # Off-white Text

        self._log_colored("INFO", "YouTube Downloader: Initialized")
        self._log_colored("INFO", "Ready for video, playlist, or channel URL")

    def _add_divider(self, parent):
        div = ctk.CTkFrame(parent, fg_color=t.CARD_BORDER, height=1)
        div.pack(fill="x", pady=4)

    # =========================================================================
    # Logging & Colored Terminal
    # =========================================================================
    def _log_colored(self, level: str, message: str):
        tb = self._log_box._textbox
        tb.insert("end", f"[{level}] ", level)
        tb.insert("end", f"{message}\n", "TEXT")
        tb.see("end")

    def _on_clear_log(self):
        self._log_box.delete("1.0", "end")

    # =========================================================================
    # Thumbnail Loading & Image Badge Generation
    # =========================================================================
    def _make_placeholder_thumbnail(self, index: int) -> ctk.CTkImage:
        """Create a sleek dark placeholder image before thumbnail finishes downloading."""
        img = Image.new("RGBA", (THUMB_W, THUMB_H), (20, 28, 42, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, THUMB_W - 1, THUMB_H - 1], outline=(35, 48, 68, 255))
        draw.text((THUMB_W // 2 - 10, THUMB_H // 2 - 6), f"#{index:02d}", fill=(100, 116, 139, 255))
        return ctk.CTkImage(light_image=img, dark_image=img, size=(THUMB_W, THUMB_H))

    def _fetch_thumbnail_async(self, item: e.VideoItem, label_widget: ctk.CTkLabel):
        """Asynchronously download and burn the duration badge onto the thumbnail."""
        if item.video_id in self._thumb_cache:
            cached_img = self._thumb_cache[item.video_id]
            self.after(0, lambda: label_widget.configure(image=cached_img))
            return

        def fetcher():
            try:
                urls_to_try = []
                if item.thumbnail_url:
                    urls_to_try.append(item.thumbnail_url)
                if item.video_id:
                    urls_to_try.append(f"https://img.youtube.com/vi/{item.video_id}/mqdefault.jpg")
                    urls_to_try.append(f"https://img.youtube.com/vi/{item.video_id}/hqdefault.jpg")

                raw_bytes = None
                for u in urls_to_try:
                    try:
                        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            data = resp.read()
                            if data and len(data) > 600:
                                raw_bytes = data
                                break
                    except Exception:
                        continue

                if not raw_bytes:
                    return

                pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                pil_img = pil_img.resize((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)

                # Burn duration timestamp badge in bottom-right corner (YouTube style)
                if item.duration_str and item.duration_str != "--:--":
                    draw = ImageDraw.Draw(pil_img)
                    text = item.duration_str
                    badge_w = len(text) * 6 + 4
                    draw.rectangle(
                        [THUMB_W - badge_w - 4, THUMB_H - 13, THUMB_W - 2, THUMB_H - 2],
                        fill=(15, 23, 42, 220)
                    )
                    draw.text((THUMB_W - badge_w - 2, THUMB_H - 13), text, fill=(255, 255, 255, 240))

                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(THUMB_W, THUMB_H))
                self._thumb_cache[item.video_id] = ctk_img
                self.after(0, lambda: label_widget.configure(image=ctk_img))
            except Exception:
                pass

        threading.Thread(target=fetcher, daemon=True).start()

    # =========================================================================
    # User Interactions & Live Filter
    # =========================================================================
    def _on_paste(self):
        try:
            clipboard = self.clipboard_get().strip()
            if clipboard:
                self._url_entry.delete(0, "end")
                self._url_entry.insert(0, clipboard)
        except Exception:
            pass

    def _on_clear(self):
        self._url_entry.delete(0, "end")
        self._clear_items()
        self._status_pill.set_state("IDLE", "idle")
        self._info_label.configure(text="Ready for link")

    def _on_browse_out(self):
        folder = filedialog.askdirectory(initialdir=str(self._default_out_dir))
        if folder:
            self._out_entry.delete(0, "end")
            self._out_entry.insert(0, folder)

    def _on_open_out(self):
        target = Path(self._out_entry.get().strip() or self._default_out_dir)
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))
        except Exception as ex:
            messagebox.showwarning("Open Folder", f"Could not open folder:\n{ex}")

    def _on_checkbox_toggled(self):
        self._opt_thumb_q.configure(state="normal" if self._cb_thumb.get() else "disabled")
        self._opt_video_q.configure(state="normal" if self._cb_video.get() else "disabled")
        self._opt_audio_f.configure(state="normal" if self._cb_audio.get() else "disabled")
        self._opt_sub_l.configure(state="normal" if self._cb_sub.get() else "disabled")

        has_any = (
            self._cb_thumb.get()
            or self._cb_video.get()
            or self._cb_audio.get()
            or self._cb_sub.get()
        )
        if not has_any:
            self._log_colored("WARN", "Select at least one asset type (Thumbnails, Video, Audio, or Subtitles).")

    def _on_select_all(self):
        for entry in self._item_rows:
            entry["cb"].select()
        self._update_selected_count()

    def _on_deselect_all(self):
        for entry in self._item_rows:
            entry["cb"].deselect()
        self._update_selected_count()

    def _apply_filter(self):
        query = self._filter_entry.get().strip().lower()
        visible = 0
        for entry in self._item_rows:
            title = entry["item"].title.lower()
            if not query or query in title:
                entry["frame"].pack(fill="x", pady=2, padx=2)
                visible += 1
            else:
                entry["frame"].pack_forget()
        self._update_selected_count(visible_override=visible if query else None)

    def _update_selected_count(self, visible_override: int | None = None):
        selected = sum(1 for e in self._item_rows if e["cb"].get())
        total = len(self._item_rows)
        if visible_override is not None and visible_override != total:
            self._items_count_label.configure(text=f"{visible_override} shown ({selected} sel)")
        else:
            self._items_count_label.configure(text=f"{total} items ({selected} selected)")

    def _clear_items(self):
        self._items = []
        self._item_rows = []
        for child in self._items_container.winfo_children():
            child.destroy()
        self._empty_label = ctk.CTkLabel(
            self._items_container,
            text="No collection scanned yet.\n\nPaste a YouTube link and click 'Scan Link'.",
            text_color=t.TEXT_MUTED,
            font=t.font(11),
            justify="center",
        )
        self._empty_label.pack(pady=100)
        self._items_count_label.configure(text="0 items loaded")

    # =========================================================================
    # Scanning & Background Fetch
    # =========================================================================
    def _on_inspect(self):
        url = self._url_entry.get().strip()
        if not url:
            messagebox.showinfo("Missing URL", "Please enter or paste a YouTube URL first.")
            return

        if self._is_working:
            return

        self._is_working = True
        self._scan_btn.configure(state="disabled")
        self._status_pill.set_state("SCANNING", "waiting")
        self._info_label.configure(text="Scanning YouTube metadata...")
        self._log_colored("SCAN", f"Scanning URL: {url}")

        def worker():
            res = e.fetch_url_info(url)
            self.after(0, lambda: self._on_inspect_complete(res))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_inspect_complete(self, res: e.FetchResult):
        self._is_working = False
        self._scan_btn.configure(state="normal")
        self._current_result = res

        if res.error:
            self._status_pill.set_state("ERROR", "error")
            self._info_label.configure(text=f"Scan Error: {res.error[:30]}")
            self._log_colored("ERROR", f"Scan error: {res.error}")
            return

        self._items = res.items
        self._status_pill.set_state("READY", "done")

        type_str = res.url_type.capitalize()
        summary = f"{type_str}: '{res.title}' ({len(res.items)} video{'s' if len(res.items) != 1 else ''})"
        self._info_label.configure(text=res.title[:30])
        self._log_colored("INFO", f"Scan successful: {summary}")

        # Render items into container with real thumbnail image labels
        for child in self._items_container.winfo_children():
            child.destroy()
        self._item_rows.clear()

        for idx, item in enumerate(self._items, 1):
            row = ctk.CTkFrame(self._items_container, fg_color=t.CARD_BG, corner_radius=t.RADIUS_CARD)
            row.pack(fill="x", pady=2, padx=2)
            row.grid_columnconfigure(2, weight=1)

            # 1. Number Index
            idx_lbl = ctk.CTkLabel(row, text=f"#{item.index:02d}", font=ctk.CTkFont(family="Consolas", size=10, weight="bold"), text_color=t.TEXT_MUTED, width=28)
            idx_lbl.grid(row=0, column=0, padx=4)

            # 2. Thumbnail Preview Image Label (Starts with placeholder, lazy loads real thumbnail)
            thumb_ph = self._make_placeholder_thumbnail(item.index)
            thumb_lbl = ctk.CTkLabel(row, image=thumb_ph, text="", width=THUMB_W, height=THUMB_H)
            thumb_lbl.grid(row=0, column=1, padx=4, pady=3)
            self._fetch_thumbnail_async(item, thumb_lbl)

            # 3. Title (Clean wrappable label)
            title_lbl = ctk.CTkLabel(
                row,
                text=item.title,
                font=t.font(11),
                text_color=t.TEXT_MAIN,
                anchor="w",
                justify="left",
            )
            title_lbl.grid(row=0, column=2, sticky="ew", padx=6)

            # 4. Duration
            dur_lbl = ctk.CTkLabel(
                row,
                text=item.duration_str,
                font=t.mono(10),
                text_color=t.TEXT_MUTED,
                width=60,
                anchor="center",
            )
            dur_lbl.grid(row=0, column=3, padx=4)

            # 5. Selection Checkbox
            cb = ctk.CTkCheckBox(row, text="", width=24, fg_color=t.ACCENT_BLUE, command=self._update_selected_count)
            cb.select()
            cb.grid(row=0, column=4, padx=4)

            # 6. Status Label
            stat_lbl = ctk.CTkLabel(
                row,
                text="Queued",
                font=t.font(10),
                text_color=t.TEXT_MUTED,
                width=70,
                anchor="e",
            )
            stat_lbl.grid(row=0, column=5, padx=(4, 8))

            self._item_rows.append({
                "item": item,
                "cb": cb,
                "stat": stat_lbl,
                "frame": row,
            })

        self._update_selected_count()
        self._log_colored("QUEUED", f"Ready to download {len(self._items)} items from collection")

    # =========================================================================
    # Queue Execution & Asset Downloading
    # =========================================================================
    def _on_start_download(self):
        if not self._items:
            messagebox.showinfo("No Items", "Please scan a collection first.")
            return

        selected_entries = [entry for entry in self._item_rows if entry["cb"].get()]
        if not selected_entries:
            messagebox.showinfo("No Selection", "Please check at least one video to download.")
            return

        dl_thumb = bool(self._cb_thumb.get())
        dl_video = bool(self._cb_video.get())
        dl_audio = bool(self._cb_audio.get())
        dl_sub = bool(self._cb_sub.get())

        if not (dl_thumb or dl_video or dl_audio or dl_sub):
            messagebox.showwarning(
                "No Asset Selected",
                "Please select at least one asset to download:\n- Thumbnails\n- Video\n- Audio\n- Subtitles",
            )
            return

        base_out = Path(self._out_entry.get().strip() or self._default_out_dir)
        if self._cb_subfolder.get() and self._current_result and self._current_result.title:
            folder_name = e.sanitize_filename(self._current_result.title)
            target_out = base_out / folder_name
        else:
            target_out = base_out

        thumb_q_map = {
            "MaxRes (1080p)": "max",
            "Standard (640x480)": "sd",
            "HQ (480x360)": "hq",
        }
        video_q_map = {
            "Best Available": "best",
            "1080p Full HD": "1080p",
            "720p HD": "720p",
            "480p": "480p",
            "360p": "360p",
        }
        audio_f_map = {
            "MP3 (192k)": "mp3",
            "M4A (AAC)": "m4a",
            "WAV": "wav",
            "Original Best": "best",
        }
        sub_l_map = {
            "English (en)": "en",
            "All Available": "all",
        }

        opts = e.DownloadOptions(
            download_thumbnails=dl_thumb,
            download_video=dl_video,
            download_audio=dl_audio,
            download_subtitles=dl_sub,
            thumbnail_quality=thumb_q_map.get(self._opt_thumb_q.get(), "max"),
            video_quality=video_q_map.get(self._opt_video_q.get(), "best"),
            audio_format=audio_f_map.get(self._opt_audio_f.get(), "mp3"),
            subtitle_lang=sub_l_map.get(self._opt_sub_l.get(), "en"),
            prefix_number=bool(self._cb_prefix_num.get()),
        )

        self._is_working = True
        self._cancel_event.clear()
        self._download_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._scan_btn.configure(state="disabled")
        self._status_pill.set_state("DOWNLOADING", "running")
        self._progress_bar.set(0)

        self._log_colored("SCAN", f"Starting queue: {len(selected_entries)} items -> {target_out.name}")

        def worker():
            total = len(selected_entries)
            completed = 0
            for idx, entry in enumerate(selected_entries, 1):
                if self._cancel_event.is_set():
                    self.after(0, lambda: self._log_colored("WARN", "Download cancelled by user."))
                    break

                item = entry["item"]
                stat_lbl = entry["stat"]

                def update_start(lbl=stat_lbl, it=item, i=idx):
                    lbl.configure(text="Downloading...", text_color=t.ACCENT_SOFT)
                    self._progress_status_label.configure(text=f"[{i}/{total}] {it.title[:35]}...")
                    self._progress_metrics_label.configure(text=f"{i - 1} / {total} completed")

                self.after(0, update_start)

                def p_cb(msg: str, frac: float):
                    overall = (idx - 1 + frac) / total
                    self.after(0, lambda m=msg, o=overall: self._on_item_progress(m, o))

                ok, msg = e.download_item(
                    item,
                    target_out,
                    opts,
                    progress_cb=p_cb,
                    cancel_check=lambda: self._cancel_event.is_set(),
                )

                if self._cancel_event.is_set():
                    break

                if ok:
                    completed += 1

                def update_done(lbl=stat_lbl, success=ok, m=msg, c_idx=completed, it=item):
                    if success:
                        lbl.configure(text="Done ✓", text_color=t.STATE["done"][1])
                        self._log_colored("DONE", f"[{it.index:02d}] {it.title[:32]} -> {m}")
                    else:
                        lbl.configure(text="Error ✗", text_color=t.STATE["error"][1])
                        self._log_colored("ERROR", f"[{it.index:02d}] {it.title[:32]} -> {m}")
                    self._progress_bar.set(c_idx / total)
                    self._progress_metrics_label.configure(text=f"{c_idx} / {total} completed")

                self.after(0, update_done)

            self.after(0, lambda: self._on_download_finished(completed, total, target_out))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_item_progress(self, msg: str, overall_fraction: float):
        self._progress_bar.set(overall_fraction)
        self._progress_status_label.configure(text=msg)

    def _on_cancel(self):
        self._cancel_event.set()
        self._cancel_btn.configure(state="disabled")
        self._progress_status_label.configure(text="Cancelling download...")
        self._status_pill.set_state("CANCELLING", "waiting")

    def _on_download_finished(self, completed: int, total: int, out_dir: Path):
        self._is_working = False
        self._download_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._scan_btn.configure(state="normal")

        if self._cancel_event.is_set():
            self._status_pill.set_state("CANCELLED", "paused")
            self._progress_status_label.configure(text=f"Cancelled. {completed}/{total} saved.")
        else:
            self._status_pill.set_state("COMPLETED", "done")
            self._progress_bar.set(1.0)
            self._progress_status_label.configure(text=f"Finished! {completed}/{total} items saved.")
            self._progress_metrics_label.configure(text=f"{completed} / {total} finished successfully")
            self._log_colored("DONE", f"Complete: {completed} of {total} saved to {out_dir}")
            messagebox.showinfo(
                "Download Complete",
                f"Successfully downloaded {completed} of {total} items!\n\nFolder:\n{out_dir}",
            )

"""YouTube Downloader UI panel.

Interactive dashboard for fetching and downloading YouTube playlists, channels,
and individual videos with selective asset checkboxes (Thumbnails, Video, Audio, Subtitles).
Runs scraping and downloads on non-blocking background worker threads.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e


class YouTubeDownloaderPanel(ctk.CTkFrame):
    def __init__(self, parent, services=None):
        super().__init__(parent, fg_color=t.BG_COLOR)

        self._services = services
        self._items: list[e.VideoItem] = []
        self._item_checkboxes: list[tuple[ctk.CTkCheckBox, e.VideoItem]] = []
        self._item_status_labels: dict[int, ctk.CTkLabel] = {}
        self._current_result: e.FetchResult | None = None

        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._is_working = False

        # Default output folder: ~/Downloads/YouTube_Downloads
        self._default_out_dir = Path.home() / "Downloads" / "YouTube_Downloads"

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        self._header = c.PageHeader(
            self,
            title="YouTube Downloader",
            subtitle="Download thumbnails, full videos, extracted audio, or subtitles from playlists, channels, and videos.",
            icon=Icons.VIDEO,
            eyebrow="Media Importer",
        )
        self._header.grid(row=0, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))

        # Main scrollable canvas so the dashboard is fully accessible on all screen resolutions
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        self._scroll.grid_columnconfigure(0, weight=1)

        # Build Cards in exact user workflow order:
        # 1. Source Link & Scan
        # 2. Scanned Collection (Organized list to select videos)
        # 3. What to Download (Thumbnails, Video + Resolution, Audio, Subtitles)
        # 4. Destination & Naming
        # 5. Download Queue & Execution
        self._build_url_card()
        self._build_items_card()
        self._build_options_card()
        self._build_output_card()
        self._build_action_card()

    # -- Step 1: Link & Scan ---------------------------------------------------
    def _build_url_card(self):
        card = c.Card(self._scroll, "1. Source Link & Scan", icon=Icons.SEARCH)
        card.pack(fill="x", pady=(0, t.PAD_GRID))
        b = card.body

        # Input row
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1)

        self._url_entry = c.entry(
            row,
            placeholder_text="Paste YouTube Playlist, Channel, or Video link (e.g. https://www.youtube.com/playlist?list=...)",
            height=34,
        )
        self._url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._url_entry.bind("<Return>", lambda _: self._on_inspect())

        paste_btn = c.secondary_button(row, text="Paste", command=self._on_paste, width=70, height=34)
        paste_btn.grid(row=0, column=1, padx=(0, 8))

        self._inspect_btn = c.primary_button(
            row, text="Scan Link", command=self._on_inspect, width=120, height=34
        )
        self._inspect_btn.grid(row=0, column=2, padx=(0, 8))

        clear_btn = c.ghost_button(row, text="Clear", command=self._on_clear, width=60, height=34)
        clear_btn.grid(row=0, column=3)

        # Status & info pill row
        info_row = ctk.CTkFrame(b, fg_color="transparent")
        info_row.pack(fill="x", pady=(8, 0))

        self._status_pill = c.Pill(info_row, text="IDLE", state="idle")
        self._status_pill.pack(side="left")

        self._info_label = ctk.CTkLabel(
            info_row,
            text="Paste a playlist, channel, or video URL above and click 'Scan Link'.",
            text_color=t.TEXT_MUTED,
            font=t.font(11),
        )
        self._info_label.pack(side="left", padx=12)

    # -- Step 2: Scanned Collection (Clean Video List) -------------------------
    def _build_items_card(self):
        card = c.Card(self._scroll, "2. Scanned Collection (Select Videos)", icon=Icons.LAYERS)
        card.pack(fill="x", pady=(0, t.PAD_GRID))
        b = card.body

        # Control bar: count badge + select all / deselect all
        ctrl = ctk.CTkFrame(b, fg_color="transparent")
        ctrl.pack(fill="x", pady=(0, 8))

        self._items_count_label = ctk.CTkLabel(
            ctrl, text="No collection scanned yet.", font=t.font(11, bold=True), text_color=t.TEXT_MAIN
        )
        self._items_count_label.pack(side="left")

        self._deselect_all_btn = c.ghost_button(
            ctrl, text="Deselect All", command=self._on_deselect_all, width=90, height=26, font=t.font(11)
        )
        self._deselect_all_btn.pack(side="right")

        self._select_all_btn = c.ghost_button(
            ctrl, text="Select All", command=self._on_select_all, width=80, height=26, font=t.font(11)
        )
        self._select_all_btn.pack(side="right", padx=8)

        # Scrollable items frame
        self._items_container = ctk.CTkScrollableFrame(
            b, fg_color=t.BG_COLOR, height=200, corner_radius=t.RADIUS_CARD
        )
        self._items_container.pack(fill="x")
        self._items_container.grid_columnconfigure(1, weight=1)

        # Initial placeholder inside list
        self._empty_label = ctk.CTkLabel(
            self._items_container,
            text="No videos scanned yet. Paste a link and click 'Scan Link' to populate this list.",
            text_color=t.TEXT_MUTED,
            font=t.font(11),
        )
        self._empty_label.pack(pady=40)

    # -- Step 3: What to Download (Checkboxes & Resolution) --------------------
    def _build_options_card(self):
        card = c.Card(self._scroll, "3. What to Download (Select Assets)", icon=Icons.GEAR)
        card.pack(fill="x", pady=(0, t.PAD_GRID))
        b = card.body

        # 4 Asset Option Columns
        grid_frame = ctk.CTkFrame(b, fg_color=t.BG_COLOR, corner_radius=t.RADIUS_CARD)
        grid_frame.pack(fill="x", padx=2, pady=2, ipady=6)

        for col_idx in range(4):
            grid_frame.grid_columnconfigure(col_idx, weight=1)

        # Column 1: Thumbnails
        col1 = ctk.CTkFrame(grid_frame, fg_color="transparent")
        col1.grid(row=0, column=0, sticky="nsew", padx=10, pady=6)
        self._cb_thumb = ctk.CTkCheckBox(
            col1,
            text="Thumbnails",
            font=t.font(12, bold=True),
            fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled,
        )
        self._cb_thumb.select()
        self._cb_thumb.pack(anchor="w", pady=(0, 6))

        ctk.CTkLabel(col1, text="Thumbnail Quality:", font=t.font(10), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._opt_thumb_q = ctk.CTkOptionMenu(
            col1,
            values=["MaxRes (1080p/720p)", "Standard (640x480)", "High Quality (480x360)"],
            width=140,
            fg_color=t.CARD_BG,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._opt_thumb_q.set("MaxRes (1080p/720p)")
        self._opt_thumb_q.pack(fill="x", pady=(2, 0))

        # Column 2: Video (with resolution picker!)
        col2 = ctk.CTkFrame(grid_frame, fg_color="transparent")
        col2.grid(row=0, column=1, sticky="nsew", padx=10, pady=6)
        self._cb_video = ctk.CTkCheckBox(
            col2,
            text="Video (MP4)",
            font=t.font(12, bold=True),
            fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled,
        )
        self._cb_video.pack(anchor="w", pady=(0, 6))

        ctk.CTkLabel(col2, text="Video Resolution:", font=t.font(10), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._opt_video_q = ctk.CTkOptionMenu(
            col2,
            values=["Best Available", "1080p Full HD", "720p HD", "480p", "360p"],
            width=140,
            fg_color=t.CARD_BG,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._opt_video_q.set("Best Available")
        self._opt_video_q.configure(state="disabled")
        self._opt_video_q.pack(fill="x", pady=(2, 0))

        # Column 3: Audio Only
        col3 = ctk.CTkFrame(grid_frame, fg_color="transparent")
        col3.grid(row=0, column=2, sticky="nsew", padx=10, pady=6)
        self._cb_audio = ctk.CTkCheckBox(
            col3,
            text="Audio Only",
            font=t.font(12, bold=True),
            fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled,
        )
        self._cb_audio.pack(anchor="w", pady=(0, 6))

        ctk.CTkLabel(col3, text="Audio Format:", font=t.font(10), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._opt_audio_f = ctk.CTkOptionMenu(
            col3,
            values=["MP3", "M4A (AAC)", "WAV", "Original Best"],
            width=130,
            fg_color=t.CARD_BG,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._opt_audio_f.set("MP3")
        self._opt_audio_f.configure(state="disabled")
        self._opt_audio_f.pack(fill="x", pady=(2, 0))

        # Column 4: Subtitles
        col4 = ctk.CTkFrame(grid_frame, fg_color="transparent")
        col4.grid(row=0, column=3, sticky="nsew", padx=10, pady=6)
        self._cb_sub = ctk.CTkCheckBox(
            col4,
            text="Subtitles / CC",
            font=t.font(12, bold=True),
            fg_color=t.ACCENT_BLUE,
            command=self._on_checkbox_toggled,
        )
        self._cb_sub.pack(anchor="w", pady=(0, 6))

        ctk.CTkLabel(col4, text="Language:", font=t.font(10), text_color=t.TEXT_MUTED).pack(anchor="w")
        self._opt_sub_l = ctk.CTkOptionMenu(
            col4,
            values=["English (en)", "All Available"],
            width=130,
            fg_color=t.CARD_BG,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._opt_sub_l.set("English (en)")
        self._opt_sub_l.configure(state="disabled")
        self._opt_sub_l.pack(fill="x", pady=(2, 0))

    # -- Step 4: Destination & Naming ------------------------------------------
    def _build_output_card(self):
        card = c.Card(self._scroll, "4. Save Destination & Organization", icon=Icons.FOLDER)
        card.pack(fill="x", pady=(0, t.PAD_GRID))
        b = card.body

        # Folder row
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(0, weight=1)

        self._out_entry = c.entry(row, height=32)
        self._out_entry.insert(0, str(self._default_out_dir))
        self._out_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        browse_btn = c.secondary_button(row, text="Browse...", command=self._on_browse_out, width=90, height=32)
        browse_btn.grid(row=0, column=1, padx=(0, 8))

        open_btn = c.ghost_button(row, text="Open Folder", command=self._on_open_out, width=100, height=32)
        open_btn.grid(row=0, column=2)

        # Organization options
        opt_row = ctk.CTkFrame(b, fg_color="transparent")
        opt_row.pack(fill="x", pady=(8, 0))

        self._cb_subfolder = ctk.CTkCheckBox(
            opt_row,
            text="Save in dedicated playlist/channel subfolder",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._cb_subfolder.select()
        self._cb_subfolder.pack(side="left", padx=(0, 24))

        self._cb_prefix_num = ctk.CTkCheckBox(
            opt_row,
            text="Number files sequentially (e.g. '01 - Title')",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._cb_prefix_num.select()
        self._cb_prefix_num.pack(side="left")

    # -- Step 5: Action, Queue & Live Log --------------------------------------
    def _build_action_card(self):
        card = c.Card(self._scroll, "5. Download Queue & Execution", icon=Icons.PLAY)
        card.pack(fill="x")
        b = card.body

        # Actions row
        act_row = ctk.CTkFrame(b, fg_color="transparent")
        act_row.pack(fill="x", pady=(0, 8))

        self._download_btn = c.primary_button(
            act_row,
            text="Start Download",
            command=self._on_start_download,
            width=160,
            height=36,
            font=t.font(13, bold=True),
        )
        self._download_btn.pack(side="left", padx=(0, 10))

        self._cancel_btn = c.danger_button(
            act_row,
            text="Cancel",
            command=self._on_cancel,
            width=90,
            height=36,
            font=t.font(12, bold=True),
        )
        self._cancel_btn.pack(side="left")
        self._cancel_btn.configure(state="disabled")

        self._progress_status_label = ctk.CTkLabel(
            act_row, text="", font=t.font(11), text_color=t.TEXT_MUTED, anchor="w"
        )
        self._progress_status_label.pack(side="left", padx=16, fill="x", expand=True)

        # Progress bar
        self._progress_bar = ctk.CTkProgressBar(b, height=8, fg_color=t.CARD_BORDER, progress_color=t.ACCENT_BLUE)
        self._progress_bar.pack(fill="x", pady=(0, 8))
        self._progress_bar.set(0)

        # Live log textbox
        self._log_box = ctk.CTkTextbox(
            b, height=90, fg_color=t.BG_COLOR, text_color=t.TEXT_MUTED, font=t.mono(11), corner_radius=t.RADIUS_CARD
        )
        self._log_box.pack(fill="x")
        self._log("YouTube Downloader ready. Paste a link and click 'Scan Link' to begin.")

    # -- Event Handlers & Dynamic Option Toggles -------------------------------
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
        self._info_label.configure(text="Paste a playlist, channel, or video URL above and click 'Scan Link'.")

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
        # Enable / disable dropdowns based on their checkbox
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
            self._log("Notice: Select at least one asset type (Thumbnails, Video, Audio, or Subtitles).")

    def _on_select_all(self):
        for cb, _ in self._item_checkboxes:
            cb.select()

    def _on_deselect_all(self):
        for cb, _ in self._item_checkboxes:
            cb.deselect()

    def _clear_items(self):
        self._items = []
        self._item_checkboxes = []
        self._item_status_labels = {}
        for child in self._items_container.winfo_children():
            child.destroy()
        self._items_count_label.configure(text="No collection scanned yet.")

    def _log(self, message: str):
        self._log_box.insert("end", f"{message}\n")
        self._log_box.see("end")

    # -- Background Workers: Scan & Download -----------------------------------
    def _on_inspect(self):
        url = self._url_entry.get().strip()
        if not url:
            messagebox.showinfo("Missing URL", "Please paste or enter a YouTube URL first.")
            return

        if self._is_working:
            return

        self._is_working = True
        self._inspect_btn.configure(state="disabled")
        self._status_pill.set_state("SCANNING", "waiting")
        self._info_label.configure(text="Connecting to YouTube and scanning metadata...")
        self._log(f"Scanning: {url}")

        def worker():
            res = e.fetch_url_info(url)
            self.after(0, lambda: self._on_inspect_complete(res))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _on_inspect_complete(self, res: e.FetchResult):
        self._is_working = False
        self._inspect_btn.configure(state="normal")
        self._current_result = res

        if res.error:
            self._status_pill.set_state("ERROR", "error")
            self._info_label.configure(text=f"Error: {res.error}")
            self._log(f"Scan failed: {res.error}")
            return

        self._items = res.items
        self._status_pill.set_state("READY", "done")

        type_str = res.url_type.capitalize()
        summary = f"{type_str}: '{res.title}' ({len(res.items)} video{'s' if len(res.items) != 1 else ''})"
        self._info_label.configure(text=summary)
        self._items_count_label.configure(text=f"{len(res.items)} items in collection:")
        self._log(f"Scanned: {summary}")

        # Render items into container
        for child in self._items_container.winfo_children():
            child.destroy()
        self._item_checkboxes.clear()
        self._item_status_labels.clear()

        for item in self._items:
            row = ctk.CTkFrame(self._items_container, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=4)
            row.grid_columnconfigure(1, weight=1)

            cb = ctk.CTkCheckBox(row, text=f"#{item.index:02d}", width=50, font=t.font(11, bold=True))
            cb.select()
            cb.grid(row=0, column=0, sticky="w", padx=(4, 8))

            title_lbl = ctk.CTkLabel(
                row,
                text=item.title,
                font=t.font(11),
                text_color=t.TEXT_MAIN,
                anchor="w",
            )
            title_lbl.grid(row=0, column=1, sticky="ew", padx=4)

            dur_lbl = ctk.CTkLabel(
                row,
                text=item.duration_str,
                font=t.mono(10),
                text_color=t.TEXT_MUTED,
                width=50,
            )
            dur_lbl.grid(row=0, column=2, padx=8)

            stat_lbl = ctk.CTkLabel(
                row,
                text="Queued",
                font=t.font(10),
                text_color=t.TEXT_MUTED,
                width=80,
                anchor="e",
            )
            stat_lbl.grid(row=0, column=3, padx=(0, 4))

            self._item_checkboxes.append((cb, item))
            self._item_status_labels[item.index] = stat_lbl

    def _on_start_download(self):
        if not self._items:
            messagebox.showinfo("No Items", "Please scan a collection first.")
            return

        selected_items = [item for cb, item in self._item_checkboxes if cb.get()]
        if not selected_items:
            messagebox.showinfo("No Selection", "Please check at least one video to download.")
            return

        # Verify download options
        dl_thumb = bool(self._cb_thumb.get())
        dl_video = bool(self._cb_video.get())
        dl_audio = bool(self._cb_audio.get())
        dl_sub = bool(self._cb_sub.get())

        if not (dl_thumb or dl_video or dl_audio or dl_sub):
            messagebox.showwarning(
                "No Asset Selected",
                "Please select at least one asset type:\n- Thumbnails\n- Video\n- Audio\n- Subtitles",
            )
            return

        # Determine target output folder
        base_out = Path(self._out_entry.get().strip() or self._default_out_dir)
        if self._cb_subfolder.get() and self._current_result and self._current_result.title:
            folder_name = e.sanitize_filename(self._current_result.title)
            target_out = base_out / folder_name
        else:
            target_out = base_out

        # Collect quality options
        thumb_q_map = {
            "MaxRes (1080p/720p)": "max",
            "Standard (640x480)": "sd",
            "High Quality (480x360)": "hq",
        }
        video_q_map = {
            "Best Available": "best",
            "1080p Full HD": "1080p",
            "720p HD": "720p",
            "480p": "480p",
            "360p": "360p",
        }
        audio_f_map = {
            "MP3": "mp3",
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
        self._inspect_btn.configure(state="disabled")
        self._status_pill.set_state("DOWNLOADING", "running")
        self._progress_bar.set(0)

        self._log(f"--- Starting Download: {len(selected_items)} videos -> {target_out} ---")

        def worker():
            total = len(selected_items)
            completed = 0
            for idx, item in enumerate(selected_items, 1):
                if self._cancel_event.is_set():
                    self.after(0, lambda: self._log("Download cancelled by user."))
                    break

                # Update UI for current item
                def update_start(it=item, i=idx):
                    if it.index in self._item_status_labels:
                        self._item_status_labels[it.index].configure(
                            text="Downloading...", text_color=t.ACCENT_SOFT
                        )
                    self._progress_status_label.configure(
                        text=f"[{i}/{total}] {it.title[:45]}..."
                    )

                self.after(0, update_start)

                # Progress callback inside download
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

                # Update UI for item completion
                def update_done(it=item, success=ok, m=msg, c_idx=completed):
                    if it.index in self._item_status_labels:
                        if success:
                            self._item_status_labels[it.index].configure(
                                text="Done ✓", text_color=t.STATE["done"][1]
                            )
                        else:
                            self._item_status_labels[it.index].configure(
                                text="Error ✗", text_color=t.STATE["error"][1]
                            )
                    self._progress_bar.set(c_idx / total)
                    self._log(f"[{it.index:02d}] {it.title[:40]} -> {m}")

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
        self._progress_status_label.configure(text="Cancelling...")
        self._status_pill.set_state("CANCELLING", "waiting")

    def _on_download_finished(self, completed: int, total: int, out_dir: Path):
        self._is_working = False
        self._download_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._inspect_btn.configure(state="normal")

        if self._cancel_event.is_set():
            self._status_pill.set_state("CANCELLED", "paused")
            self._progress_status_label.configure(text=f"Cancelled. {completed}/{total} saved.")
        else:
            self._status_pill.set_state("COMPLETED", "done")
            self._progress_bar.set(1.0)
            self._progress_status_label.configure(text=f"Finished! {completed}/{total} completed.")
            self._log(f"=== Complete: {completed} of {total} items saved to {out_dir} ===")
            messagebox.showinfo(
                "Download Complete",
                f"Successfully downloaded {completed} of {total} items!\n\nSaved in:\n{out_dir}",
            )

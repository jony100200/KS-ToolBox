"""Sprite Viewer — the tool's UI. Thin over engine.py.

A *custom* panel (not BaseBatchPanel): this is an interactive VIEWER, not a batch
job. Load one source (sheet / frame folder / animated GIF-WebP-APNG), slice it,
then play / step / scrub the frames on a CTkImage canvas. Frames are small so
rendering runs on the UI thread; the play loop is a cancellable `self.after`
timer (torn down on destroy). Slicing/detection is the only potentially heavy
work and it runs once, synchronously, on Load.

No slicing math lives here — that's engine.py. Nothing is persisted unless you
explicitly Export.
"""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e

_DISPLAY_MAX = (420, 300)     # canvas fit box (w, h)
_MODES = ("Animation / Folder", "Sheet grid (rows x cols)",
          "Cell size (w x h)", "Auto-detect (alpha)")


class SpriteViewerPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=t.BG_COLOR)

        # --- viewer state -----------------------------------------------------
        self._source_img = None          # PIL sheet (grid/cell/auto modes) or None
        self._frames: list = []          # list[PIL.Image] currently loaded
        self._boxes: list = []           # slice boxes on the sheet (PIL convention)
        self._kind: str = ""             # engine kind tag for metadata/export
        self._index: int = 0
        self._playing: bool = False
        self._play_job = None            # self.after handle (cancellable)
        self._syncing: bool = False      # guards slider<->index feedback loop
        self._ctk_img = None             # keep a ref so it isn't GC'd
        self._checker_cache: dict = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)      # viewer row stretches

        self._build_source_card()
        self._build_viewer_card()
        self._build_transport()

        self._set_status("Load a sprite sheet, animation, or frame folder.", "idle")

    # -- cards -----------------------------------------------------------------

    def _build_source_card(self):
        card = c.Card(self, "Source", icon=Icons.FOLDER)
        card.grid(row=0, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body
        body.grid_columnconfigure(1, weight=1)

        # path row
        ctk.CTkLabel(body, text="Path:", text_color=t.TEXT_MAIN, font=t.font(12)
                     ).grid(row=0, column=0, padx=(0, 10), pady=(0, 10), sticky="w")
        self._path = c.entry(body)
        self._path.grid(row=0, column=1, padx=(0, 10), pady=(0, 10), sticky="ew")
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.grid(row=0, column=2, pady=(0, 10))
        c.secondary_button(btns, "File", self._browse_file, width=70).pack(side="left")
        c.secondary_button(btns, "Folder", self._browse_folder, width=80).pack(side="left", padx=(8, 0))

        # mode + params row
        opts = ctk.CTkFrame(body, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=3, sticky="w")
        ctk.CTkLabel(opts, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)
                     ).pack(side="left", padx=(0, 8))
        self._mode = ctk.CTkOptionMenu(opts, values=list(_MODES), width=210,
                                       command=self._on_mode_change,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                       button_hover_color=t.NEUTRAL_HOVER)
        self._mode.set(_MODES[0])
        self._mode.pack(side="left", padx=(0, 16))

        # grid params (rows x cols)  |  cell params (w x h)  |  auto param (alpha)
        self._p_rows = self._small_entry(opts, "Rows", "4")
        self._p_cols = self._small_entry(opts, "Cols", "4")
        self._p_cw = self._small_entry(opts, "Cell W", "32")
        self._p_ch = self._small_entry(opts, "Cell H", "32")
        self._p_alpha = self._small_entry(opts, "Alpha thr.", "32")

        c.primary_button(body, "Load", self._load, width=110
                         ).grid(row=1, column=2, sticky="e")

        self._on_mode_change(_MODES[0])     # show the right params for the default mode

    def _small_entry(self, parent, label: str, default: str):
        """A labeled narrow numeric entry (returned as a (frame, entry) holder)."""
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(holder, text=label, text_color=t.TEXT_MUTED, font=t.font(11)
                     ).pack(side="left", padx=(0, 4))
        ent = c.entry(holder, width=54)
        ent.insert(0, default)
        ent.pack(side="left", padx=(0, 12))
        holder._entry = ent          # stash the entry for reads
        return holder

    def _build_viewer_card(self):
        card = c.Card(self, "Viewer", icon=Icons.PLAY)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # the canvas is a CTkLabel that shows a CTkImage
        self._canvas = ctk.CTkLabel(body, text="(no source loaded)", text_color=t.TEXT_MUTED,
                                    fg_color=t.BG_COLOR, corner_radius=t.RADIUS_CARD,
                                    width=_DISPLAY_MAX[0], height=_DISPLAY_MAX[1])
        self._canvas.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        toggles = ctk.CTkFrame(body, fg_color="transparent")
        toggles.grid(row=1, column=0, sticky="w")
        self._chk_checker = ctk.CTkCheckBox(toggles, text="Alpha checkerboard", font=t.font(11),
                                            fg_color=t.ACCENT_BLUE, command=self._render)
        self._chk_checker.select(); self._chk_checker.pack(side="left", padx=(0, 16))
        self._chk_grid = ctk.CTkCheckBox(toggles, text="Slice-grid overlay", font=t.font(11),
                                         fg_color=t.ACCENT_BLUE, command=self._render)
        self._chk_grid.pack(side="left")

    def _build_transport(self):
        bar = c.Card(self, "Transport", icon=Icons.GEAR)
        bar.grid(row=2, column=0, sticky="ew", padx=t.PAD_GRID, pady=(0, t.PAD_GRID))
        body = bar.body
        body.grid_columnconfigure(4, weight=1)

        self._play_btn = c.primary_button(body, "▶ Play", self._toggle_play, width=90)
        self._play_btn.grid(row=0, column=0, pady=(0, 8))
        c.secondary_button(body, "◀ Prev", lambda: self._step(-1), width=80
                           ).grid(row=0, column=1, padx=(8, 0), pady=(0, 8))
        c.secondary_button(body, "Next ▶", lambda: self._step(1), width=80
                           ).grid(row=0, column=2, padx=(8, 0), pady=(0, 8))

        fpsbox = ctk.CTkFrame(body, fg_color="transparent")
        fpsbox.grid(row=0, column=3, padx=(16, 0), pady=(0, 8))
        ctk.CTkLabel(fpsbox, text="FPS", text_color=t.TEXT_MUTED, font=t.font(11)
                     ).pack(side="left", padx=(0, 4))
        self._fps = c.entry(fpsbox, width=48)
        self._fps.insert(0, "12"); self._fps.pack(side="left")

        self._frame_lbl = ctk.CTkLabel(body, text="0 / 0", text_color=t.TEXT_MAIN, font=t.mono(12))
        self._frame_lbl.grid(row=0, column=4, sticky="e", padx=(16, 0), pady=(0, 8))

        # scrub slider (full width, second row)
        self._slider = ctk.CTkSlider(body, from_=0, to=1, number_of_steps=1,
                                     command=self._on_scrub, progress_color=t.ACCENT_BLUE,
                                     button_color=t.ACCENT_BLUE, button_hover_color=t.ACCENT_HOVER)
        self._slider.set(0)
        self._slider.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(0, 8))
        self._slider.configure(state="disabled")

        # export row
        exp = ctk.CTkFrame(body, fg_color="transparent")
        exp.grid(row=2, column=0, columnspan=5, sticky="w")
        c.ghost_button(exp, "Export GIF", self._export_gif, width=110).pack(side="left")
        c.ghost_button(exp, "Export slice JSON", self._export_json, width=150
                       ).pack(side="left", padx=(8, 0))
        self._status = c.Pill(exp, "IDLE", "idle"); self._status.pack(side="left", padx=(16, 0))

    # -- mode / browse ---------------------------------------------------------

    def _on_mode_change(self, mode: str):
        for h in (self._p_rows, self._p_cols, self._p_cw, self._p_ch, self._p_alpha):
            h.pack_forget()
        if mode == _MODES[1]:            # sheet grid
            self._p_rows.pack(side="left"); self._p_cols.pack(side="left")
        elif mode == _MODES[2]:          # cell size
            self._p_cw.pack(side="left"); self._p_ch.pack(side="left")
        elif mode == _MODES[3]:          # auto-detect
            self._p_alpha.pack(side="left")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select an image, sprite sheet, or animation", parent=self,
            filetypes=[("Images & animations", "*.png *.gif *.webp *.apng *.jpg *.jpeg *.bmp *.tif *.tiff"),
                       ("All files", "*.*")])
        if path:
            self._path.delete(0, "end"); self._path.insert(0, path)

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select a frame folder", parent=self)
        if path:
            self._path.delete(0, "end"); self._path.insert(0, path)
            self._mode.set(_MODES[0]); self._on_mode_change(_MODES[0])

    # -- load ------------------------------------------------------------------

    def _read_int(self, holder, minimum: int = 1) -> int | None:
        try:
            v = int(holder._entry.get().strip())
            return v if v >= minimum else None
        except ValueError:
            return None

    def _load(self):
        self._pause()
        path = self._path.get().strip()
        if not path:
            self._set_status("Choose a file or folder first.", "error"); return
        p = Path(path)
        mode = self._mode.get()
        self._source_img = None
        self._boxes = []

        try:
            if mode == _MODES[0]:                     # animation / folder / still
                if p.is_dir():
                    res = e.load_folder(p); self._kind = "folder"
                elif p.is_file():
                    res = e.load_frames(p)
                    self._kind = "animation" if res.get("data") and len(res["data"]) > 1 else "image"
                else:
                    self._set_status(f"Path not found: {p}", "error"); return
                if res["error"]:
                    self._set_status(res["details"], "error"); return
                frames = res["data"]
            else:                                     # sheet modes need one image file
                if not p.is_file():
                    self._set_status("Sheet/auto modes need a single image file.", "error"); return
                load = e.load_frames(p)
                if load["error"]:
                    self._set_status(load["details"], "error"); return
                self._source_img = load["data"][0]
                w, h = self._source_img.size
                if mode == _MODES[1]:                 # grid rows x cols
                    rows, cols = self._read_int(self._p_rows), self._read_int(self._p_cols)
                    if not rows or not cols:
                        self._set_status("Rows and Cols must be positive integers.", "error"); return
                    self._boxes = e.grid_boxes(w, h, rows, cols); self._kind = "sheet-grid"
                elif mode == _MODES[2]:               # cell w x h
                    cw, ch = self._read_int(self._p_cw), self._read_int(self._p_ch)
                    if not cw or not ch:
                        self._set_status("Cell W and Cell H must be positive integers.", "error"); return
                    self._boxes = e.cell_boxes(w, h, cw, ch); self._kind = "sheet-cell"
                else:                                 # auto-detect
                    thr = self._read_int(self._p_alpha, minimum=0)
                    if thr is None:
                        self._set_status("Alpha threshold must be 0-255.", "error"); return
                    self._boxes = e.detect_sprites(self._source_img, alpha_thresh=thr)
                    self._kind = "auto"
                if not self._boxes:
                    self._set_status("No frames produced — check the mode parameters.", "error"); return
                frames = e.crop_boxes(self._source_img, self._boxes)
        except Exception as ex:                       # PIL can raise many types
            self._set_status(f"Load failed: {ex}", "error"); return

        if not frames:
            self._set_status("No frames were loaded.", "error"); return

        self._frames = frames
        self._index = 0
        n = len(frames)
        self._syncing = True
        self._slider.configure(state=("normal" if n > 1 else "disabled"),
                               number_of_steps=max(1, n - 1), from_=0, to=max(1, n - 1))
        self._slider.set(0)
        self._syncing = False
        self._render()
        fw, fh = frames[0].size
        self._set_status(f"Loaded {n} frame(s) · {fw}x{fh} · {self._kind}", "done")

    # -- rendering (UI thread) -------------------------------------------------

    def _checkerboard(self, size, cell: int = 8):
        from PIL import Image
        key = (size, cell)
        if key in self._checker_cache:
            return self._checker_cache[key]
        w, h = size
        light, dark = (90, 96, 110, 255), (58, 64, 78, 255)
        board = Image.new("RGBA", (w, h), light)
        px = board.load()
        for y in range(h):
            for x in range(w):
                if ((x // cell) + (y // cell)) % 2:
                    px[x, y] = dark
        self._checker_cache[key] = board
        return board

    def _render(self):
        if not self._frames:
            return
        from PIL import Image, ImageDraw

        show_grid = bool(self._chk_grid.get()) and self._source_img is not None and self._boxes

        if show_grid:
            base = self._source_img.convert("RGBA").copy()
            draw = ImageDraw.Draw(base)
            for i, (l, top, r, b) in enumerate(self._boxes):
                color = (37, 99, 235, 255) if i != self._index else (52, 211, 153, 255)
                draw.rectangle([l, top, r - 1, b - 1], outline=color,
                               width=2 if i == self._index else 1)
            frame = base
        else:
            frame = self._frames[self._index].convert("RGBA")

        # fit into the display box, preserving aspect (NEAREST = pixel-art crisp)
        fw, fh = frame.size
        scale = min(_DISPLAY_MAX[0] / max(1, fw), _DISPLAY_MAX[1] / max(1, fh))
        disp_w, disp_h = max(1, int(fw * scale)), max(1, int(fh * scale))
        disp = frame.resize((disp_w, disp_h), Image.Resampling.NEAREST)

        if bool(self._chk_checker.get()):
            board = self._checkerboard((disp_w, disp_h)).copy()
            board.alpha_composite(disp)
            disp = board

        self._ctk_img = ctk.CTkImage(light_image=disp, dark_image=disp, size=(disp_w, disp_h))
        self._canvas.configure(image=self._ctk_img, text="")
        self._frame_lbl.configure(text=f"{self._index + 1} / {len(self._frames)}")
        if not self._syncing and len(self._frames) > 1:
            self._syncing = True
            self._slider.set(self._index)
            self._syncing = False

    # -- transport -------------------------------------------------------------

    def _get_fps(self) -> int:
        try:
            return max(1, min(120, int(self._fps.get().strip())))
        except ValueError:
            return 12

    def _step(self, delta: int):
        if not self._frames:
            return
        self._pause()
        self._index = (self._index + delta) % len(self._frames)
        self._render()

    def _on_scrub(self, value):
        if self._syncing or not self._frames:
            return
        self._pause()
        idx = int(round(float(value)))
        idx = max(0, min(len(self._frames) - 1, idx))
        if idx != self._index:
            self._index = idx
            self._render()

    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if len(self._frames) < 2:
            return
        self._playing = True
        self._play_btn.configure(text="⏸ Pause")
        self._set_status(f"Playing @ {self._get_fps()} fps", "running")
        self._tick()

    def _tick(self):
        if not self._playing:
            return
        self._index = (self._index + 1) % len(self._frames)
        self._render()
        delay = max(8, int(1000 / self._get_fps()))
        self._play_job = self.after(delay, self._tick)

    def _pause(self):
        if self._play_job is not None:
            try:
                self.after_cancel(self._play_job)
            except Exception:
                pass
            self._play_job = None
        if self._playing:
            self._playing = False
            self._set_status(f"Paused at {self._index + 1}/{len(self._frames)}", "paused")
        self._play_btn.configure(text="▶ Play")

    # -- export ----------------------------------------------------------------

    def _export_gif(self):
        if not self._frames:
            self._set_status("Nothing to export — load a source first.", "error"); return
        self._pause()
        dst = filedialog.asksaveasfilename(
            title="Export animated GIF", parent=self, defaultextension=".gif",
            filetypes=[("Animated GIF", "*.gif")])
        if not dst:
            return
        res = e.export_gif(self._frames, dst, fps=self._get_fps())
        if res["error"]:
            self._set_status(res["details"], "error")
            messagebox.showerror("Export failed", res["details"], parent=self)
        else:
            self._set_status(res["details"], "done")

    def _export_json(self):
        if not self._frames:
            self._set_status("Nothing to export — load a source first.", "error"); return
        self._pause()
        dst = filedialog.asksaveasfilename(
            title="Export slice metadata", parent=self, defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not dst:
            return
        meta = e.describe(self._frames, self._path.get().strip(), self._kind,
                          fps=self._get_fps(), boxes=self._boxes)
        res = e.export_meta_json(meta, dst)
        if res["error"]:
            self._set_status(res["details"], "error")
            messagebox.showerror("Export failed", res["details"], parent=self)
        else:
            self._set_status(res["details"], "done")

    # -- status / lifecycle ----------------------------------------------------

    def _set_status(self, text: str, state: str):
        short = text if len(text) <= 60 else text[:57] + "..."
        self._status.set_state(short.upper(), state)

    def destroy(self):
        # Tear the play timer down so no stray `after` fires post-teardown.
        self._playing = False
        if self._play_job is not None:
            try:
                self.after_cancel(self._play_job)
            except Exception:
                pass
            self._play_job = None
        super().destroy()

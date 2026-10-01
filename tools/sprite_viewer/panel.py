"""Sprite Viewer — the tool's UI. Thin over engine.py.

A *custom* panel (not BaseBatchPanel): this is an interactive VIEWER, not a batch
job. Load one source (sheet / frame folder / animated GIF-WebP-APNG), slice it,
then play / step / scrub the frames on a CTkImage canvas. Bounded rendering runs
on the UI thread; decoding, slicing, detection, and export run on a cancellable
worker so the Tk event loop stays responsive. The play loop is a cancellable
`self.after` timer (torn down on destroy).

No slicing math lives here — that's engine.py. Nothing is persisted unless you
explicitly Export.
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import OrderedDict
from pathlib import Path
from tkinter import TclError, filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e

_DISPLAY_MAX = (420, 300)     # canvas fit box (w, h)
_CHECKER_CACHE_LIMIT = 8
_MODES = ("Animation / Folder", "Sheet grid (rows x cols)",
          "Cell size (w x h)", "Auto-detect (alpha)")
_ENGINE_MODES = {
    _MODES[0]: "animation",
    _MODES[1]: "grid",
    _MODES[2]: "cell",
    _MODES[3]: "auto",
}

_LOG = logging.getLogger(__name__)


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
        self._checker_cache: OrderedDict = OrderedDict()
        self._worker: threading.Thread | None = None
        self._worker_results: queue.SimpleQueue = queue.SimpleQueue()
        self._result_lock = threading.Lock()
        self._poll_job = None
        self._cancel = threading.Event()
        self._operation = ""
        self._generation = 0
        self._destroying = False
        self._release_deferred = False

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

        self._load_btn = c.primary_button(body, "Load", self._load, width=110)
        self._load_btn.grid(row=1, column=2, sticky="e")

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
        # Do not shadow CTkFrame._canvas: CustomTkinter owns that internal name
        # and needs it for redraws after resize/theme events.
        self._preview_label = ctk.CTkLabel(
            body,
            text="(no source loaded)",
            text_color=t.TEXT_MUTED,
            fg_color=t.BG_COLOR,
            corner_radius=t.RADIUS_CARD,
            width=_DISPLAY_MAX[0],
            height=_DISPLAY_MAX[1],
        )
        self._preview_label.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

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
        self._gif_btn = c.ghost_button(
            exp, "Export GIF", self._export_gif, width=110
        )
        self._gif_btn.pack(side="left")
        self._json_btn = c.ghost_button(
            exp, "Export slice JSON", self._export_json, width=150
        )
        self._json_btn.pack(side="left", padx=(8, 0))
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
        if self._worker is not None and self._worker.is_alive():
            self._cancel.set()
            self._load_btn.configure(state="disabled", text="Cancelling…")
            self._set_status(f"Cancelling {self._operation}…", "waiting")
            return
        self._pause()
        path = self._path.get().strip()
        if not path:
            self._set_status("Choose a file or folder first.", "error"); return
        p = Path(path)
        mode = self._mode.get()
        if not p.exists():
            self._set_status(f"Path not found: {p}", "error"); return
        params: dict[str, int] = {}
        if mode == _MODES[1]:
            rows, cols = self._read_int(self._p_rows), self._read_int(self._p_cols)
            if not rows or not cols:
                self._set_status("Rows and Cols must be positive integers.", "error"); return
            params.update(rows=rows, cols=cols)
        elif mode == _MODES[2]:
            cw, ch = self._read_int(self._p_cw), self._read_int(self._p_ch)
            if not cw or not ch:
                self._set_status("Cell W and Cell H must be positive integers.", "error"); return
            params.update(cell_width=cw, cell_height=ch)
        elif mode == _MODES[3]:
            threshold = self._read_int(self._p_alpha, minimum=0)
            if threshold is None or threshold > 255:
                self._set_status("Alpha threshold must be 0–255.", "error"); return
            params["alpha_threshold"] = threshold

        self._start_worker(
            "load",
            self._load_worker,
            p,
            _ENGINE_MODES[mode],
            params,
        )

    def _start_worker(self, operation: str, target, *args):
        self._cancel.clear()
        self._generation += 1
        generation = self._generation
        self._operation = operation
        self._load_btn.configure(text=f"Cancel {operation}", state="normal")
        self._gif_btn.configure(state="disabled")
        self._json_btn.configure(state="disabled")
        self._set_status(f"{operation.capitalize()} running…", "running")
        self._worker = threading.Thread(
            target=target,
            args=(generation, *args),
            daemon=True,
            name=f"sprite-viewer-{operation}",
        )
        self._worker.start()
        if self._poll_job is None:
            self._poll_job = self.after(20, self._poll_worker)

    def _load_worker(self, generation: int, path: Path, mode: str, params: dict):
        try:
            result = e.prepare_source(
                path,
                mode,
                cancelled=self._cancel.is_set,
                **params,
            )
        except Exception as ex:  # noqa: BLE001 - visible worker boundary
            result = self._worker_failure("load", ex)
        self._post_worker_result(generation, self._finish_load, result)

    @staticmethod
    def _worker_failure(operation: str, ex: Exception) -> dict:
        return {
            "error": True,
            "error_type": "worker.failed",
            "retryable": False,
            "degraded": False,
            "details": f"{operation} worker failed: {type(ex).__name__}: {ex}",
            "data": None,
        }

    def _post_worker_result(self, generation: int, callback, result: dict):
        # Worker threads never call Tk. The UI-owned poller delivers results.
        with self._result_lock:
            if self._destroying:
                self._release_result(result)
                if self._release_deferred:
                    self._release_viewer_state()
                    self._release_deferred = False
                return
            self._worker_results.put((generation, callback, result))

    def _poll_worker(self):
        self._poll_job = None
        while True:
            try:
                generation, callback, result = self._worker_results.get_nowait()
            except queue.Empty:
                break
            callback(generation, result)
        if (
            not self._destroying
            and self._worker is not None
            and self._worker.is_alive()
        ):
            self._poll_job = self.after(20, self._poll_worker)

    @staticmethod
    def _release_result(result: dict) -> None:
        loaded = result.get("data")
        if not isinstance(loaded, e.LoadedSpriteSource):
            return
        for frame in loaded.frames:
            frame.close()
        if loaded.source_image is not None:
            loaded.source_image.close()

    def _finish_load(self, generation: int, result: dict):
        if generation != self._generation:
            self._release_result(result)
            return
        self._finish_worker_controls()
        if result["error"]:
            state = "waiting" if result["error_type"] == "operation.cancelled" else "error"
            self._set_status(result["details"], state)
            return

        loaded = result["data"]
        self._release_viewer_state()
        self._frames = loaded.frames
        self._source_img = loaded.source_image
        self._boxes = loaded.boxes
        self._kind = loaded.kind
        self._index = 0
        n = len(self._frames)
        self._syncing = True
        self._slider.configure(state=("normal" if n > 1 else "disabled"),
                               number_of_steps=max(1, n - 1), from_=0, to=max(1, n - 1))
        self._slider.set(0)
        self._syncing = False
        self._render()
        fw, fh = self._frames[0].size
        self._set_status(f"Loaded {n} frame(s) · {fw}x{fh} · {self._kind}", "done")

    # -- rendering (UI thread) -------------------------------------------------

    def _checkerboard(self, size, cell: int = 8):
        from PIL import Image
        key = (size, cell)
        if key in self._checker_cache:
            self._checker_cache.move_to_end(key)
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
        if len(self._checker_cache) > _CHECKER_CACHE_LIMIT:
            _, expired = self._checker_cache.popitem(last=False)
            expired.close()
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
        self._preview_label.configure(image=self._ctk_img, text="")
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
            except TclError as ex:
                _LOG.warning("sprite play timer was already unavailable: %s", ex)
            self._play_job = None
        if self._playing:
            self._playing = False
            self._set_status(f"Paused at {self._index + 1}/{len(self._frames)}", "paused")
        self._play_btn.configure(text="▶ Play")

    # -- export ----------------------------------------------------------------

    def _export_gif(self):
        if self._worker is not None and self._worker.is_alive():
            self._set_status(f"{self._operation.capitalize()} already running.", "waiting")
            return
        if not self._frames:
            self._set_status("Nothing to export — load a source first.", "error"); return
        self._pause()
        dst = filedialog.asksaveasfilename(
            title="Export animated GIF", parent=self, defaultextension=".gif",
            filetypes=[("Animated GIF", "*.gif")])
        if not dst:
            return
        if not self._confirm_replace(dst):
            return
        self._start_worker(
            "GIF export",
            self._export_worker,
            e.export_gif,
            list(self._frames),
            dst,
            self._get_fps(),
        )

    def _export_json(self):
        if self._worker is not None and self._worker.is_alive():
            self._set_status(f"{self._operation.capitalize()} already running.", "waiting")
            return
        if not self._frames:
            self._set_status("Nothing to export — load a source first.", "error"); return
        self._pause()
        dst = filedialog.asksaveasfilename(
            title="Export slice metadata", parent=self, defaultextension=".json",
            filetypes=[("JSON", "*.json")])
        if not dst:
            return
        if not self._confirm_replace(dst):
            return
        meta = e.describe(self._frames, self._path.get().strip(), self._kind,
                          fps=self._get_fps(), boxes=self._boxes)
        self._start_worker(
            "JSON export",
            self._export_worker,
            e.export_meta_json,
            meta,
            dst,
        )

    def _export_worker(self, generation: int, exporter, value, dst, fps=None):
        kwargs = {"cancelled": self._cancel.is_set}
        if fps is not None:
            kwargs["fps"] = fps
        try:
            result = exporter(value, dst, **kwargs)
        except Exception as ex:  # noqa: BLE001 - visible worker boundary
            result = self._worker_failure("export", ex)
        self._post_worker_result(generation, self._finish_export, result)

    def _confirm_replace(self, destination: str) -> bool:
        path = Path(destination)
        if not path.exists():
            return True
        return messagebox.askyesno(
            "Replace existing export?",
            f"{path.name} already exists.\n\nReplace it after validation?",
            parent=self,
        )

    def _finish_export(self, generation: int, result: dict):
        if generation != self._generation:
            return
        self._finish_worker_controls()
        if result["error"]:
            state = "waiting" if result["error_type"] == "operation.cancelled" else "error"
            self._set_status(result["details"], state)
            if result["error_type"] != "operation.cancelled":
                messagebox.showerror("Export failed", result["details"], parent=self)
        else:
            self._set_status(result["details"], "done")

    def _finish_worker_controls(self):
        self._worker = None
        self._operation = ""
        self._load_btn.configure(text="Load", state="normal")
        self._gif_btn.configure(state="normal")
        self._json_btn.configure(state="normal")

    def _release_viewer_state(self):
        for frame in self._frames:
            frame.close()
        self._frames = []
        if self._source_img is not None:
            self._source_img.close()
            self._source_img = None
        self._boxes = []
        for board in self._checker_cache.values():
            board.close()
        self._checker_cache.clear()

    # -- status / lifecycle ----------------------------------------------------

    def _set_status(self, text: str, state: str):
        short = text if len(text) <= 60 else text[:57] + "..."
        self._status.set_state(short.upper(), state)

    def destroy(self):
        # Tear the play timer down so no stray `after` fires post-teardown.
        completed_result_waiting = False
        with self._result_lock:
            self._destroying = True
            while True:
                try:
                    _, _, result = self._worker_results.get_nowait()
                except queue.Empty:
                    break
                completed_result_waiting = True
                self._release_result(result)
            self._release_deferred = bool(
                self._worker is not None
                and self._worker.is_alive()
                and not completed_result_waiting
            )
        self._cancel.set()
        self._playing = False
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except TclError as ex:
                _LOG.warning("sprite worker poll teardown was already complete: %s", ex)
            self._poll_job = None
        if self._play_job is not None:
            try:
                self.after_cancel(self._play_job)
            except TclError as ex:
                _LOG.warning("sprite play timer teardown was already complete: %s", ex)
            self._play_job = None
        if self._release_deferred:
            # GIF encoding may still be inside Pillow. Its worker will release
            # viewer-owned frames after it reaches the next cancellation boundary.
            _LOG.debug("deferring sprite frame release until worker cancellation")
        else:
            self._release_viewer_state()
        super().destroy()

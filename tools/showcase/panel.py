"""Showcase — the tool's UI. Thin over engine.py via BaseBatchPanel.

Collects images + a presentation mode + its options, runs on a worker thread,
streams results back via after(). No compositing math here — that all lives in
engine.py. The visible sub-options relabel per mode (contact / hero /
before_after) like Image Rescale's _on_mode_change.
"""
from __future__ import annotations

import csv
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class ShowcasePanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Render"

    def __init__(self, parent):
        super().__init__(parent)
        self._on_mode_change(self._mode.get())

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # common row: mode + cell + padding (apply to every mode)
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._mode = ctk.CTkOptionMenu(row, values=list(e.MODES), width=150, command=self._on_mode_change,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._mode.set("contact"); self._mode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Cell size (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._cell = c.entry(row, width=90); self._cell.insert(0, "256")
        self._cell.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Padding (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._padding = c.entry(row, width=90); self._padding.insert(0, "16")
        self._padding.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # per-mode sub-options live in one box; only the active mode's frame shows
        self._mode_box = ctk.CTkFrame(b, fg_color="transparent"); self._mode_box.pack(fill="x", pady=(10, 0))
        self._build_contact_frame()
        self._build_hero_frame()
        self._build_ba_frame()

        self._build_output_row(b, "Output folder (blank = ./showcase beside sources)")

        # shared toggle
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.pack(side="left", padx=20)

        self._build_run_row(b)

    def _build_contact_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        row = ctk.CTkFrame(f, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Columns", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._cols = c.entry(row, width=70); self._cols.insert(0, "4")
        self._cols.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Title header (optional)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._title = c.entry(row, width=240)
        self._title.grid(row=1, column=1, sticky="w", pady=(2, 0))
        self._labels = ctk.CTkCheckBox(f, text="Filename labels under thumbnails", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._labels.select(); self._labels.pack(anchor="w", pady=(8, 0))
        self._contact_frame = f

    def _build_hero_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        row = ctk.CTkFrame(f, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Backdrop", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._bg_style = ctk.CTkOptionMenu(row, values=list(e.BG_STYLES), width=130,
                                           fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._bg_style.set("solid"); self._bg_style.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Backdrop colour (hex)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._bg_color = c.entry(row, width=110); self._bg_color.insert(0, "#111827")
        self._bg_color.grid(row=1, column=1, sticky="w", pady=(2, 0))
        row2 = ctk.CTkFrame(f, fg_color="transparent"); row2.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row2, text="Caption (optional)", text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        self._caption = c.entry(row2); self._caption.pack(fill="x", pady=(2, 0))
        toggles = ctk.CTkFrame(f, fg_color="transparent"); toggles.pack(fill="x", pady=(8, 0))
        self._shadow = ctk.CTkCheckBox(toggles, text="Drop shadow", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._shadow.select(); self._shadow.pack(side="left")
        self._watermark = ctk.CTkCheckBox(toggles, text="\"Made with KS ToolBox\" watermark", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._watermark.select(); self._watermark.pack(side="left", padx=20)
        self._hero_frame = f

    def _build_ba_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        ctk.CTkLabel(f, text="Second folder (counterparts, matched by filename)",
                     text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        pick = ctk.CTkFrame(f, fg_color="transparent"); pick.pack(fill="x", pady=(2, 0))
        self._ba_entry = c.entry(pick); self._ba_entry.pack(side="left", fill="x", expand=True)
        c.secondary_button(pick, "Browse", self._pick_ba, width=90).pack(side="left", padx=(8, 0))
        self._ba_frame = f

    def _pick_ba(self):
        d = filedialog.askdirectory(title="Choose the counterpart (after) folder")
        if d:
            self._ba_entry.delete(0, "end"); self._ba_entry.insert(0, d)

    def _on_mode_change(self, mode: str):
        """Show only the active mode's sub-options; contact ignores the shared cell
        as a per-thumbnail box, hero/ba use cell as the canvas/panel size."""
        for frame in (self._contact_frame, self._hero_frame, self._ba_frame):
            frame.pack_forget()
        if mode == "hero":
            self._hero_frame.pack(fill="x")
        elif mode == "before_after":
            self._ba_frame.pack(fill="x")
        else:
            self._contact_frame.pack(fill="x")

    def _collect_options(self):
        mode = self._mode.get()
        try:
            cell = int(float(self._cell.get()))
            padding = int(float(self._padding.get()))
            cols = int(float(self._cols.get() or "4"))
        except ValueError:
            self._logline("Cell size, padding and columns must be numbers.", t.STATE["error"][1]); return None
        bg_color = self._parse_hex(self._bg_color.get())
        if bg_color is None:
            self._logline("Backdrop colour must be a hex like #111827.", t.STATE["error"][1]); return None

        ba_folder = None
        if mode == "before_after":
            ba = self._ba_entry.get().strip()
            if not ba:
                self._logline("Before/After needs a second folder of counterparts.", t.STATE["error"][1]); return None
            ba_folder = Path(ba)

        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ShowcaseOptions(
            mode=mode, cols=cols, cell_size=cell, padding=padding,
            bg_style=self._bg_style.get(), bg_color=bg_color,
            caption=self._caption.get().strip(), watermark=bool(self._watermark.get()),
            shadow=bool(self._shadow.get()), labels=bool(self._labels.get()),
            title=self._title.get().strip(), ba_folder=ba_folder,
            out_root=out_root, input_root=input_root, mirror=mirror,
            dry_run=bool(self._dry.get()))

    @staticmethod
    def _parse_hex(text: str):
        s = text.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return None
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None

    # -- batch loop (tool-specific) --------------------------------------------

    def _work(self, files: list[Path], opts: e.ShowcaseOptions):
        rendered = previewed = failed = 0; results = []
        if opts.mode == "contact":
            self.after(0, self._logline, f"building contact sheet from {len(files)} images …", t.TEXT_MUTED)
            res = e.build_contact_sheet(files, opts); results.append(res)
            rendered = int(res.action == "rendered")
            previewed = int(res.action == "dry-run")
            failed = int(res.action == "failed")
            self.after(0, self._show, res, 1, 1)
        else:
            for i, f in enumerate(files, 1):
                if self._stop.is_set():
                    self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
                self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
                res = e.process(f, opts); results.append(res)
                if res.action == "rendered":
                    rendered += 1
                elif res.action == "dry-run":
                    previewed += 1
                else:
                    failed += 1
                self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, rendered, previewed, failed, manifest)

    def _write_manifest(self, opts: e.ShowcaseOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "showcase_manifest.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "out_path", "detail", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, r.out_path, r.detail, r.reason])
            return str(path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"rendered": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"rendered": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        extra = f"  → {res.out_path}" if res.action == "rendered" else f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, rendered, previewed, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"rendered {rendered} · previewed {previewed} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

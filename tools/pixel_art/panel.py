"""Pixel Art Converter — the tool's UI. Thin over engine.py via BaseBatchPanel."""
from __future__ import annotations

import csv
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class PixelArtPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Convert"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Pixel size", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._pixel = ctk.CTkOptionMenu(row, values=["2", "4", "8", "16"], width=70,
                                        fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._pixel.set("4"); self._pixel.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Colors", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._colors = ctk.CTkOptionMenu(row, values=["8", "16", "32", "64", "256"], width=80,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._colors.set("16"); self._colors.grid(row=1, column=1, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./pixel_art beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._dither = ctk.CTkCheckBox(toggles, text="Dither", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dither.select(); self._dither.pack(side="left", padx=20)
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(6, 0))
        self._upscale = ctk.CTkCheckBox(row2, text="Upscale to original size (big visible pixels)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._upscale.select(); self._upscale.pack(side="left")
        self._mirror = ctk.CTkCheckBox(row2, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.PixelOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                              pixel_size=int(self._pixel.get()), num_colors=int(self._colors.get()),
                              dither=bool(self._dither.get()), upscale=bool(self._upscale.get()),
                              dry_run=bool(self._dry.get()))

    # -- batch loop (tool-specific) --------------------------------------------

    def _work(self, files: list[Path], opts: e.PixelOptions):
        converted = failed = skipped = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "converted":
                converted += 1
            elif res.action == "dry-run":
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, converted, skipped, failed, manifest)

    def _write_manifest(self, opts: e.PixelOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "pixel_manifest.csv"
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
        icon = {"converted": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        extra = f"  → {res.out_path}" if res.action == "converted" else f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, converted, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"converted {converted} · previewed {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

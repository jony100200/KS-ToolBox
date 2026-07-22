"""To SVG — the tool's UI. Thin over engine.py via BaseBatchPanel: collect
images + vtracer options, run on a worker thread, stream results back. No
vectorize logic here."""
from __future__ import annotations

import csv
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class ToSvgPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.EXPAND
    RUN_LABEL = "Preview & Vectorize"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Color mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._colormode = ctk.CTkOptionMenu(row, values=list(e.COLORMODES), width=100,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._colormode.set("color"); self._colormode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Filter speckle (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._speckle = c.entry(row, width=80); self._speckle.insert(0, "4")
        self._speckle.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Color precision", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._precision = ctk.CTkOptionMenu(row, values=["4", "5", "6", "7", "8"], width=70,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._precision.set("6"); self._precision.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./svg beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        try:
            speckle = int(self._speckle.get() or "4")
        except ValueError:
            self._logline("Filter speckle must be a number.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.SvgOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                            colormode=self._colormode.get(), filter_speckle=speckle,
                            color_precision=int(self._precision.get()),
                            dry_run=bool(self._dry.get()))

    # -- batch loop (tool-specific) --------------------------------------------

    def _work(self, files: list[Path], opts: e.SvgOptions):
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

    def _write_manifest(self, opts: e.SvgOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "svg_manifest.csv"
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
        self._summary.configure(text=f"vectorized {converted} · previewed {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

"""Image Rescale — the tool's UI. Thin over engine.py: collect images + options,
run on a worker thread, stream results back via after(). No sizing math here.
"""
from __future__ import annotations

import csv
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e

_MODE_LABELS = {
    "longest_side": "Longest side (px)",
    "max_mp": "Max megapixels",
    "scale_factor": "Scale factor",
    "fit_inside": "Fit inside (WxH)",
}


class ImageRescalePanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.EXPAND
    RUN_LABEL = "Preview & Resize"

    def __init__(self, parent):
        super().__init__(parent)
        self._on_mode_change(self._mode.get())

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._mode = ctk.CTkOptionMenu(row, values=list(e.MODES), width=160, command=self._on_mode_change,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._mode.set("longest_side"); self._mode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        # the single value entry whose meaning depends on the mode
        self._val_label = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._val_label.grid(row=0, column=1, sticky="w")
        self._val = c.entry(row, width=100); self._val.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        # second entry (only for fit_inside height)
        self._val2_label = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._val2_label.grid(row=0, column=2, sticky="w")
        self._val2 = c.entry(row, width=100); self._val2.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # resample + snap
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row2, text="Resample", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._resample = ctk.CTkOptionMenu(row2, values=["auto", "lanczos", "bicubic", "bilinear", "nearest"],
                                           width=120, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._resample.set("auto"); self._resample.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row2, text="Snap to multiple of (0=off)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._snap = c.entry(row2, width=80); self._snap.insert(0, "0")
        self._snap.grid(row=1, column=1, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./resized beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list resizes — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._upscale = ctk.CTkCheckBox(toggles, text="Allow upscaling", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._upscale.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _on_mode_change(self, mode: str):
        """The value fields relabel to match the chosen mode; height shows only for fit_inside."""
        defaults = {"longest_side": ("1024", ""), "max_mp": ("1.0", ""),
                    "scale_factor": ("0.5", ""), "fit_inside": ("1024", "1024")}
        self._val_label.configure(text=_MODE_LABELS[mode])
        self._val.delete(0, "end"); self._val.insert(0, defaults[mode][0])
        if mode == "fit_inside":
            self._val_label.configure(text="Fit width (px)")
            self._val2_label.configure(text="Fit height (px)")
            self._val2.delete(0, "end"); self._val2.insert(0, defaults[mode][1])
            self._val2.grid(); self._val2_label.grid()
        else:
            self._val2_label.configure(text=""); self._val2.grid_remove(); self._val2_label.grid_remove()

    def _collect_options(self):
        mode = self._mode.get()
        try:
            snap = int(self._snap.get() or "0")
            kw: dict = {}
            if mode == "longest_side":
                kw["longest_side"] = int(float(self._val.get()))
            elif mode == "max_mp":
                kw["max_mp"] = float(self._val.get())
            elif mode == "scale_factor":
                kw["scale_factor"] = float(self._val.get())
            elif mode == "fit_inside":
                kw["fit_w"] = int(float(self._val.get())); kw["fit_h"] = int(float(self._val2.get()))
        except ValueError:
            self._logline("Mode value(s) must be numbers.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ResizeOptions(out_root=out_root, input_root=input_root, mirror=mirror, mode=mode,
                               allow_upscale=bool(self._upscale.get()), snap=snap,
                               resample=self._resample.get(), dry_run=bool(self._dry.get()), **kw)

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.ResizeOptions):
        resized = skipped = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "resized":
                resized += 1
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, resized, skipped, failed, manifest)

    def _write_manifest(self, opts: e.ResizeOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-images run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "resize_manifest.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "before", "after", "out_path", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, r.before, r.after, r.out_path, r.reason])
            return str(path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"resized": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"resized": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("resized", "dry-run"):
            extra = f"  {res.before} → {res.after}"
            if res.out_path and res.action == "resized":
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, resized, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"resized {resized} · skipped {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

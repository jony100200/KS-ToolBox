"""Clean Cutout — the tool's UI. Thin over engine.py: collect images + options,
run on a worker thread, stream results back via after(). No image math here.
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


class CleanCutoutPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BROOM
    RUN_LABEL = "Preview & Cut"

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        # model + edge row
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Model", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._model = ctk.CTkOptionMenu(row, values=list(e.MODELS), width=180,
                                        fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                        button_hover_color=t.NEUTRAL_HOVER)
        self._model.set("u2net"); self._model.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Edge erode (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._erode = c.entry(row, width=60); self._erode.insert(0, "1")
        self._erode.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Feather", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._feather = c.entry(row, width=60); self._feather.insert(0, "0.6")
        self._feather.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./cutouts beside each source)")
        # toggles
        safety = ctk.CTkFrame(b, fg_color="transparent"); safety.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(safety, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._hiq = ctk.CTkCheckBox(safety, text="Hi-Q matte (needs rembg — extra models + alpha matting)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._hiq.pack(side="left", padx=20)   # off by default: lean onnx backend
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(6, 0))
        self._defringe = ctk.CTkCheckBox(row2, text="Defringe (clean edge halo)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._defringe.select(); self._defringe.pack(side="left")
        self._green = ctk.CTkCheckBox(row2, text="Green-screen despill", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._green.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(row2, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        try:
            erode_px = int(self._erode.get())
            feather = float(self._feather.get())
        except ValueError:
            self._logline("Erode must be an integer and feather a number.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.CutoutOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                               model=self._model.get(), alpha_matting=bool(self._matting.get()),
                               do_defringe=bool(self._defringe.get()), erode_px=erode_px,
                               feather=feather, green_despill=bool(self._green.get()),
                               dry_run=bool(self._dry.get()))

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.CutoutOptions):
        cut = skipped = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "cut":
                cut += 1
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, cut, skipped, failed, manifest)

    def _write_manifest(self, opts: e.CutoutOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-images run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "cutout_manifest.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "coverage", "out_path", "detail", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, f"{r.coverage:.3f}", r.out_path, r.detail, r.reason])
            return str(path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"cut": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"cut": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action == "cut":
            extra = f"  {res.coverage*100:.0f}% kept  ({res.detail})  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, cut, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"cut {cut} · skipped {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

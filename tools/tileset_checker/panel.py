"""Tileset Checker — the tool's UI. Thin over engine.py via BaseBatchPanel:
collect textures + options, run on a worker thread, stream seam scores back via
after(). No seam math here.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class TilesetCheckerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "texture"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Check"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Tile grid (N×N)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._tile_n = ctk.CTkOptionMenu(row, values=["2", "3", "4"], width=70,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._tile_n.set("3"); self._tile_n.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        # preview toggles
        prev = ctk.CTkFrame(b, fg_color="transparent"); prev.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(prev, text="Previews to write", text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        prow = ctk.CTkFrame(prev, fg_color="transparent"); prow.pack(fill="x", pady=(2, 0))
        self._offset = ctk.CTkCheckBox(prow, text="Wrap-offset", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._offset.select(); self._offset.pack(side="left")
        self._tile = ctk.CTkCheckBox(prow, text="Tile montage", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._tile.select(); self._tile.pack(side="left", padx=20)
        self._heatmap = ctk.CTkCheckBox(prow, text="Edge-diff heatmap", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._heatmap.select(); self._heatmap.pack(side="left", padx=20)
        # output folder
        self._build_output_row(b, "Output folder (blank = ./tileset_check beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (score & list writes — no files)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.TileOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                             tile_n=int(self._tile_n.get()),
                             make_offset=bool(self._offset.get()),
                             make_tile=bool(self._tile.get()),
                             make_heatmap=bool(self._heatmap.get()),
                             dry_run=bool(self._dry.get()))

    # -- batch loop (tool-specific) --------------------------------------------

    def _work(self, files: list[Path], opts: e.TileOptions):
        checked = previewed = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "checked":
                checked += 1
            elif res.action == "dry-run":
                previewed += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, checked, previewed, failed, manifest)

    def _write_manifest(self, opts: e.TileOptions, results: list) -> str | None:
        """Aggregate seam scores — CSV row per texture + a JSON sibling. Only
        written on a real (non-dry) run with an output folder set."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            csv_path = root / "seam_scores.csv"
            new = not csv_path.exists()
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "x", "y", "overall", "outputs", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, f"{r.x:.6f}", f"{r.y:.6f}",
                                f"{r.overall:.6f}", "|".join(r.outputs), r.reason])
            json_path = root / "seam_scores.json"
            tmp = json_path.with_name("seam_scores.part.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([r.to_dict() for r in results], f, indent=2)
            tmp.replace(json_path)
            return str(csv_path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"checked": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("checked", "dry-run"):
            verdict = "seamless" if res.overall >= 0.85 else ("borderline" if res.overall >= 0.6 else "visible seam")
            if res.action == "checked":
                color = t.STATE["done"][1] if res.overall >= 0.85 else t.TEXT_MUTED
            extra = f"  x={res.x:.3f} y={res.y:.3f} overall={res.overall:.3f}  [{verdict}]"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, checked, previewed, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"checked {checked} · previewed {previewed} · failed {failed}")
        if manifest:
            self._logline(f"  scores: {manifest}", t.TEXT_MUTED)

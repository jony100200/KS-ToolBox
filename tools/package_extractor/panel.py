"""Package Extractor — the tool's UI. Thin over engine.py: collect archives +
options, run on a worker thread, stream per-archive results back via after().
No extraction or safe-path logic here — that all lives in the engine.
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


class PackageExtractorPanel(BaseBatchPanel):
    FILE_EXTS = e.ARCHIVE_EXTS
    FILE_LABEL = "archive"
    RESULTS_ICON = Icons.FOLDER
    RUN_LABEL = "Preview & Extract"

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # output folder (required) — each archive lands in its own <stem>/ here
        self._build_output_row(b, "Output folder (required — each archive extracts into its own subfolder)")

        # extension filter + collision policy
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(row, text="Only extract these extensions (blank = all, e.g. .png, .fbx)",
                     text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w", columnspan=2)
        self._filter = c.entry(row, width=260)
        self._filter.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="On name clash", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._collision = ctk.CTkOptionMenu(row, values=["rename", "skip"], width=120,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                            button_hover_color=t.NEUTRAL_HOVER)
        self._collision.set("rename")
        self._collision.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list contents — no writes)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._nested = ctk.CTkCheckBox(toggles, text="Also extract nested archives (bounded)",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._nested.pack(side="left", padx=20)

        # run row
        self._build_run_row(b)

    def _collect_options(self):
        out = self._out_entry.get().strip()
        if not out:
            self._logline("Output folder is required.", t.STATE["error"][1]); return None
        return e.ExtractOptions(
            out_root=Path(out),
            filter_exts=e.norm_exts(self._filter.get()),
            collision=self._collision.get(),
            nested_depth=(2 if self._nested.get() else 0),
            dry_run=bool(self._dry.get()),
        )

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.ExtractOptions):
        extracted = skipped = failed = 0
        results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "extracted":
                extracted += 1
            elif res.action == "dry-run":
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, extracted, skipped, failed, manifest)

    def _write_manifest(self, opts: e.ExtractOptions, results: list) -> str | None:
        """Top-level CSV summarising every archive in the run (per-archive JSON+CSV
        reports are written by the engine into each archive's folder)."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "extract_manifest.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "written", "skipped", "rejected",
                                "errors", "out_path", "report", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, r.written, r.skipped, r.rejected,
                                r.errors, r.out_path, r.report_path, r.reason])
            return str(path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"extracted": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"extracted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        self._logline(f"  {icon} {name}  — {res.reason}", color)
        if res.action == "extracted" and res.rejected:
            self._logline(f"      ⚠ {res.rejected} unsafe entr(y/ies) rejected (see report)", t.STATE["error"][1])
        if res.report_path:
            self._logline(f"      report: {res.report_path}", t.TEXT_MUTED)

    def _done(self, extracted, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"extracted {extracted} · preview {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

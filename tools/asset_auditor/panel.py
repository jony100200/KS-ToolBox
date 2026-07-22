"""Asset Auditor — the tool's UI. Thin over engine.py: collect the image files +
options, run the (read-only) audit on a worker thread, stream category counts
back via after(), then write the HTML/JSON/CSV reports. No inspection logic here.
"""
from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class AssetAuditorPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "file"
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Run Audit"

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Near-dup Hamming (of 64 bits)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._hamming = c.entry(row, width=80); self._hamming.insert(0, "8")
        self._hamming.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Oversized over (MB)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._oversized = c.entry(row, width=80); self._oversized.insert(0, "25")
        self._oversized.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Min dimension (px)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._min_dim = c.entry(row, width=80); self._min_dim.insert(0, "32")
        self._min_dim.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # output folder (the report destination — the tool's only output)
        self._build_output_row(b, "Report folder (blank = ./asset_audit beside the scanned folder)")

        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._health = ctk.CTkCheckBox(toggles, text="Image health flags (dark / bright / low-contrast)",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._health.select(); self._health.pack(side="left")

        self._build_run_row(b)

    def _collect_options(self):
        try:
            hamming = int(self._hamming.get() or "8")
            oversized = float(self._oversized.get() or "25")
            min_dim = int(self._min_dim.get() or "32")
        except ValueError:
            self._logline("Hamming / MB / dimension must be numbers.", t.STATE["error"][1])
            return None
        scan_root = self._resolve_input_root()
        out = self._out_entry.get().strip()
        if out:
            out_root = Path(out)
        elif scan_root:
            out_root = Path(scan_root) / "asset_audit"
        else:
            out_root = None
        return e.AuditOptions(out_root=out_root, scan_root=scan_root,
                              near_dup_hamming=hamming, oversized_mb=oversized,
                              min_dimension=min_dim, check_health=bool(self._health.get()))

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.AuditOptions):
        def progress(i, total, name):
            self.after(0, self._tick, i, total, name)

        report = e.audit(files, opts, progress=progress)
        manifest = self._write_manifest(opts, report)
        self.after(0, self._done, report, manifest)

    def _tick(self, i: int, total: int, name: str):
        self._progress.set(i / total if total else 1)
        self._logline(f"[{i}/{total}] {name}", t.TEXT_MUTED)

    def _write_manifest(self, opts: e.AuditOptions, report) -> str | None:
        """Writes the three report files. Returns the HTML path (the headline
        output) or None. Reports are the tool's output — the audit is read-only."""
        if opts.out_root is None:
            return None
        res = e.write_reports(report, opts.out_root)
        if res["error"]:
            self.after(0, self._logline, f"  report write failed: {res['details']}",
                       t.STATE["error"][1])
            return None
        return res["data"]["html"]

    def _done(self, report, manifest: str | None = None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._progress.set(1)
        issues = report.issue_count()
        self._status.set_state("DONE", "done" if not issues else "waiting")

        # a per-category breakdown in the results log
        rows = [
            ("exact duplicate groups", len(report.exact_dups)),
            ("near-duplicate groups", len(report.near_dups)),
            ("corrupt / unreadable", len(report.corrupt)),
            ("unsafe filenames", len(report.unsafe_names)),
            ("empty files", len(report.empty_files)),
            ("oversized files", len(report.oversized)),
            ("empty folders", len(report.empty_folders)),
            ("tiny images", len(report.tiny_images)),
            ("image health flags", len(report.health_flags)),
        ]
        self._logline("", t.TEXT_MAIN)
        for label, n in rows:
            color = t.STATE["waiting"][1] if n else t.TEXT_MUTED
            self._logline(f"  {n:>4}  {label}", color)
        if report.resolution_histogram:
            top = list(report.resolution_histogram.items())[:5]
            self._logline("  resolutions: " + ", ".join(f"{k}×{v}" for k, v in top), t.TEXT_MUTED)

        self._summary.configure(text=f"{report.scanned} scanned · {issues} issue(s)")
        if manifest:
            self._logline(f"  report: {manifest}", t.TEXT_MUTED)
        elif report.scanned:
            self._logline("  no report folder set — add one to write audit.html/json/csv",
                          t.TEXT_MUTED)

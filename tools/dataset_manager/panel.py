"""Dataset Manager — the tool's UI. Thin over engine.py: collect images + options,
run the chosen operation on a worker thread, stream results back via after().

No dataset logic here — pairing, bucketing, splitting and replacing all live in
engine.py. The operation dropdown swaps which sub-options are visible.
"""
from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e

_OP_LABELS = {
    "pair_report": "Pair report (audit captions)",
    "replace": "Caption find & replace",
    "bucket": "Resolution bucketing",
    "split": "Train / val / test split",
}


class DatasetManagerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Preview & Run"

    def __init__(self, parent):
        super().__init__(parent)
        self._on_op_change(self._op.get())

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # operation + caption extensions
        top = ctk.CTkFrame(b, fg_color="transparent"); top.pack(fill="x")
        ctk.CTkLabel(top, text="Operation", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._op_labels = {_OP_LABELS[k]: k for k in e.OPERATIONS}
        self._op = ctk.CTkOptionMenu(top, values=[_OP_LABELS[k] for k in e.OPERATIONS], width=240,
                                     command=self._on_op_change, fg_color=t.BG_COLOR,
                                     button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._op.set(_OP_LABELS["pair_report"]); self._op.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(top, text="Caption extensions", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._caption_exts = c.entry(top, width=180); self._caption_exts.insert(0, ".txt, .caption")
        self._caption_exts.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # swappable sub-option frames (only the active operation's frame is shown)
        self._subframe = ctk.CTkFrame(b, fg_color="transparent"); self._subframe.pack(fill="x", pady=(10, 0))
        self._build_replace_frame()
        self._build_bucket_frame()
        self._build_split_frame()

        # output folder + preview toggle + run row
        self._build_output_row(b, "Output folder (results are copied here — sources are never touched)")
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list what would be copied/changed — no writes)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._build_run_row(b)

    def _build_replace_frame(self):
        f = ctk.CTkFrame(self._subframe, fg_color="transparent")
        ctk.CTkLabel(f, text="Find", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._find = c.entry(f, width=200); self._find.grid(row=1, column=0, sticky="w", padx=(0, 16), pady=(2, 0))
        ctk.CTkLabel(f, text="Replace with", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._replace = c.entry(f, width=200); self._replace.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(2, 0))
        self._regex = ctk.CTkCheckBox(f, text="Regex", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._regex.grid(row=1, column=2, sticky="w", pady=(2, 0))
        self._frame_replace = f

    def _build_bucket_frame(self):
        f = ctk.CTkFrame(self._subframe, fg_color="transparent")
        ctk.CTkLabel(f, text="Bucket by", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._bucket_mode = ctk.CTkOptionMenu(f, values=list(e.BUCKET_MODES), width=160, fg_color=t.BG_COLOR,
                                              button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._bucket_mode.set("dimensions"); self._bucket_mode.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkLabel(f, text="dimensions = WxH folders · aspect = portrait / landscape / square",
                     text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=1, column=1, sticky="w", padx=(16, 0))
        self._frame_bucket = f

    def _build_split_frame(self):
        f = ctk.CTkFrame(self._subframe, fg_color="transparent")
        ctk.CTkLabel(f, text="Split ratios (train / val / test)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=0, columnspan=3, sticky="w")
        self._r_train = c.entry(f, width=70); self._r_train.insert(0, "0.8"); self._r_train.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(2, 0))
        self._r_val = c.entry(f, width=70); self._r_val.insert(0, "0.1"); self._r_val.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(2, 0))
        self._r_test = c.entry(f, width=70); self._r_test.insert(0, "0.1"); self._r_test.grid(row=1, column=2, sticky="w", pady=(2, 0))
        ctk.CTkLabel(f, text="deterministic: sorted by filename, no randomness",
                     text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=1, column=3, sticky="w", padx=(16, 0))
        self._frame_split = f

    def _on_op_change(self, label: str):
        """Show only the active operation's sub-option frame."""
        op = self._op_labels.get(label, "pair_report")
        for fr in (self._frame_replace, self._frame_bucket, self._frame_split):
            fr.pack_forget()
        if op == "replace":
            self._frame_replace.pack(fill="x")
        elif op == "bucket":
            self._frame_bucket.pack(fill="x")
        elif op == "split":
            self._frame_split.pack(fill="x")

    def _collect_options(self):
        op = self._op_labels.get(self._op.get(), "pair_report")
        raw = self._caption_exts.get().replace(";", ",")
        caption_exts = tuple(x.strip() for x in raw.split(",") if x.strip()) or e._DEFAULT_CAPTION_EXTS
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        dry_run = bool(self._dry.get())

        ratios = (0.8, 0.1, 0.1)
        if op == "split":
            try:
                ratios = (float(self._r_train.get()), float(self._r_val.get()), float(self._r_test.get()))
            except ValueError:
                self._logline("Split ratios must be numbers.", t.STATE["error"][1]); return None
            if sum(ratios) <= 0:
                self._logline("Split ratios must sum to more than zero.", t.STATE["error"][1]); return None
        if op == "replace" and not self._find.get():
            self._logline("Enter text to find for a replace.", t.STATE["error"][1]); return None
        if op != "pair_report" and not dry_run and out_root is None:
            self._logline("Choose an output folder (or keep Preview only).", t.STATE["error"][1]); return None
        if not dry_run and out_root is None and op == "pair_report":
            self._logline("Choose an output folder to write the report (or keep Preview only).",
                          t.STATE["error"][1]); return None

        return e.DatasetOptions(
            operation=op, caption_exts=caption_exts,
            find=self._find.get(), replace=self._replace.get(), regex=bool(self._regex.get()),
            bucket_mode=self._bucket_mode.get(), ratios=ratios,
            out_root=out_root, dry_run=dry_run)

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.DatasetOptions):
        def log(msg: str):
            self.after(0, self._logline, msg, t.TEXT_MUTED)

        def progress(done: int, total: int):
            self.after(0, self._progress.set, done / total if total else 1)

        report = e.run(files, opts, log=log, progress=progress, should_stop=self._stop.is_set)
        self.after(0, self._done, report)

    def _write_manifest(self, opts, results):   # engine writes its own manifest inside run()
        return None

    def _show(self, *_):                          # streaming handled via the log callback
        pass

    def _done(self, report: e.Report):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        state = "error" if report.failed else ("waiting" if report.degraded else "done")
        self._status.set_state("DONE", state)
        self._progress.set(1)
        for msg in report.messages:
            self._logline(f"  {msg}", t.TEXT_MUTED)
        op = report.operation
        if op == "pair_report":
            summary = f"paired {report.pairs} · missing {report.missing} · orphans {report.orphans}"
        elif op == "replace":
            summary = f"{report.changed} replacement(s) · copied {report.copied} · failed {report.failed}"
        else:
            summary = f"copied {report.copied} · pairs {report.pairs} · failed {report.failed}"
        self._summary.configure(text=summary)
        if report.manifest:
            self._logline(f"  manifest: {report.manifest['csv']}", t.TEXT_MUTED)
        if report.pair_report:
            self._logline(f"  pair report: {report.pair_report['csv']}", t.TEXT_MUTED)

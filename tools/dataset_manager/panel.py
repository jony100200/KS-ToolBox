"""Dataset Manager — the tool's UI. Thin over engine.py: collect images + options,
run the chosen operation on a worker thread, stream results back via after().

No dataset logic here — pairing, bucketing, splitting and replacing all live in
engine.py. The operation dropdown swaps which sub-options are visible.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.job_queue import QueueCompletion, QueueSubmission
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

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._stage_progress: tuple[int, int] | None = None
        self._last_stage_position = 0
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

        opts = e.DatasetOptions(
            operation=op, caption_exts=caption_exts,
            find=self._find.get(), replace=self._replace.get(), regex=bool(self._regex.get()),
            bucket_mode=self._bucket_mode.get(), ratios=ratios,
            out_root=out_root, dry_run=dry_run)
        normalized, error = e.normalized_options(opts)
        if normalized is None:
            self._logline(error, t.STATE["error"][1])
            return None
        return normalized

    def _pre_run_check(self, _opts: e.DatasetOptions) -> bool:
        if len(self._files) <= e.MAX_DATASET_FILES:
            return True
        self._logline(
            f"Dataset limit is {e.MAX_DATASET_FILES:,} images; split this collection.",
            t.STATE["error"][1],
        )
        return False

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.DatasetOptions):
        def log(msg: str):
            self.after(0, self._logline, msg, t.TEXT_MUTED)

        def progress(done: int, total: int):
            self.after(0, self._progress.set, done / total if total else 1)

        result = e.process(
            files, opts, log=log, progress=progress,
            cancelled=lambda: self._stop.is_set(),
        )
        self.after(0, self._done, result, result.out_path)

    def _build_submission(self, files: list[Path], opts: e.DatasetOptions) -> QueueSubmission:
        files = sorted(files, key=lambda path: str(path).casefold())
        dependencies = e.identity_dependencies(files, opts)
        self._stage_progress = None
        self._last_stage_position = 0
        definition = JobDefinition.create(
            tool_id="dataset_manager",
            tool_version="1",
            workflow_version="grouped-dataset.v2",
            inputs=[files[0]],
            identity_dependencies=dependencies,
            settings=asdict(opts),
            max_retries=1,
        )

        def execute(_anchor: Path, token) -> e.DatasetResult:
            return e.process(
                files, opts,
                progress=self._record_stage_progress,
                cancelled=lambda: token.is_cancelled,
            )

        def classify(result: e.DatasetResult) -> ItemOutcome:
            data = result.to_dict()
            if result.action == "processed":
                if result.degraded or result.messages:
                    return ItemOutcome.warning(data, result.reason)
                return ItemOutcome.completed(data, result.reason)
            if result.action == "preview":
                return ItemOutcome.skipped(data, result.reason)
            return ItemOutcome.failed(
                result.reason, retryable=result.retryable, data=data
            )

        return QueueSubmission(
            definition=definition,
            label=f"Dataset Manager {opts.operation.replace('_', ' ').title()} · {len(files)} image(s)",
            execute=execute,
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="dataset_manager"
            ),
        )

    def _record_stage_progress(self, done: int, total: int) -> None:
        self._stage_progress = (done, total)

    def _on_queue_snapshot(self, snapshot) -> None:
        super()._on_queue_snapshot(snapshot)
        stage = self._stage_progress
        if stage is not None and stage[0] != self._last_stage_position:
            self._last_stage_position = stage[0]
            self._progress.set(stage[0] / stage[1] if stage[1] else 1)

    def _write_manifest(self, _opts, results):
        return next(
            (result.out_path for result in results if result.action == "processed"),
            None,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.DatasetResult:
        if item.data and {"action", "reason", "operation"}.issubset(item.data):
            return e.DatasetResult(**item.data)
        return e.DatasetResult(
            "failed", item.details or "dataset run quarantined", "unknown",
            detail="batch.quarantined",
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        result = payload.results[0] if payload.results else e.DatasetResult(
            "failed", "dataset run produced no terminal result", "unknown",
            detail="batch.empty",
        )
        self._done(
            result, payload.manifest or result.out_path, payload.report_path,
            self._queue_completion_state(completion), completion.report.recovered,
            completion.report.reused,
        )

    def _show(self, result: e.DatasetResult, _position: int, _total: int):
        icon = "✓" if result.action in {"processed", "preview"} else "✗"
        color = t.STATE["done"][1] if result.action == "processed" else t.TEXT_MUTED
        if result.action == "failed":
            color = t.STATE["error"][1]
        self._logline(f"  {icon} {result.reason}", color)

    def _done(self, result: e.DatasetResult, manifest=None, report_path=None,
              job_state=JobState.COMPLETED, recovered=False, reused=False):
        for msg in result.messages:
            self._logline(f"  {msg}", t.TEXT_MUTED)
        op = result.operation
        if op == "pair_report":
            summary = f"paired {result.pairs} · missing {result.missing} · orphans {result.orphans}"
        elif op == "replace":
            summary = f"{result.changed} replacement(s) · copied {result.copied} · failed {result.failed}"
        else:
            summary = f"copied {result.copied} · pairs {result.pairs} · failed {result.failed}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )

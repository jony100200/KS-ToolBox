"""Audio Tool UI; FFmpeg work executes through the shell-owned queue."""
from __future__ import annotations

import csv
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


class AudioToolPanel(BaseBatchPanel):
    FILE_EXTS = e.AUDIO_EXTS
    FILE_LABEL = "audio file"
    FILES_ICON = Icons.PLAY
    RESULTS_ICON = Icons.PLAY
    RUN_LABEL = "Preview & Process"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._refresh_tools_hint()

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # format + bitrate row
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Target format", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._fmt = ctk.CTkOptionMenu(row, values=list(e.TARGET_FORMATS), width=110,
                                      fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                      button_hover_color=t.NEUTRAL_HOVER)
        self._fmt.set("mp3"); self._fmt.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Bitrate (lossy only)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._bitrate = ctk.CTkOptionMenu(row, values=["96k", "128k", "192k", "256k", "320k"], width=90,
                                          fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                          button_hover_color=t.NEUTRAL_HOVER)
        self._bitrate.set("192k"); self._bitrate.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # trim row
        trim = ctk.CTkFrame(b, fg_color="transparent"); trim.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(trim, text="Trim start (s or mm:ss)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._trim_start = c.entry(trim, width=110); self._trim_start.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(trim, text="Trim end (s or mm:ss)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._trim_end = c.entry(trim, width=110); self._trim_end.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # fade row
        fade = ctk.CTkFrame(b, fg_color="transparent"); fade.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(fade, text="Fade in (s)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._fade_in = c.entry(fade, width=90); self._fade_in.insert(0, "0"); self._fade_in.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(fade, text="Fade out (s)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._fade_out = c.entry(fade, width=90); self._fade_out.insert(0, "0"); self._fade_out.grid(row=1, column=1, sticky="w", pady=(2, 0))

        # output folder
        self._build_output_row(b, "Output folder (blank = ./audio beside each source)")

        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list actions — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._normalize = ctk.CTkCheckBox(toggles, text="Normalize loudness", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._normalize.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)

        # run row
        self._build_run_row(b)
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))

    def _refresh_tools_hint(self):
        s = e.tools_status()
        missing = [k for k, v in s.items() if not v]
        if missing:
            self._tools_hint.configure(text=f"⚠ missing: {', '.join(missing)} — bundle bin/ffmpeg or install ffmpeg")
        else:
            self._tools_hint.configure(text="tools: ffmpeg ✓  ffprobe ✓")

    def _collect_options(self):
        try:
            fade_in = float(self._fade_in.get() or "0")
            fade_out = float(self._fade_out.get() or "0")
        except ValueError:
            self._logline("Fade values must be numbers.", t.STATE["error"][1]); return None
        if fade_in < 0 or fade_out < 0:
            self._logline("Fade values must be zero or positive.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.AudioOptions(
            target_format=self._fmt.get(), bitrate=self._bitrate.get(),
            trim_start=self._trim_start.get().strip(), trim_end=self._trim_end.get().strip(),
            fade_in=fade_in, fade_out=fade_out, normalize=bool(self._normalize.get()),
            out_root=out_root, input_root=input_root, mirror=mirror,
            dry_run=bool(self._dry.get()),
        )

    # -- queue submission (tool-specific) --------------------------------------

    def _pre_run_check(self, opts: e.AudioOptions) -> bool:
        if opts.dry_run:
            return True
        return self._check_output_collisions(e.find_output_collisions(self._files, opts))

    def _build_submission(self, files: list[Path], opts: e.AudioOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="audio_tool",
            tool_version="1",
            workflow_version="audio.v2",
            inputs=files,
            settings=asdict(opts),
            max_retries=1,
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "processed":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, retryable=res.retryable, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Audio Tool · {len(files)} file(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="audio_tool"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        processed = sum(result.action == "processed" for result in results)
        skipped = sum(result.action in {"skipped", "dry-run"} for result in results)
        failed = len(results) - processed - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            processed, skipped, failed, remaining, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            item.input_path,
            "failed",
            item.details or "audio item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.AudioOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a large batch."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "audio_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "before", "after", "ops", "out_path", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.before, r.after, r.ops, r.out_path, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"processed": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"processed": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("processed", "dry-run"):
            extra = f"  {res.before} → {res.after}  [{res.ops}]"
            if res.out_path and res.action == "processed":
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, processed, skipped, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"processed {processed} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )

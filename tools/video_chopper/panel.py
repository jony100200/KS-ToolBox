"""Video Chopper UI; multi-clip FFmpeg work uses the shell-owned queue."""
from __future__ import annotations

import csv
import math
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


class VideoChopperPanel(BaseBatchPanel):
    FILE_EXTS = e.VIDEO_EXTS
    FILE_LABEL = "video"
    FILES_ICON = Icons.VIDEO
    RESULTS_ICON = Icons.SCISSORS
    RUN_LABEL = "Preview & Chop"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._refresh_tools_hint()

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        # detection row
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Min black gap (s)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._min_black = c.entry(row, width=70); self._min_black.insert(0, "0.10")
        self._min_black.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Black threshold (0-1)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._pix_th = c.entry(row, width=70); self._pix_th.insert(0, "0.10")
        self._pix_th.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Min clip length (s)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._min_clip = c.entry(row, width=70); self._min_clip.insert(0, "0.50")
        self._min_clip.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = <video>_clips beside each source)")
        # toggles — safety row
        safety = ctk.CTkFrame(b, fg_color="transparent"); safety.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(safety, text="Preview only (plan clips — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._reencode = ctk.CTkCheckBox(safety, text="Frame-accurate (re-encode H.264, slower)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._reencode.pack(side="left", padx=20)
        batch = ctk.CTkFrame(b, fg_color="transparent"); batch.pack(fill="x", pady=(6, 0))
        self._mirror = ctk.CTkCheckBox(batch, text="Mirror input folder structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left")
        # run row
        self._build_run_row(b)
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))

    def _refresh_tools_hint(self):
        s = e.tools_status()
        missing = [k for k, v in s.items() if not v]
        if missing:
            self._tools_hint.configure(text=f"⚠ missing: {', '.join(missing)} — bundle a bin/ or install ffmpeg")
        else:
            self._tools_hint.configure(text="tools: ffmpeg ✓  ffprobe ✓")

    def _collect_options(self):
        try:
            min_black = float(self._min_black.get())
            pix_th = float(self._pix_th.get())
            min_clip = float(self._min_clip.get())
        except ValueError:
            self._logline("Gap / threshold / clip length must be numbers.", t.STATE["error"][1]); return None
        if (
            not math.isfinite(min_black)
            or min_black <= 0
            or not math.isfinite(pix_th)
            or not 0 <= pix_th <= 1
            or not math.isfinite(min_clip)
            or min_clip <= 0
        ):
            self._logline(
                "Gap and clip length must be positive; threshold must be between 0 and 1.",
                t.STATE["error"][1],
            )
            return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        # mirror relative to the common ancestor of every source's folder.
        input_root = self._resolve_input_root() if mirror else None
        return e.ChopOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                             min_black_s=min_black, pix_th=pix_th, min_clip_s=min_clip,
                             reencode=bool(self._reencode.get()), dry_run=bool(self._dry.get()))

    # -- queue submission (tool-specific) --------------------------------------

    def _pre_run_check(self, opts: e.ChopOptions) -> bool:
        if opts.dry_run:
            return True
        return self._check_output_collisions(e.find_output_collisions(self._files, opts))

    def _build_submission(self, files: list[Path], opts: e.ChopOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="video_chopper",
            tool_version="1",
            workflow_version="black-gap-chop.v2",
            inputs=files,
            settings=asdict(opts),
            max_retries=1,
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "chopped":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, retryable=res.retryable, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Video Chopper · {len(files)} file(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="video_chopper"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        chopped = sum(result.action == "chopped" for result in results)
        skipped = sum(result.action in {"skipped", "dry-run"} for result in results)
        failed = len(results) - chopped - skipped
        total_clips = sum(
            result.clips for result in results
            if result.action in {"chopped", "dry-run"}
        )
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            chopped, skipped, failed, total_clips, remaining, payload.manifest,
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
            item.details or "video-chop item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.ChopOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-videos run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "chop_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "clips", "out_dir", "detail", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.clips, r.out_dir, r.detail, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"chopped": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"chopped": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action == "chopped":
            extra = f"  {res.clips} clips → {res.out_dir}  ({res.detail})"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, chopped, skipped, failed, total_clips, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = (f"chopped {chopped} · skipped {skipped} · failed {failed} · "
                   f"{total_clips} clips total")
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )

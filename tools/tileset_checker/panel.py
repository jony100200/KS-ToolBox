"""Tileset Checker UI; seam analysis executes through the shell-owned queue."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_reporting import completion_report_path, prepare_batch_completion
from toolbox.job_queue import QueueCompletion, QueueFinalization, QueueSubmission
from . import engine as e


class TilesetCheckerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "texture"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Check"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

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

    # -- queue submission (tool-specific) --------------------------------------

    def _pre_run_check(self, opts: e.TileOptions) -> bool:
        if opts.dry_run:
            return True
        collisions = e.find_output_collisions(self._files, opts)
        if not collisions:
            return True
        self._logline(
            f"Cannot start: {len(collisions)} output path collision(s). "
            "Choose mirror mode, a different output folder, or rename the inputs.",
            t.STATE["error"][1],
        )
        for output, owners in list(collisions.items())[:5]:
            self._logline(f"  {output}", t.STATE["error"][1])
            for owner in owners:
                self._logline(f"    ← {owner}", t.TEXT_MUTED)
        return False

    def _build_submission(self, files: list[Path], opts: e.TileOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="tileset_checker",
            tool_version="1",
            workflow_version="seam-check.v2",
            inputs=files,
            settings=asdict(opts),
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "checked":
                return ItemOutcome.completed(data, res.reason)
            if res.action == "dry-run":
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Tileset Checker · {len(files)} texture(s)",
            execute=lambda path, token: e.process(path, opts),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_completion(report, opts),
        )

    def _prepare_completion(self, report, opts: e.TileOptions) -> QueueFinalization:
        return prepare_batch_completion(
            report,
            result_from_record=self._result_from_record,
            write_manifest=lambda results: self._write_manifest(opts, results),
            report_path=completion_report_path(
                "tileset_checker", report.job_id,
                out_root=opts.out_root, dry_run=opts.dry_run,
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        checked = sum(result.action == "checked" for result in results)
        previewed = sum(result.action == "dry-run" for result in results)
        failed = len(results) - checked - previewed
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            checked, previewed, failed, remaining, payload.manifest,
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
            item.details or "tileset item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.TileOptions, results: list) -> str | None:
        """Aggregate seam scores — CSV row per texture + a JSON sibling. Only
        written on a real (non-dry) run with an output folder set."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
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

    def _done(self, checked, previewed, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"checked {checked} · previewed {previewed} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )

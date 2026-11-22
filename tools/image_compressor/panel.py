"""Image Compressor UI; execution runs through the shell-owned job queue."""
from __future__ import annotations

import csv
import threading
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, JobDefinition
from toolbox.batch_panel import BaseBatchPanel
from toolbox.icons import Icons
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class ImageCompressorPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    FILES_ICON = Icons.GRID
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Analyze & Compress"

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._mode = ctk.CTkOptionMenu(
            row,
            values=["Lossless WebP (exact pixels)", "Lossless Same Format", "Visually Lossless (PSNR >= 42 dB)"],
            width=240,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._mode.set("Lossless WebP (exact pixels)")
        self._mode.grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(2, 0))

        ctk.CTkLabel(row, text="Quality (lossy)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._quality = c.entry(row, width=70)
        self._quality.insert(0, "90")
        self._quality.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(2, 0))

        ctk.CTkLabel(row, text="Min PSNR (dB)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._psnr = c.entry(row, width=70)
        self._psnr.insert(0, "42")
        self._psnr.grid(row=1, column=2, sticky="w", pady=(2, 0))

        self._build_output_row(b, "Output folder (blank = ./compressed beside each source)")

        safety = ctk.CTkFrame(b, fg_color="transparent")
        safety.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(safety, text="Dry run (analyze only — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select()
        self._dry.pack(side="left")
        self._del = ctk.CTkCheckBox(safety, text="Delete original after verified output (→ Recycle Bin)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._del.pack(side="left", padx=20)

        batch = ctk.CTkFrame(b, fg_color="transparent")
        batch.pack(fill="x", pady=(6, 0))
        self._mirror = ctk.CTkCheckBox(batch, text="Mirror input folder structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select()
        self._mirror.pack(side="left")
        self._skip = ctk.CTkCheckBox(batch, text="Skip already-done (resumable)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._skip.select()
        self._skip.pack(side="left", padx=20)

        self._build_run_row(b)
        self._clean_btn = c.secondary_button(b, "Verify & Clean Originals (from Output Folder)", self._verify_and_clean, width=320)
        self._clean_btn.pack(anchor="w", pady=(8, 0))

    def _collect_options(self):
        try:
            quality = int(self._quality.get() or "90")
            psnr = float(self._psnr.get() or "42")
        except ValueError:
            self._logline("Quality and Min PSNR must be numbers.", t.STATE["error"][1])
            return None
        mode_map = {
            "Lossless WebP (exact pixels)": "lossless_webp",
            "Lossless Same Format": "lossless_same",
            "Visually Lossless (PSNR >= 42 dB)": "visually_lossless",
        }
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.CompressOptions(
            out_root=out_root,
            input_root=input_root,
            mirror=mirror,
            mode=mode_map[self._mode.get()],
            quality=quality,
            psnr_floor=psnr,
            dry_run=bool(self._dry.get()),
            delete_original=bool(self._del.get()),
            skip_existing=bool(self._skip.get()),
        )

    def _pre_run_check(self, opts: e.CompressOptions) -> bool:
        if opts.delete_original and not opts.dry_run:
            ok = messagebox.askyesno(
                "Delete originals after compression?",
                f"{len(self._files)} original image(s) will be sent to the Recycle Bin "
                f"after pixel/PSNR verification and smaller size check.\n\nContinue?",
                icon="warning",
                parent=self,
            )
            if not ok:
                self._logline("Cancelled — originals kept.", t.TEXT_MUTED)
                return False
        return True

    def _verify_and_clean(self):
        out = self._out_entry.get().strip()
        if not out or not Path(out).is_dir():
            self._logline("Pick an existing Output folder containing .img.json proofs first.", t.STATE["error"][1])
            return
        try:
            psnr_floor = float(self._psnr.get() or "42")
        except ValueError:
            self._logline("Min PSNR must be a number.", t.STATE["error"][1])
            return
        self._clean_btn.configure(state="disabled")

        def _preview():
            results = e.cleanup_verified_originals(out, psnr_floor=psnr_floor, dry_run=True)

            def _confirm():
                self._clean_btn.configure(state="normal")
                ready = [r for r in results if r.status == "dry_run_ok"]
                reclaim_mb = sum(r.saved_bytes for r in ready) / 1e6
                self._logline(f"Proof audit: {len(results)} proof(s) · {len(ready)} verified ready ({reclaim_mb:.1f} MB)", t.TEXT_MAIN)
                if not ready:
                    return
                if not messagebox.askyesno(
                    "Remove verified larger originals?",
                    f"Send {len(ready)} verified original image(s) to Recycle Bin to reclaim {reclaim_mb:.1f} MB?",
                    icon="warning",
                    parent=self,
                ):
                    return
                self._clean_btn.configure(state="disabled")

                def _exec():
                    done = e.cleanup_verified_originals(out, psnr_floor=psnr_floor, dry_run=False)
                    rem = sum(1 for r in done if r.status == "removed")
                    self.after(0, lambda: (
                        self._clean_btn.configure(state="normal"),
                        self._logline(f"Removed {rem} original image(s) → Recycle Bin.", t.STATE["done"][1]),
                    ))

                threading.Thread(target=_exec, daemon=True).start()

            self.after(0, _confirm)

        threading.Thread(target=_preview, daemon=True).start()

    def _build_submission(self, files: list[Path], opts: e.CompressOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="image_compressor",
            tool_version="1",
            workflow_version="img_compress.v1",
            inputs=files,
            settings=asdict(opts),
            max_retries=1,
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "compressed":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Image Compressor · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts, cancelled=lambda: token.is_cancelled),
            classify=classify,
            validate_stored=lambda item: e.validate_result(e.Result(**item.data)) if item.data else False,
            finalize=lambda report: self._prepare_queue_completion(report, opts, tool_id="image_compressor"),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        compressed = sum(r.action == "compressed" for r in results)
        skipped = sum(r.action in {"skipped", "dry-run"} for r in results)
        failed = len(results) - compressed - skipped
        saved_mb = sum((r.before_kb - r.after_kb) / 1024 for r in results if r.action == "compressed")
        self._finish_queue_ui(
            f"compressed {compressed} · skipped {skipped} · failed {failed} · saved {saved_mb:.1f} MB",
            job_state=self._queue_completion_state(completion),
            recovered=report.recovered,
            reused=report.reused,
            manifest=payload.manifest,
            report_path=payload.report_path,
        )

    def _write_manifest(self, opts: e.CompressOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "image_compression_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "before_kb", "after_kb", "saved_pct", "lossless_exact", "psnr", "out_path", "original_removed", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.before_kb, r.after_kb, r.saved_pct, r.lossless_exact, r.psnr, r.out_path, r.original_removed, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"compressed": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"compressed": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        self._logline(f"  {icon} {Path(res.src).name}  — {res.reason}", color)

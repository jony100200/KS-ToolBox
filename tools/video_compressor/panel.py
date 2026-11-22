"""Video Compressor UI; execution is submitted to the shell-owned job queue."""
from __future__ import annotations

import csv
import threading
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import (
    ItemOutcome,
    ItemRecord,
    JobDefinition,
    JobState,
)
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class VideoCompressorPanel(BaseBatchPanel):
    FILE_EXTS = e.VIDEO_EXTS
    FILE_LABEL = "video"
    FILES_ICON = Icons.VIDEO
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Analyze & Compress"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._refresh_tools_hint()

    # -- options (tool-specific) ----------------------------------------------
    _PRESETS: dict[str, dict[str, str]] = {
        "Archival (AV1 CRF 20)": {
            "encoder": "svt_av1", "crf": "20", "vmaf": "92", "container": "MKV",
            "max_height": "Keep original", "max_fps": "Keep original", "audio": "Copy original",
        },
        "Space Saver 1080p (AV1 CRF 24)": {
            "encoder": "svt_av1", "crf": "24", "vmaf": "90", "container": "MKV",
            "max_height": "1080p", "max_fps": "30 fps", "audio": "Shrink raw/bloated audio",
        },
        "Compatible 1080p (HEVC NVENC · MP4)": {
            "encoder": "nvenc_hevc", "crf": "22", "vmaf": "90", "container": "MP4",
            "max_height": "1080p", "max_fps": "Keep original", "audio": "Shrink raw/bloated audio",
        },
        "Fast Intel Arc (AV1 QSV CRF 22)": {
            "encoder": "qsv_av1", "crf": "22", "vmaf": "92", "container": "MKV",
            "max_height": "Keep original", "max_fps": "Keep original", "audio": "Shrink raw/bloated audio",
        },
        "Fast NVIDIA (AV1 NVENC CRF 22)": {
            "encoder": "nvenc_av1", "crf": "22", "vmaf": "92", "container": "MKV",
            "max_height": "Keep original", "max_fps": "Keep original", "audio": "Shrink raw/bloated audio",
        },
    }

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # preset row
        prow = ctk.CTkFrame(b, fg_color="transparent"); prow.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(prow, text="Quick Preset", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 10))
        self._preset = ctk.CTkOptionMenu(
            prow, values=["Custom", *self._PRESETS.keys()], width=260,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER, command=self._apply_preset,
        )
        self._preset.set("Archival (AV1 CRF 20)")
        self._preset.pack(side="left")

        # encoder + crf + vmaf + container row
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        self._enc_labels = {spec["label"]: key for key, spec in e.ENCODERS.items()}
        self._enc_keys = {key: spec["label"] for key, spec in e.ENCODERS.items()}
        ctk.CTkLabel(row, text="Encoder", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._encoder = ctk.CTkOptionMenu(row, values=list(self._enc_labels.keys()),
                                          width=235, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                          button_hover_color=t.NEUTRAL_HOVER)
        self._encoder.grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(2, 0))
        ctk.CTkLabel(row, text="CRF", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._crf = ctk.CTkOptionMenu(row, values=["18", "20", "22", "24", "28"], width=70,
                                      fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._crf.set("20"); self._crf.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(2, 0))
        ctk.CTkLabel(row, text="Min VMAF", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._vmaf = c.entry(row, width=60); self._vmaf.insert(0, "92"); self._vmaf.grid(row=1, column=2, sticky="w", padx=(0, 18), pady=(2, 0))
        ctk.CTkLabel(row, text="Container", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=3, sticky="w")
        self._container = ctk.CTkOptionMenu(row, values=["MKV", "MP4"], width=85,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                            button_hover_color=t.NEUTRAL_HOVER)
        self._container.set("MKV"); self._container.grid(row=1, column=3, sticky="w", pady=(2, 0))

        # resolution + fps + audio space-saving caps
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row2, text="Max resolution", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._max_height = ctk.CTkOptionMenu(row2, values=["Keep original", "1080p", "720p"], width=130,
                                             fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._max_height.set("Keep original"); self._max_height.grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(2, 0))
        ctk.CTkLabel(row2, text="Max FPS", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._max_fps = ctk.CTkOptionMenu(row2, values=["Keep original", "30 fps", "24 fps"], width=120,
                                          fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._max_fps.set("Keep original"); self._max_fps.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=(2, 0))
        ctk.CTkLabel(row2, text="Audio", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._audio_mode = ctk.CTkOptionMenu(row2, values=["Copy original", "Shrink raw/bloated audio"], width=200,
                                             fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._audio_mode.set("Copy original"); self._audio_mode.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # output folder
        self._build_output_row(b, "Output folder (blank = ./compressed beside each source)")
        # toggles — safety row + batch row
        safety = ctk.CTkFrame(b, fg_color="transparent"); safety.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(safety, text="Dry run (analyze only — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._del = ctk.CTkCheckBox(safety, text="Delete original after verified-good output (→ Recycle Bin)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._del.pack(side="left", padx=20)
        batch = ctk.CTkFrame(b, fg_color="transparent"); batch.pack(fill="x", pady=(6, 0))
        self._mirror = ctk.CTkCheckBox(batch, text="Mirror input folder structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left")
        self._skip = ctk.CTkCheckBox(batch, text="Skip already-done (resumable)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._skip.select(); self._skip.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)
        self._clean_btn = c.secondary_button(b, "Verify & Clean Originals (from Output Folder)", self._verify_and_clean_originals, width=320)
        self._clean_btn.pack(anchor="w", pady=(8, 0))
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))

    def _apply_preset(self, choice: str):
        spec = self._PRESETS.get(choice)
        if not spec:
            return
        self._encoder.set(self._enc_keys[spec["encoder"]])
        self._crf.set(spec["crf"])
        self._vmaf.delete(0, "end"); self._vmaf.insert(0, spec["vmaf"])
        self._container.set(spec["container"])
        self._max_height.set(spec["max_height"])
        self._max_fps.set(spec["max_fps"])
        self._audio_mode.set(spec["audio"])

    def _refresh_tools_hint(self):
        s = e.tools_status()
        missing = [k for k, v in s.items() if not v]
        if missing:
            self._tools_hint.configure(text=f"⚠ missing on PATH: {', '.join(missing)} — install ffmpeg/ffprobe")
        else:
            encs = ", ".join(e.available_encoders()) or "none"
            self._tools_hint.configure(text=f"tools: ffmpeg ✓  ffprobe ✓  | encoders (probing…): {encs}")
            def _probe():
                verified = ", ".join(e.available_encoders(verify=True)) or "none"
                try:
                    self.after(0, lambda: self._tools_hint.configure(
                        text=f"tools: ffmpeg ✓  ffprobe ✓  | working encoders: {verified}"
                    ))
                except Exception:
                    pass
            threading.Thread(target=_probe, daemon=True).start()

    def _verify_and_clean_originals(self):
        out = self._out_entry.get().strip()
        if not out or not Path(out).is_dir():
            self._logline("Pick an existing Output folder containing compressed videos + .json proofs first.", t.STATE["error"][1])
            return
        try:
            vmaf_floor = float(self._vmaf.get())
        except ValueError:
            self._logline("Min VMAF must be a number.", t.STATE["error"][1])
            return
        self._clean_btn.configure(state="disabled")
        self._logline(f"Verifying proofs in {out} (VMAF >= {vmaf_floor:.1f})…", t.TEXT_MUTED)

        def _preview():
            results = e.cleanup_verified_originals(out, vmaf_floor=vmaf_floor, dry_run=True)
            def _confirm():
                try:
                    self._clean_btn.configure(state="normal")
                    ready = [r for r in results if r.status == "dry_run_ok"]
                    rejected = [r for r in results if r.status == "rejected"]
                    already = [r for r in results if r.status == "already_removed"]
                    reclaim_gb = sum(r.saved_bytes for r in ready) / 1e9
                    self._logline(
                        f"Proof audit: {len(results)} proof(s) · {len(ready)} verified ready ({reclaim_gb:.2f} GB) · "
                        f"{len(already)} already removed · {len(rejected)} rejected",
                        t.TEXT_MAIN,
                    )
                    if not ready:
                        return
                    ok = messagebox.askyesno(
                        "Remove verified larger originals?",
                        f"{len(ready)} compressed file(s) passed output existence, smaller size, "
                        f"duration match, and VMAF >= {vmaf_floor:.1f}.\n\n"
                        f"Send the {len(ready)} larger original file(s) to the Recycle Bin "
                        f"to reclaim {reclaim_gb:.2f} GB?",
                        icon="warning",
                        parent=self,
                    )
                    if not ok:
                        self._logline("Cleanup cancelled — originals kept.", t.TEXT_MUTED)
                        return
                    self._clean_btn.configure(state="disabled")
                    def _execute():
                        done_res = e.cleanup_verified_originals(out, vmaf_floor=vmaf_floor, dry_run=False)
                        rem = sum(1 for r in done_res if r.status == "removed")
                        freed = sum(r.saved_bytes for r in done_res if r.status == "removed") / 1e9
                        def _finish():
                            self._clean_btn.configure(state="normal")
                            self._logline(f"Removed {rem} larger original(s) → Recycle Bin ({freed:.2f} GB reclaimed).", t.STATE["done"][1])
                        try:
                            self.after(0, _finish)
                        except Exception:
                            pass
                    threading.Thread(target=_execute, daemon=True).start()
                except Exception:
                    pass
            try:
                self.after(0, _confirm)
            except Exception:
                pass
        threading.Thread(target=_preview, daemon=True).start()

    def _pre_run_check(self, opts) -> bool:
        # Destructive batch action must be explicitly confirmed (Preview+Confirm+
        # Logging). Only when actually writing AND deleting originals.
        if opts.delete_original and not opts.dry_run:
            ok = messagebox.askyesno(
                "Delete originals after compression?",
                f"{len(self._files)} original file(s) will be sent to the Recycle Bin "
                f"after each is compressed and passes the VMAF {opts.vmaf_floor:.0f} quality check.\n\n"
                f"Files that fail the check keep their original. Continue?",
                icon="warning", parent=self)
            if not ok:
                self._logline("Cancelled — originals will be kept. Re-run to proceed.", t.TEXT_MUTED)
                return False
        return True

    def _collect_options(self):
        try:
            vmaf_floor = float(self._vmaf.get())
        except ValueError:
            self._logline("Min VMAF must be a number.", t.STATE["error"][1]); return None
        container = "mp4" if self._container.get() == "MP4" else "mkv"
        height_map = {"Keep original": None, "1080p": 1080, "720p": 720}
        fps_map = {"Keep original": None, "30 fps": 30.0, "24 fps": 24.0}
        audio_mode = "shrink_bloated" if self._audio_mode.get() == "Shrink raw/bloated audio" else "copy"
        policy = e.Policy(
            crf=int(self._crf.get()),
            encoder=self._enc_labels[self._encoder.get()],
            max_height=height_map[self._max_height.get()],
            max_fps=fps_map[self._max_fps.get()],
            audio_mode=audio_mode,
        )
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        # mirror relative to the common ancestor of every source's folder.
        input_root = self._resolve_input_root() if mirror else None
        return e.ProcessOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                                policy=policy, vmaf_floor=vmaf_floor, container=container,
                                dry_run=bool(self._dry.get()), delete_original=bool(self._del.get()),
                                skip_existing=bool(self._skip.get()))

    # -- queue submission (tool-specific) --------------------------------------
    def _build_submission(self, files: list[Path], opts: e.ProcessOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="video_compressor",
            tool_version="3",
            workflow_version="compress.v4",
            inputs=files,
            settings=asdict(opts),
            max_retries=1,
        )
        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "compressed":
                return ItemOutcome.completed(data, res.reason)
            if res.action in ("skipped", "dry-run"):
                return ItemOutcome.skipped(data, res.reason)
            retryable = res.detail in {"probe.timeout", "encode.timeout"}
            return ItemOutcome.failed(res.reason, retryable=retryable, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Video Compressor · {len(files)} file(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(self._result_from_record(item)),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="video_compressor"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        compressed = sum(result.action == "compressed" for result in results)
        skipped = sum(result.action in ("skipped", "dry-run") for result in results)
        failed = len(results) - compressed - skipped
        remaining = len(report.items) - len(payload.finished_items)
        saved_total = sum(
            result.before_mb - result.after_mb for result in results if result.action == "compressed"
        )
        self._done(
            compressed, skipped, failed, remaining, saved_total, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined",
                        detail="batch.quarantined")

    def _write_manifest(self, opts: e.ProcessOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-videos run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "compression_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "before_mb", "after_mb", "saved_pct",
                            "vmaf", "out_path", "original_removed", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.before_mb, r.after_mb, r.saved_pct,
                            r.vmaf, r.out_path, r.original_removed, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"compressed": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"compressed": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action == "compressed":
            vmaf = f"{res.vmaf:.1f}" if res.vmaf is not None else "unproven"
            extra = f"  {res.before_mb:.1f}→{res.after_mb:.1f} MB  (-{res.saved_pct:.0f}%)  VMAF {vmaf}"
            extra += "  🗑 original removed" if res.original_removed else ""
        elif res.action == "dry-run":
            # Honest: the saving shown is the unverified assess() heuristic.
            extra = f"  — est. -{res.saved_pct:.0f}% (unverified) — {res.reason}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, compressed, skipped, failed, remaining, saved_total, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = (f"compressed {compressed} · skipped {skipped} · failed {failed} · "
                   f"saved {saved_total:.0f} MB total")
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )

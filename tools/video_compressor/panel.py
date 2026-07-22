"""Video Compressor — the tool's UI. Thin over engine.py: collect files + options,
run the engine on a worker thread, stream results back to the UI via after().
No encoding logic lives here (that's engine.py); no UI logic lives there.
"""
from __future__ import annotations

import csv
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e


class VideoCompressorPanel(BaseBatchPanel):
    FILE_EXTS = e.VIDEO_EXTS
    FILE_LABEL = "video"
    FILES_ICON = Icons.VIDEO
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Analyze & Compress"

    def __init__(self, parent):
        super().__init__(parent)
        self._refresh_tools_hint()

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        # encoder + crf + vmaf row
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Encoder", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._encoder = ctk.CTkOptionMenu(row, values=["x265 (best quality)", "nvenc_hevc (GPU fast)", "handbrake"],
                                          width=180, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                          button_hover_color=t.NEUTRAL_HOVER)
        self._encoder.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="CRF (lower = higher quality)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._crf = ctk.CTkOptionMenu(row, values=["18", "20", "22", "24"], width=70,
                                      fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._crf.set("20"); self._crf.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Min VMAF", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._vmaf = c.entry(row, width=60); self._vmaf.insert(0, "92"); self._vmaf.grid(row=1, column=2, sticky="w", pady=(2, 0))
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
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))

    def _refresh_tools_hint(self):
        s = e.tools_status()
        missing = [k for k, v in s.items() if not v and k != "HandBrakeCLI"]
        if missing:
            self._tools_hint.configure(text=f"⚠ missing on PATH: {', '.join(missing)} — install ffmpeg/ffprobe")
        else:
            hb = "HandBrakeCLI ✓" if s["HandBrakeCLI"] else "HandBrakeCLI ✗ (optional)"
            self._tools_hint.configure(text=f"tools: ffmpeg ✓  ffprobe ✓  {hb}")

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
        enc_map = {"x265 (best quality)": "x265", "nvenc_hevc (GPU fast)": "nvenc_hevc", "handbrake": "handbrake"}
        policy = e.Policy(crf=int(self._crf.get()), encoder=enc_map[self._encoder.get()])
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        # mirror relative to the common ancestor of every source's folder.
        input_root = self._resolve_input_root() if mirror else None
        return e.ProcessOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                                policy=policy, vmaf_floor=vmaf_floor,
                                dry_run=bool(self._dry.get()), delete_original=bool(self._del.get()),
                                skip_existing=bool(self._skip.get()))

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.ProcessOptions):
        saved_total = 0.0; compressed = skipped = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "compressed":
                compressed += 1; saved_total += (res.before_mb - res.after_mb)
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, compressed, skipped, failed, saved_total, manifest)

    def _write_manifest(self, opts: e.ProcessOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-videos run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
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
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"compressed": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"compressed": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action == "compressed":
            extra = f"  {res.before_mb:.1f}→{res.after_mb:.1f} MB  (-{res.saved_pct:.0f}%)  VMAF {res.vmaf:.1f}"
            extra += "  🗑 original removed" if res.original_removed else ""
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, compressed, skipped, failed, saved_total, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"compressed {compressed} · skipped {skipped} · failed {failed} · "
                                     f"saved {saved_total:.0f} MB total")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)

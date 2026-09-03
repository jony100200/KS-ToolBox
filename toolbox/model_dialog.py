"""In-app AI Models Manager for KS ToolBox.

Provides a 1-click model download and management dialog, ensuring models are
always downloaded and organized directly inside the ToolBox's root `models/`
directory regardless of install drive (C:, D:, E:, etc.).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.engine_common import bundled_models_dir


@dataclass(frozen=True)
class ModelEntry:
    id: str
    name: str
    filename: str
    size_str: str
    description: str
    url: str
    is_archive: bool = False
    extract_dirname: str = ""

    def get_path(self, models_dir: Path) -> Path:
        if self.is_archive and self.extract_dirname:
            return models_dir / self.extract_dirname
        return models_dir / self.filename

    def is_installed(self, models_dir: Path) -> bool:
        path = self.get_path(models_dir)
        if self.is_archive and self.extract_dirname:
            exe_name = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
            return (path / exe_name).is_file()
        return path.is_file()


MODELS_CATALOG: list[ModelEntry] = [
    ModelEntry(
        id="yunet_face",
        name="YuNet Face Detector",
        filename="face_detection_yunet_2023mar.onnx",
        size_str="232 KB",
        description="Fast local face detector for Image Enhancer portrait repair and detail tuning.",
        url="https://github.com/opencv/opencv_zoo/raw/master/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ),
    ModelEntry(
        id="u2netp_mask",
        name="U2NetP Subject Mask",
        filename="u2netp.onnx",
        size_str="4.57 MB",
        description="Compact salient subject foreground segmentation (Image Enhancer & Alpha Doctor).",
        url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
    ),
    ModelEntry(
        id="realesrgan_ncnn",
        name="Real-ESRGAN Vulkan NCNN",
        filename="realesrgan-ncnn-vulkan-20220424-windows.zip",
        size_str="~48 MB",
        description="GPU-accelerated Vulkan upscaler for Image Rescale and Image Enhancer.",
        url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip",
        is_archive=True,
        extract_dirname="realesrgan-ncnn-20220424",
    ),
    ModelEntry(
        id="u2net_full",
        name="Full U2Net Matte",
        filename="u2net.onnx",
        size_str="176 MB",
        description="High-precision background removal for complex photographs (Alpha Doctor).",
        url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
    ),
]


def open_folder(path: Path) -> None:
    """Open a folder in the native file manager."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


class ModelManagerDialog(ctk.CTkToplevel):
    """Modal dialog allowing 1-click download of all optional micro-models into root models/."""

    def __init__(self, parent: ctk.CTk) -> None:
        super().__init__(parent)
        self.title("KS ToolBox — AI Models Manager")
        self.geometry("720x620")
        self.minsize(640, 520)
        self.configure(fg_color=t.BG_COLOR)

        self._models_dir = bundled_models_dir()
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._is_downloading = False

        self._build_ui()
        self._refresh_status()

        self.transient(parent)
        self.after(10, self.focus_force)

    def _build_ui(self) -> None:
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 10))

        title_label = ctk.CTkLabel(
            header,
            text="AI Models Manager",
            font=t.font(20, bold=True),
            text_color=t.TEXT_MAIN,
        )
        title_label.pack(anchor="w")

        dir_frame = ctk.CTkFrame(header, fg_color="transparent")
        dir_frame.pack(fill="x", pady=(4, 0))

        path_display = f"Target Directory: {self._models_dir}"
        ctk.CTkLabel(
            dir_frame,
            text=path_display,
            font=t.font(11),
            text_color=t.TEXT_MUTED,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        c.ghost_button(
            dir_frame,
            text="Open Folder",
            command=lambda: open_folder(self._models_dir),
            height=26,
            width=90,
        ).pack(side="right")

        # Scrollable area for model cards
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", corner_radius=0
        )
        self._scroll.pack(fill="both", expand=True, padx=20, pady=10)

        self._row_widgets: dict[str, dict] = {}
        for entry in MODELS_CATALOG:
            card = self._build_model_row(self._scroll, entry)
            card.pack(fill="x", pady=5)

        # Progress bar & status line
        self._progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._progress_frame.pack(fill="x", padx=20, pady=(4, 0))

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame,
            fg_color=t.CARD_BORDER,
            progress_color=t.ACCENT_BLUE,
            height=6,
        )
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x")

        self._status_label = ctk.CTkLabel(
            self._progress_frame,
            text="Ready. Models are stored locally in the Toolbox root folder.",
            font=t.font(11),
            text_color=t.TEXT_MUTED,
            anchor="w",
        )
        self._status_label.pack(fill="x", pady=(3, 0))

        # Bottom Action Bar
        action_bar = ctk.CTkFrame(self, fg_color=t.CARD_BG, corner_radius=t.RADIUS_CARD)
        action_bar.pack(fill="x", padx=20, pady=(10, 20))

        c.primary_button(
            action_bar,
            text="1-Click Download Recommended (4.8 MB)",
            command=self._download_recommended,
            height=36,
        ).pack(side="left", padx=14, pady=12)

        c.secondary_button(
            action_bar,
            text="Download All Missing",
            command=self._download_all_missing,
            height=36,
        ).pack(side="left", padx=(0, 14), pady=12)

        c.ghost_button(
            action_bar,
            text="Close",
            command=self.destroy,
            height=36,
            width=80,
        ).pack(side="right", padx=14, pady=12)

    def _build_model_row(self, parent, entry: ModelEntry) -> ctk.CTkFrame:
        row = ctk.CTkFrame(
            parent,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=t.BORDER_W,
            corner_radius=t.RADIUS_CARD,
        )

        left_frame = ctk.CTkFrame(row, fg_color="transparent")
        left_frame.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        title_line = ctk.CTkFrame(left_frame, fg_color="transparent")
        title_line.pack(fill="x")

        ctk.CTkLabel(
            title_line,
            text=entry.name,
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
        ).pack(side="left")

        ctk.CTkLabel(
            title_line,
            text=f"({entry.size_str})",
            font=t.font(11),
            text_color=t.TEXT_MUTED,
        ).pack(side="left", padx=(6, 0))

        pill = c.Pill(title_line, text="CHECKING...", state="idle")
        pill.pack(side="left", padx=(10, 0))

        desc_label = ctk.CTkLabel(
            left_frame,
            text=entry.description,
            font=t.font(11),
            text_color=t.TEXT_MUTED,
            wraplength=420,
            justify="left",
            anchor="w",
        )
        desc_label.pack(fill="x", pady=(2, 0))

        right_frame = ctk.CTkFrame(row, fg_color="transparent")
        right_frame.pack(side="right", padx=14, pady=10)

        action_btn = c.secondary_button(
            right_frame,
            text="Download",
            command=lambda e=entry: self._download_single_model(e),
            width=100,
            height=30,
        )
        action_btn.pack()

        self._row_widgets[entry.id] = {
            "pill": pill,
            "button": action_btn,
            "entry": entry,
        }
        return row

    def _refresh_status(self) -> None:
        for entry_id, widgets in self._row_widgets.items():
            entry: ModelEntry = widgets["entry"]
            pill: c.Pill = widgets["pill"]
            btn: ctk.CTkButton = widgets["button"]
            installed = entry.is_installed(self._models_dir)
            if installed:
                pill.set_state("INSTALLED", "pass")
                btn.configure(text="Re-download", state="normal" if not self._is_downloading else "disabled")
            else:
                pill.set_state("MISSING", "warn")
                btn.configure(text="Download", state="normal" if not self._is_downloading else "disabled")

    def _download_single_model(self, entry: ModelEntry) -> None:
        if self._is_downloading:
            return
        self._start_download_task([entry])

    def _download_recommended(self) -> None:
        if self._is_downloading:
            return
        recommended = [
            e for e in MODELS_CATALOG
            if e.id in {"yunet_face", "u2netp_mask"} and not e.is_installed(self._models_dir)
        ]
        if not recommended:
            messagebox.showinfo(
                "Already Installed",
                "Recommended micro-models (YuNet + U2NetP) are already installed and ready in:\n"
                f"{self._models_dir}",
                parent=self,
            )
            return
        self._start_download_task(recommended)

    def _download_all_missing(self) -> None:
        if self._is_downloading:
            return
        missing = [e for e in MODELS_CATALOG if not e.is_installed(self._models_dir)]
        if not missing:
            messagebox.showinfo(
                "All Models Ready",
                f"All models in the catalog are already installed in:\n{self._models_dir}",
                parent=self,
            )
            return
        self._start_download_task(missing)

    def _start_download_task(self, items: list[ModelEntry]) -> None:
        self._is_downloading = True
        self._refresh_status()
        self._progress_bar.set(0)

        def _worker():
            total_items = len(items)
            for idx, entry in enumerate(items, start=1):
                self._update_status(f"Downloading {entry.name} ({idx}/{total_items})...")
                ok, err = self._download_and_organize_entry(entry)
                if not ok:
                    self._update_status(f"Error downloading {entry.name}: {err}")
                    self.after(0, lambda msg=err: messagebox.showerror("Download Error", msg, parent=self))
                    break

            self._is_downloading = False
            self.after(0, self._on_task_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _update_status(self, msg: str, progress: float | None = None) -> None:
        def _apply():
            self._status_label.configure(text=msg)
            if progress is not None:
                self._progress_bar.set(progress)
        self.after(0, _apply)

    def _download_and_organize_entry(self, entry: ModelEntry) -> tuple[bool, str]:
        target_file = self._models_dir / entry.filename
        temp_file = self._models_dir / f"{entry.filename}.part"

        try:
            req = urllib.request.Request(entry.url, headers={"User-Agent": "KS-ToolBox/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                total_len = int(resp.headers.get("content-length", 0))
                downloaded = 0
                block_size = 64 * 1024
                with open(temp_file, "wb") as f:
                    while True:
                        chunk = resp.read(block_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_len > 0:
                            pct = downloaded / total_len
                            mb_down = downloaded / (1024 * 1024)
                            mb_tot = total_len / (1024 * 1024)
                            self._update_status(
                                f"Downloading {entry.name}: {mb_down:.1f} MB / {mb_tot:.1f} MB ({int(pct*100)}%)",
                                pct,
                            )

            if entry.is_archive and entry.extract_dirname:
                self._update_status(f"Extracting {entry.name} bundle...")
                extract_dest = self._models_dir / entry.extract_dirname
                extract_dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(temp_file, "r") as zf:
                    zf.extractall(extract_dest)

                # Check if archive extracted into an internal subfolder (e.g. realesrgan-ncnn-vulkan-20220424-windows)
                # and flatten if needed so binaries/models are directly in extract_dest
                exe_name = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
                if not (extract_dest / exe_name).is_file():
                    for sub in list(extract_dest.iterdir()):
                        if sub.is_dir() and (sub / exe_name).is_file():
                            for item in list(sub.iterdir()):
                                dest_item = extract_dest / item.name
                                if dest_item.exists():
                                    if dest_item.is_dir():
                                        shutil.rmtree(dest_item)
                                    else:
                                        dest_item.unlink()
                                shutil.move(str(item), str(extract_dest))
                            sub.rmdir()
                            break

                temp_file.unlink(missing_ok=True)
            else:
                temp_file.replace(target_file)

            return True, ""
        except Exception as exc:
            temp_file.unlink(missing_ok=True)
            return False, f"Failed to download {entry.name}: {exc}"

    def _on_task_finished(self) -> None:
        self._refresh_status()
        self._progress_bar.set(1.0)
        self._status_label.configure(
            text=f"Downloads complete. Models are organized inside {self._models_dir}"
        )
        messagebox.showinfo(
            "Models Updated",
            f"Model files have been updated and organized in:\n{self._models_dir}\n\n"
            "AI features in Image Enhancer, Image Rescale, and Alpha Doctor are ready to use.",
            parent=self,
        )

"""CustomTkinter queue/history view over the shell-owned JobQueue."""
from __future__ import annotations

import time

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.job_queue import JobQueue, QueueSnapshot
from toolbox.batch_core import JobState


_ACTIVE = {
    JobState.QUEUED, JobState.PREPARING, JobState.RUNNING,
    JobState.PAUSED, JobState.CANCELLING,
}


class QueuePanel(ctk.CTkFrame):
    def __init__(self, parent, queue: JobQueue):
        super().__init__(parent, fg_color=t.BG_COLOR)
        self._queue = queue
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._list = ctk.CTkScrollableFrame(self, fg_color=t.BG_COLOR, corner_radius=0)
        self._list.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(0, t.PAD_GRID))
        self._poll_job = self.after(0, self.refresh)

    def _build_header(self) -> None:
        card = c.Card(self, "Queue and History", icon=Icons.QUEUE)
        card.grid(row=0, column=0, sticky="ew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x")
        self._summary = ctk.CTkLabel(row, text="No queued work", text_color=t.TEXT_MAIN,
                                     font=t.font(12, bold=True))
        self._summary.pack(side="left")
        c.secondary_button(row, "Refresh history", self.refresh, width=130).pack(side="right")
        self._hint = ctk.CTkLabel(card.body,
                                  text="Jobs run one at a time to protect responsiveness and external-tool throughput.",
                                  text_color=t.TEXT_MUTED, font=t.font(10))
        self._hint.pack(anchor="w", pady=(6, 0))

    def refresh(self) -> None:
        try:
            self._queue.load_persisted_history()
            self._hint.configure(
                text="History is local. Completed records reload from the SQLite job store on demand."
            )
        except Exception as ex:  # noqa: BLE001 - visible degraded history
            self._hint.configure(text=f"History unavailable: {type(ex).__name__}: {ex}")
        self._render()
        if not getattr(self, "_poll_started", False):
            self._poll_started = True
            self._poll_job = self.after(250, self._poll)

    def _poll(self) -> None:
        if self.winfo_exists():
            self._render()
            self._poll_job = self.after(250, self._poll)

    def _render(self) -> None:
        if not self.winfo_exists():
            return
        for child in self._list.winfo_children():
            child.destroy()
        history = self._queue.history()
        active = sum(item.state in _ACTIVE for item in history)
        completed = sum(item.state in {JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS} for item in history)
        failed = sum(item.state is JobState.FAILED for item in history)
        self._summary.configure(text=f"active {active} · completed {completed} · failed {failed}")
        if not history:
            ctk.CTkLabel(self._list, text="No jobs yet. Open any tool, configure the work, and add it to the queue.",
                         text_color=t.TEXT_MUTED, font=t.font(12)).pack(anchor="w", padx=8, pady=12)
            return
        for snapshot in history[:100]:
            self._build_row(snapshot)

    def _build_row(self, snapshot: QueueSnapshot) -> None:
        row = ctk.CTkFrame(self._list, fg_color=t.CARD_BG, border_color=t.CARD_BORDER,
                           border_width=1, corner_radius=t.RADIUS_CARD)
        row.pack(fill="x", pady=(0, 8))
        top = ctk.CTkFrame(row, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text=snapshot.label, text_color=t.TEXT_MAIN,
                     font=t.font(12, bold=True)).pack(side="left")
        ctk.CTkLabel(top, text=snapshot.state.value.replace("_", " ").upper(),
                     text_color=self._state_color(snapshot.state), font=t.font(10, bold=True)).pack(side="right")

        middle = ctk.CTkFrame(row, fg_color="transparent")
        middle.pack(fill="x", padx=12)
        progress = ctk.CTkProgressBar(middle, height=6, fg_color=t.CARD_BORDER,
                                      progress_color=t.ACCENT_BLUE)
        progress.pack(side="left", fill="x", expand=True)
        ratio = snapshot.completed_items / snapshot.total_items if snapshot.total_items else 0
        progress.set(max(0.0, min(1.0, ratio)))
        ctk.CTkLabel(middle, text=f"{snapshot.completed_items}/{snapshot.total_items}",
                     text_color=t.TEXT_MUTED, font=t.mono(10), width=70).pack(side="left", padx=(8, 0))

        bottom = ctk.CTkFrame(row, fg_color="transparent")
        bottom.pack(fill="x", padx=12, pady=(4, 10))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(snapshot.submitted_at))
        detail = snapshot.detail or ("saved history" if snapshot.persisted else when)
        ctk.CTkLabel(bottom, text=detail, text_color=t.TEXT_MUTED,
                     font=t.font(10)).pack(side="left")
        if snapshot.persisted:
            return
        if snapshot.state is JobState.PAUSED:
            c.secondary_button(bottom, "Resume", lambda jid=snapshot.job_id: self._queue.resume(jid),
                               width=75).pack(side="right")
        elif snapshot.state in {JobState.QUEUED, JobState.PREPARING, JobState.RUNNING}:
            c.secondary_button(bottom, "Pause", lambda jid=snapshot.job_id: self._queue.pause(jid),
                               width=70).pack(side="right")
        if snapshot.state in _ACTIVE and snapshot.state is not JobState.CANCELLING:
            c.danger_button(bottom, "Cancel", lambda jid=snapshot.job_id: self._queue.cancel(jid),
                            width=70).pack(side="right", padx=(0, 6))

    @staticmethod
    def _state_color(state: JobState) -> str:
        if state in {JobState.COMPLETED, JobState.RUNNING}:
            return t.STATE["done"][1]
        if state in {JobState.QUEUED, JobState.PAUSED, JobState.CANCELLING,
                     JobState.COMPLETED_WITH_WARNINGS, JobState.RECOVERED}:
            return t.STATE["waiting"][1]
        if state is JobState.FAILED:
            return t.STATE["error"][1]
        return t.TEXT_MUTED

    def destroy(self) -> None:
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
        super().destroy()

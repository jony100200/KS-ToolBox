from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from toolbox.batch_core import ItemOutcome, JobDefinition, JobState
from toolbox.job_queue import JobQueue, QueueFinalization, QueueSubmission
from toolbox.sqlite_job_store import SQLiteJobStore


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.db = self.root / "queue.sqlite3"
        self.queue = JobQueue(lambda: SQLiteJobStore(self.db))

    def tearDown(self) -> None:
        self.queue.close()
        self._temp.cleanup()

    def _submission(self, name: str, execute, *, priority: int = 0, complete=None):
        path = self.root / f"{name}.dat"
        path.write_text(name, encoding="utf-8")
        definition = JobDefinition.create(
            tool_id=name,
            tool_version="1",
            workflow_version="1",
            inputs=[path],
            settings={"name": name},
        )
        return QueueSubmission(
            definition=definition,
            label=name,
            execute=lambda path, token: execute(path),
            classify=lambda result: ItemOutcome.completed({"value": str(result)}),
            validate_stored=lambda item: True,
            priority=priority,
            on_complete=complete,
        )

    def _wait(self, job_id: str, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.queue.snapshot(job_id)
            if snapshot and snapshot.state in {
                JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                JobState.FAILED, JobState.CANCELLED,
            }:
                return snapshot
            time.sleep(0.01)
        self.fail(f"job did not finish: {job_id}")

    def test_queue_is_lazy_and_completes_job(self) -> None:
        self.assertIsNone(self.queue._worker)
        submission = self._submission("simple", lambda path: path.name)
        job_id = self.queue.submit(submission)
        snapshot = self._wait(job_id)
        self.assertEqual(snapshot.state, JobState.COMPLETED)
        self.assertEqual(snapshot.completed_items, 1)

    def test_priority_orders_jobs_waiting_behind_active_work(self) -> None:
        gate = threading.Event()
        order: list[str] = []
        active = self._submission("active", lambda path: gate.wait(3) or path.name,
                                  complete=lambda report: order.append("active"))
        active_id = self.queue.submit(active)
        deadline = time.monotonic() + 2
        while self.queue.snapshot(active_id).state is not JobState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        low = self._submission("low", lambda path: path.name,
                               priority=1, complete=lambda report: order.append("low"))
        high = self._submission("high", lambda path: path.name,
                                priority=10, complete=lambda report: order.append("high"))
        low_id = self.queue.submit(low)
        high_id = self.queue.submit(high)
        gate.set()
        self._wait(active_id); self._wait(high_id); self._wait(low_id)
        self.assertEqual(order, ["active", "high", "low"])

    def test_cancel_queued_job_without_execution(self) -> None:
        gate = threading.Event()
        active_id = self.queue.submit(self._submission("block", lambda path: gate.wait(3) or path.name))
        calls: list[str] = []
        queued = self._submission("cancel", lambda path: calls.append(path.name) or path.name)
        queued_id = self.queue.submit(queued)
        self.assertTrue(self.queue.cancel(queued_id))
        gate.set()
        self._wait(active_id)
        snapshot = self._wait(queued_id)
        self.assertEqual(snapshot.state, JobState.CANCELLED)
        self.assertFalse(calls)

    def test_pause_holds_at_item_boundary_then_resumes(self) -> None:
        inputs = [self.root / "pause_a.dat", self.root / "pause_b.dat"]
        for path in inputs:
            path.write_text(path.stem, encoding="utf-8")
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def execute(path: Path, token) -> str:
            calls.append(path.name)
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return path.name

        definition = JobDefinition.create(
            tool_id="pause",
            tool_version="1",
            workflow_version="1",
            inputs=inputs,
            settings={"mode": "pause"},
        )
        job_id = self.queue.submit(QueueSubmission(
            definition=definition,
            label="pause",
            execute=execute,
            classify=lambda result: ItemOutcome.completed({"value": result}),
            validate_stored=lambda item: True,
        ))
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.queue.pause(job_id))
        release.set()
        time.sleep(0.1)
        self.assertEqual(calls, ["pause_a.dat"])
        self.assertEqual(self.queue.snapshot(job_id).state, JobState.PAUSED)
        self.assertTrue(self.queue.resume(job_id))
        self.assertEqual(self._wait(job_id).state, JobState.COMPLETED)
        self.assertEqual(calls, ["pause_a.dat", "pause_b.dat"])

    def test_subscriber_failure_is_isolated_and_history_loads(self) -> None:
        self.queue.subscribe(lambda snapshot: (_ for _ in ()).throw(RuntimeError("subscriber")))
        job_id = self.queue.submit(self._submission("history", lambda path: path.name))
        self.assertEqual(self._wait(job_id).state, JobState.COMPLETED)
        self.assertTrue(self.queue.subscriber_errors)

        reopened = JobQueue(lambda: SQLiteJobStore(self.db))
        try:
            loaded = reopened.load_persisted_history()
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].persisted)
        finally:
            reopened.close()

    def test_finalizer_warnings_are_visible_in_snapshot_and_completion(self) -> None:
        submission = self._submission("warning", lambda path: path.name)
        submission = QueueSubmission(
            **{
                **submission.__dict__,
                "finalize": lambda report: QueueFinalization(
                    {"report": report.job_id}, ("manifest write failed",)
                ),
            }
        )
        job_id = self.queue.submit(submission)
        snapshot = self._wait(job_id)
        completion = self.queue.completion(job_id)

        self.assertEqual(snapshot.state, JobState.COMPLETED_WITH_WARNINGS)
        self.assertIn("manifest write failed", snapshot.detail)
        self.assertIsNotNone(completion)
        self.assertEqual(completion.warnings, ("manifest write failed",))
        self.assertEqual(completion.value, {"report": job_id})


if __name__ == "__main__":
    unittest.main()

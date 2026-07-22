from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from toolbox.batch_core import (
    BatchRunner,
    CancellationToken,
    ItemOutcome,
    ItemState,
    JobDefinition,
    JobState,
    write_completion_report,
)
from toolbox.sqlite_job_store import SQLiteJobStore


class BatchCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.inputs = [self.root / name for name in ("one.dat", "bad.dat", "three.dat")]
        for path in self.inputs:
            path.write_text(path.stem, encoding="utf-8")
        self.db_path = self.root / "jobs.sqlite3"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _job(self, *, retries: int = 0) -> JobDefinition:
        return JobDefinition.create(
            tool_id="test.batch",
            tool_version="1",
            workflow_version="1",
            inputs=self.inputs,
            settings={"mode": "test"},
            max_retries=retries,
        )

    def test_bad_item_is_quarantined_without_stopping_batch(self) -> None:
        with SQLiteJobStore(self.db_path) as store:
            report = BatchRunner(store).run(
                self._job(),
                lambda path: (_ for _ in ()).throw(ValueError("malformed")) if path.name == "bad.dat" else path,
                lambda path: ItemOutcome.completed({"name": path.name}),
            )

        self.assertEqual(report.state, JobState.COMPLETED_WITH_WARNINGS)
        self.assertEqual(report.counts[ItemState.COMPLETED.value], 2)
        self.assertEqual(report.counts[ItemState.QUARANTINED.value], 1)
        self.assertIn("ValueError: malformed", report.items[1].details)

    def test_retryable_item_retries_only_that_item(self) -> None:
        attempts: dict[str, int] = {}

        def execute(path: Path) -> Path:
            attempts[path.name] = attempts.get(path.name, 0) + 1
            return path

        def classify(path: Path) -> ItemOutcome:
            if path.name == "bad.dat" and attempts[path.name] == 1:
                return ItemOutcome.failed("temporary", retryable=True)
            return ItemOutcome.completed({"name": path.name})

        with SQLiteJobStore(self.db_path) as store:
            report = BatchRunner(store).run(self._job(retries=1), execute, classify)

        self.assertEqual(report.state, JobState.COMPLETED)
        self.assertEqual(attempts, {"one.dat": 1, "bad.dat": 2, "three.dat": 1})
        self.assertEqual(report.items[1].attempts, 2)

    def test_cancelled_job_resumes_without_repeating_completed_items(self) -> None:
        first_calls: list[str] = []
        stop = threading.Event()
        token = CancellationToken(stop)

        def first_execute(path: Path) -> Path:
            first_calls.append(path.name)
            return path

        def stop_after_first(progress) -> None:
            if progress.completed_items == 1:
                stop.set()

        job = self._job()
        with SQLiteJobStore(self.db_path) as store:
            first = BatchRunner(store).run(
                job,
                first_execute,
                lambda path: ItemOutcome.completed({"name": path.name}),
                cancellation=token,
                on_progress=stop_after_first,
            )
        self.assertEqual(first.state, JobState.CANCELLED)
        self.assertEqual(first_calls, ["one.dat"])

        resumed_calls: list[str] = []
        with SQLiteJobStore(self.db_path) as store:
            resumed = BatchRunner(store).run(
                job,
                lambda path: resumed_calls.append(path.name) or path,
                lambda path: ItemOutcome.completed({"name": path.name}),
            )
        self.assertTrue(resumed.recovered)
        self.assertEqual(resumed.state, JobState.COMPLETED)
        self.assertEqual(resumed_calls, ["bad.dat", "three.dat"])

        with SQLiteJobStore(self.db_path) as store:
            reused = BatchRunner(store).run(
                job,
                lambda path: self.fail(f"completed job re-executed {path}"),
                lambda path: ItemOutcome.completed(),
                validate_stored=lambda item: True,
            )
        self.assertTrue(reused.reused)

    def test_mid_item_cancellation_defers_interrupted_item(self) -> None:
        token = CancellationToken()
        job = self._job()

        def interrupted(path: Path) -> Path:
            token.cancel()
            raise RuntimeError("owned worker stopped")

        with SQLiteJobStore(self.db_path) as store:
            cancelled = BatchRunner(store).run(
                job,
                interrupted,
                lambda path: ItemOutcome.completed({"name": path.name}),
                cancellation=token,
            )
        self.assertEqual(cancelled.state, JobState.CANCELLED)
        self.assertEqual(cancelled.items[0].state, ItemState.PENDING)
        self.assertIn("owned worker stopped", cancelled.items[0].details)

        calls: list[str] = []
        with SQLiteJobStore(self.db_path) as store:
            resumed = BatchRunner(store).run(
                job,
                lambda path: calls.append(path.name) or path,
                lambda path: ItemOutcome.completed({"name": path.name}),
            )
        self.assertTrue(resumed.recovered)
        self.assertEqual(calls, ["one.dat", "bad.dat", "three.dat"])

    def test_invalid_stored_result_reexecutes_only_that_item(self) -> None:
        job = self._job()
        with SQLiteJobStore(self.db_path) as store:
            BatchRunner(store).run(
                job,
                lambda path: path,
                lambda path: ItemOutcome.completed({"name": path.name}),
            )

        calls: list[str] = []
        with SQLiteJobStore(self.db_path) as store:
            report = BatchRunner(store).run(
                job,
                lambda path: calls.append(path.name) or path,
                lambda path: ItemOutcome.completed({"name": path.name}),
                validate_stored=lambda item: item.position != 1,
            )
        self.assertTrue(report.recovered)
        self.assertFalse(report.reused)
        self.assertEqual(calls, ["bad.dat"])

    def test_completion_report_is_atomic_and_machine_readable(self) -> None:
        with SQLiteJobStore(self.db_path) as store:
            report = BatchRunner(store).run(
                self._job(),
                lambda path: path,
                lambda path: ItemOutcome.skipped({"name": path.name}, "test skip"),
            )
        target = write_completion_report(report, self.root / "report.json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["counts"]["skipped"], 3)
        self.assertFalse(list(self.root.glob("*.part")))


if __name__ == "__main__":
    unittest.main()

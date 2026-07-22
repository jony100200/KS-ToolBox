"""Measure durable batch orchestration overhead without tool-processing cost.

This is intentionally a scheduler/checkpoint benchmark, not an image or codec
benchmark. It compares the direct-call lower bound with SQLite-checkpointed
execution and verifies that a completed job is reused without execution.
"""
from __future__ import annotations

import tempfile
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolbox.batch_core import BatchRunner, ItemOutcome, JobDefinition
from toolbox.sqlite_job_store import SQLiteJobStore

ITEMS = 1000


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        inputs = [root / f"item_{index:04d}.dat" for index in range(ITEMS)]
        for path in inputs:
            path.write_bytes(b"x")

        calls = 0

        def execute(path: Path) -> Path:
            nonlocal calls
            calls += 1
            return path

        started = time.perf_counter()
        for path in inputs:
            execute(path)
        direct_seconds = time.perf_counter() - started
        calls = 0

        started = time.perf_counter()
        job = JobDefinition.create(
            tool_id="benchmark.noop",
            tool_version="1",
            workflow_version="1",
            inputs=inputs,
            settings={"operation": "noop"},
        )
        definition_seconds = time.perf_counter() - started

        db_path = root / "jobs.sqlite3"
        started = time.perf_counter()
        with SQLiteJobStore(db_path) as store:
            report = BatchRunner(store).run(
                job,
                execute,
                lambda path: ItemOutcome.completed({"name": path.name}),
            )
        durable_seconds = time.perf_counter() - started
        assert calls == ITEMS and report.state.value == "completed"

        calls = 0
        started = time.perf_counter()
        with SQLiteJobStore(db_path) as store:
            reused = BatchRunner(store).run(
                job,
                execute,
                lambda path: ItemOutcome.completed({"name": path.name}),
                validate_stored=lambda item: True,
            )
        reuse_seconds = time.perf_counter() - started
        assert reused.reused and calls == 0

        db_bytes = sum(path.stat().st_size for path in root.glob("jobs.sqlite3*"))
        print(f"items                         : {ITEMS}")
        print(f"direct-call lower bound       : {direct_seconds * 1000:8.2f} ms")
        print(f"job identity (path+stat)      : {definition_seconds * 1000:8.2f} ms")
        print(f"durable SQLite execution      : {durable_seconds * 1000:8.2f} ms")
        print(f"durable throughput            : {ITEMS / durable_seconds:8.0f} items/s")
        print(f"completed-job reuse           : {reuse_seconds * 1000:8.2f} ms")
        print(f"checkpoint storage            : {db_bytes:8d} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

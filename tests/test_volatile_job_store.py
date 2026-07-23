from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from toolbox.batch_core import BatchRunner, ItemOutcome, JobDefinition, JobState
from toolbox.volatile_job_store import VolatileJobStore


class VolatileJobStoreTests(unittest.TestCase):
    def test_batch_completes_without_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "one.png", root / "two.png"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            definition = JobDefinition.create(
                tool_id="test.volatile", tool_version="1", workflow_version="1",
                inputs=[first, second], settings={},
            )
            with VolatileJobStore() as store:
                report = BatchRunner(store).run(
                    definition, lambda path: path,
                    lambda path: ItemOutcome.completed({"name": path.name}),
                )
            self.assertEqual(report.state, JobState.COMPLETED)
            self.assertEqual(report.counts["completed"], 2)


if __name__ == "__main__":
    unittest.main()

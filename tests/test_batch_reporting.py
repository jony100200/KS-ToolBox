from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from toolbox.batch_core import BatchReport, ItemRecord, ItemState, JobState
from toolbox.batch_reporting import BatchCompletionArtifacts, prepare_batch_completion


class BatchReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _report(self, *, reused: bool = False) -> BatchReport:
        now = time.time()
        return BatchReport(
            job_id="report-job",
            tool_id="report-tool",
            state=JobState.COMPLETED,
            items=(ItemRecord(0, "input.dat", ItemState.COMPLETED, 1,
                              data={"value": "decoded"}),),
            created_at=now,
            updated_at=now,
            reused=reused,
        )

    def test_prepares_typed_artifacts_and_atomic_report(self) -> None:
        manifest_path = self.root / "manifest.csv"

        def write_manifest(results: list[object]) -> str:
            manifest_path.write_text(str(results), encoding="utf-8")
            return str(manifest_path)

        finalized = prepare_batch_completion(
            self._report(),
            result_from_record=lambda item: item.data["value"],
            write_manifest=write_manifest,
            report_path=self.root / "completion.json",
        )

        self.assertIsInstance(finalized.value, BatchCompletionArtifacts)
        self.assertEqual(finalized.value.results, ("decoded",))
        self.assertEqual(finalized.value.manifest, str(manifest_path))
        self.assertTrue(Path(finalized.value.report_path).is_file())
        self.assertFalse(finalized.warnings)

    def test_manifest_failure_is_visible_and_reused_job_skips_manifest(self) -> None:
        calls = 0

        def fail_manifest(results: list[object]) -> str:
            nonlocal calls
            calls += 1
            raise OSError("read-only destination")

        failed = prepare_batch_completion(
            self._report(),
            result_from_record=lambda item: item.data,
            write_manifest=fail_manifest,
            report_path=self.root / "failed-manifest-report.json",
        )
        self.assertEqual(calls, 1)
        self.assertIn("manifest write failed", failed.warnings[0])
        self.assertTrue(Path(failed.value.report_path).is_file())

        reused = prepare_batch_completion(
            self._report(reused=True),
            result_from_record=lambda item: item.data,
            write_manifest=fail_manifest,
            report_path=self.root / "reused-report.json",
        )
        self.assertEqual(calls, 1)
        self.assertIsNone(reused.value.manifest)
        self.assertFalse(reused.warnings)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import main
from toolbox.sqlite_job_store import (
    SQLiteJobStore,
    _without_conflicting_sqlite_dll_dirs,
)


class StartupTests(unittest.TestCase):
    def test_main_sets_the_windows_stdlib_dll_directory(self) -> None:
        if os.name == "nt":
            self.assertTrue(
                (Path(sys.base_prefix) / "DLLs" / "sqlite3.dll").is_file()
            )

    def test_conflicting_sqlite_path_directory_is_removed(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            expected = root / "python" / "sqlite3.dll"
            conflicting_dir = root / "extension"
            expected.parent.mkdir()
            conflicting_dir.mkdir()
            expected.write_bytes(b"new")
            (conflicting_dir / "sqlite3.dll").write_bytes(b"old")
            filtered = _without_conflicting_sqlite_dll_dirs(
                os.pathsep.join([str(conflicting_dir), str(expected.parent)]),
                expected,
            )
            self.assertNotIn(str(conflicting_dir), filtered)
            self.assertIn(str(expected.parent), filtered)
        finally:
            shutil.rmtree(root)

    def test_sqlite_bootstrap_keeps_durable_store_available(self) -> None:
        """A Windows shell-extension SQLite DLL must not break the queue store."""
        from toolbox import sqlite_job_store

        if os.name == "nt":
            self.assertIsNotNone(sqlite_job_store._SQLITE_DLL_DIRECTORY)
        self.assertGreaterEqual(tuple(map(int, sqlite3.sqlite_version.split("."))), (3, 0, 0))
        root = Path(tempfile.mkdtemp())
        try:
            with SQLiteJobStore(root / "jobs.sqlite3"):
                pass
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()

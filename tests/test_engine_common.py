from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    find_output_collisions,
    run_cancellable_cmd,
    sha256_file,
)


class CancellableCommandTests(unittest.TestCase):
    def test_output_collisions_cover_shared_targets_and_selected_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "a" / "same.png"
            second = root / "b" / "same.png"
            protected = root / "out" / "protected.png"
            for source in (first, second, protected):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"source")

            def plan(source: Path):
                if source == first:
                    return [root / "out" / "shared.png", protected,
                            root / "out" / "duplicate.png", root / "out" / "duplicate.png"]
                if source == protected:
                    return [root / "out" / "other.png"]
                return [root / "out" / "shared.png", protected]

            collisions = find_output_collisions([first, second, protected], plan)
            self.assertEqual(len(collisions), 3)
            self.assertEqual(len(collisions[str((root / "out" / "shared.png").resolve())]), 2)
            self.assertEqual(
                collisions[str((root / "out" / "duplicate.png").resolve())],
                (str(first.resolve()),),
            )
            protected_owners = collisions[str(protected.resolve())]
            self.assertTrue(any(owner.startswith("selected input:") for owner in protected_owners))

    def test_streaming_sha256_matches_known_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.bin"
            source.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(source, chunk_size=1),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
            with self.assertRaises(ValueError):
                sha256_file(source, chunk_size=0)

    def test_success_captures_output(self) -> None:
        result = run_cancellable_cmd(
            [sys.executable, "-c", "print('ready')"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ready")

    def test_cancel_stops_owned_process_promptly(self) -> None:
        stop = threading.Event()
        timer = threading.Timer(0.15, stop.set)
        timer.start()
        started = time.perf_counter()
        try:
            with self.assertRaises(CommandCancelled):
                run_cancellable_cmd(
                    [sys.executable, "-c", "import time; time.sleep(10)"],
                    timeout=15,
                    cancelled=stop.is_set,
                    poll_seconds=0.05,
                )
        finally:
            timer.cancel()
        self.assertLess(time.perf_counter() - started, 3.0)

    def test_timeout_stops_owned_process(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            run_cancellable_cmd(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=0.15,
                poll_seconds=0.05,
            )


if __name__ == "__main__":
    unittest.main()

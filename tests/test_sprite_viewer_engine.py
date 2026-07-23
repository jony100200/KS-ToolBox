from __future__ import annotations

import importlib.util
import math
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.sprite_viewer import engine


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is optional")
class SpriteViewerEngineTests(unittest.TestCase):
    def test_grid_and_cell_requests_are_strictly_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            engine.grid_boxes(8, 8, 1_000_000, 1)
        with self.assertRaisesRegex(ValueError, "frame viewer limit"):
            engine.grid_boxes(200, 200, 101, 100)
        with self.assertRaisesRegex(ValueError, "frame viewer limit"):
            engine.cell_boxes(200, 200, 1, 1)
        self.assertEqual(
            engine.grid_boxes(8, 8, 2, 2),
            [(0, 0, 4, 4), (4, 0, 8, 4), (0, 4, 4, 8), (4, 4, 8, 8)],
        )

    def test_animated_and_folder_loads_enforce_frame_budgets(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = [
                Image.new("RGBA", (8, 8), (index * 30, 0, 0, 255))
                for index in range(3)
            ]
            animation = root / "three.gif"
            frames[0].save(
                animation,
                "GIF",
                save_all=True,
                append_images=frames[1:],
                duration=50,
            )
            limited = engine.load_frames(animation, max_frames=2)
            self.assertTrue(limited["error"])
            self.assertEqual(limited["error_type"], "input.too_many_frames")

            folder = root / "sequence"
            folder.mkdir()
            for index, frame in enumerate(frames):
                frame.save(folder / f"frame_{index}.png")
            folder_limited = engine.load_folder(folder, max_frames=2)
            self.assertTrue(folder_limited["error"])
            self.assertEqual(
                folder_limited["error_type"], "input.too_many_frames"
            )
            entry_limited = engine.load_folder(
                folder, max_frames=3, max_entries=2
            )
            self.assertTrue(entry_limited["error"])
            self.assertEqual(
                entry_limited["error_type"], "input.too_many_entries"
            )
            for frame in frames:
                frame.close()

    def test_prepare_source_is_cancellable_and_keeps_ui_metadata(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sheet.png"
            Image.new("RGBA", (16, 8), (255, 0, 0, 255)).save(source)
            cancelled = engine.prepare_source(
                source, "grid", rows=1, cols=2, cancelled=lambda: True
            )
            self.assertTrue(cancelled["error"])
            self.assertEqual(cancelled["error_type"], "operation.cancelled")

            result = engine.prepare_source(source, "grid", rows=1, cols=2)
            self.assertFalse(result["error"], result["details"])
            loaded = result["data"]
            try:
                self.assertEqual(loaded.kind, "sheet-grid")
                self.assertEqual(len(loaded.frames), 2)
                self.assertEqual(loaded.frames[0].size, (8, 8))
                self.assertEqual(len(loaded.boxes), 2)
            finally:
                for frame in loaded.frames:
                    frame.close()
                loaded.source_image.close()

    def test_invalid_gif_stage_is_removed_and_destination_is_preserved(self) -> None:
        from PIL import Image

        frame = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                destination = root / "preview.gif"
                destination.write_bytes(b"known-good-existing-output")

                def write_corrupt(_image, path, *_args, **_kwargs):
                    Path(path).write_bytes(b"not a GIF")

                with mock.patch.object(Image.Image, "save", write_corrupt):
                    result = engine.export_gif([frame], destination)

                self.assertTrue(result["error"])
                self.assertEqual(result["error_type"], "export.failed")
                self.assertEqual(
                    destination.read_bytes(), b"known-good-existing-output"
                )
                self.assertEqual(list(root.glob("*.part.gif")), [])
                self.assertEqual(list(root.glob(".*.part.gif")), [])
        finally:
            frame.close()

    def test_json_export_rejects_nonstandard_numbers_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "meta.json"
            destination.write_text('{"stable": true}\n', encoding="utf-8")
            result = engine.export_meta_json(
                {"score": math.nan},
                destination,
            )
            self.assertTrue(result["error"])
            self.assertEqual(result["error_type"], "export.failed")
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                '{"stable": true}\n',
            )
            self.assertEqual(list(root.glob("*.part.json")), [])
            self.assertEqual(list(root.glob(".*.part.json")), [])


@unittest.skipUnless(
    PIL_AVAILABLE and (os.name == "nt" or bool(os.environ.get("DISPLAY"))),
    "interactive Tk display unavailable",
)
class SpriteViewerPanelTests(unittest.TestCase):
    def test_custom_panel_delivers_worker_result_on_ui_thread(self) -> None:
        import customtkinter as ctk
        from PIL import Image
        from tools.sprite_viewer.panel import SpriteViewerPanel, _MODES

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sheet.png"
            with Image.new("RGBA", (32, 16), (255, 0, 0, 255)) as image:
                image.save(source)
            root = ctk.CTk()
            root.withdraw()
            panel = SpriteViewerPanel(root)
            panel.pack()
            try:
                panel._path.insert(0, str(source))
                panel._mode.set(_MODES[1])
                panel._on_mode_change(_MODES[1])
                panel._p_rows._entry.delete(0, "end")
                panel._p_rows._entry.insert(0, "1")
                panel._p_cols._entry.delete(0, "end")
                panel._p_cols._entry.insert(0, "2")
                panel._load()
                deadline = time.monotonic() + 5
                while panel._worker is not None and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                root.update()
                self.assertIsNone(panel._worker)
                self.assertEqual(len(panel._frames), 2)
                self.assertEqual(panel._kind, "sheet-grid")
            finally:
                panel.destroy()
                root.destroy()


if __name__ == "__main__":
    unittest.main()

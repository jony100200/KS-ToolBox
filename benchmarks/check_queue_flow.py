"""Real CustomTkinter → JobQueue → BatchRunner → QueuePanel integration check."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TERMINAL_STATES = {"completed", "completed_with_warnings", "failed", "cancelled"}


def _run_and_wait(app, panel, label: str, expected_items: int) -> str:
    panel._run()
    job_id = panel._active_job_id
    assert job_id, f"{label} did not submit a queue job"
    deadline = time.monotonic() + 10
    snapshot = None
    while time.monotonic() < deadline:
        app.update()
        snapshot = app._services.queue.snapshot(job_id)
        if snapshot and snapshot.state.value in _TERMINAL_STATES:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"queued {label} job did not finish")
    control_deadline = time.monotonic() + 2
    while panel._run_btn.cget("state") != "normal" and time.monotonic() < control_deadline:
        app.update()
        time.sleep(0.01)
    assert snapshot.state.value == "completed", snapshot
    assert snapshot.completed_items == expected_items, snapshot
    assert panel._run_btn.cget("state") == "normal", (
        f"{label} controls did not recover; callback errors="
        f"{app._services.queue.subscriber_errors}"
    )
    return job_id


def main() -> int:
    try:
        from PIL import Image
        from toolbox.discovery import discover
        from toolbox.engine_common import resolve_tool, run_cmd
        from toolbox.queue_panel import QueuePanel
        from toolbox.shell import ToolBoxShell
    except ImportError as ex:
        print(f"SKIP: queue UI integration dependency unavailable: {ex}")
        return 0

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        os.environ["KS_TOOLBOX_STATE_DIR"] = str(root / "state")
        first, second = root / "first.png", root / "second.png"
        Image.new("RGB", (64, 32), (20, 40, 60)).save(first)
        Image.new("RGB", (32, 64), (60, 40, 20)).save(second)

        app = ToolBoxShell(discover())
        app.withdraw()
        try:
            app._select("image_rescale")
            panel = app._panels["image_rescale"]
            panel._add([first, second])
            job_id = _run_and_wait(app, panel, "Image Rescale", 2)

            app._select("icon_normalizer")
            icon_panel = app._panels["icon_normalizer"]
            icon_panel._add([first, second])
            icon_job_id = _run_and_wait(app, icon_panel, "Icon Normalizer", 2)

            app._select("pixel_art")
            pixel_panel = app._panels["pixel_art"]
            pixel_panel._add([first, second])
            pixel_job_id = _run_and_wait(app, pixel_panel, "Pixel Art Converter", 2)

            video_job_id = None
            ffmpeg = resolve_tool("ffmpeg")
            if ffmpeg:
                sample = root / "sample.mp4"
                made = run_cmd([
                    ffmpeg, "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=15:duration=0.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(sample),
                ], timeout=30)
                assert made.returncode == 0 and sample.is_file(), made.stderr[-300:]
                app._select("video_compressor")
                video_panel = app._panels["video_compressor"]
                video_panel._add([sample])
                video_job_id = _run_and_wait(app, video_panel, "Video Compressor", 1)

            app._select(app.QUEUE_ID)
            app.update()
            queue_panel = app._panels[app.QUEUE_ID]
            assert isinstance(queue_panel, QueuePanel)
            assert any(item.job_id == job_id for item in app._services.queue.history())
            assert any(item.job_id == icon_job_id for item in app._services.queue.history())
            assert any(item.job_id == pixel_job_id for item in app._services.queue.history())
            if video_job_id:
                assert any(item.job_id == video_job_id for item in app._services.queue.history())
        finally:
            app._on_close()

    print("PASS: CustomTkinter submitted Image Rescale, Icon Normalizer, Pixel Art, "
          "and Video Compressor through shell queue; history UI rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

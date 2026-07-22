"""Real CustomTkinter → JobQueue → BatchRunner → QueuePanel integration check."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    try:
        from PIL import Image
        from toolbox.batch_core import JobState
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
            panel._run()
            job_id = panel._active_job_id
            assert job_id, "panel did not submit a queue job"

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.update()
                snapshot = app._services.queue.snapshot(job_id)
                if snapshot and snapshot.state in {
                    JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                    JobState.FAILED, JobState.CANCELLED,
                }:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("queued Image Rescale job did not finish")

            control_deadline = time.monotonic() + 2
            while (panel._run_btn.cget("state") != "normal"
                   and time.monotonic() < control_deadline):
                app.update()
                time.sleep(0.01)
            snapshot = app._services.queue.snapshot(job_id)
            assert snapshot.state is JobState.COMPLETED, snapshot
            assert snapshot.completed_items == 2
            assert panel._run_btn.cget("state") == "normal", (
                "panel controls did not recover; callback errors="
                f"{app._services.queue.subscriber_errors}"
            )

            app._select("icon_normalizer")
            icon_panel = app._panels["icon_normalizer"]
            icon_panel._add([first, second])
            icon_panel._run()  # default dry run
            icon_job_id = icon_panel._active_job_id
            assert icon_job_id, "Icon Normalizer did not submit a queue job"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.update()
                icon_snapshot = app._services.queue.snapshot(icon_job_id)
                if icon_snapshot and icon_snapshot.state in {
                    JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                    JobState.FAILED, JobState.CANCELLED,
                }:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("queued Icon Normalizer job did not finish")
            control_deadline = time.monotonic() + 2
            while (icon_panel._run_btn.cget("state") != "normal"
                   and time.monotonic() < control_deadline):
                app.update(); time.sleep(0.01)
            assert icon_snapshot.state is JobState.COMPLETED, icon_snapshot
            assert icon_snapshot.completed_items == 2
            assert icon_panel._run_btn.cget("state") == "normal"

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
                video_panel._run()  # default dry run: probe/plan only
                video_job_id = video_panel._active_job_id
                assert video_job_id, "Video Compressor did not submit a queue job"
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    app.update()
                    video_snapshot = app._services.queue.snapshot(video_job_id)
                    if video_snapshot and video_snapshot.state in {
                        JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                        JobState.FAILED, JobState.CANCELLED,
                    }:
                        break
                    time.sleep(0.01)
                else:
                    raise AssertionError("queued Video Compressor job did not finish")
                control_deadline = time.monotonic() + 2
                while (video_panel._run_btn.cget("state") != "normal"
                       and time.monotonic() < control_deadline):
                    app.update(); time.sleep(0.01)
                assert video_snapshot.state is JobState.COMPLETED, video_snapshot
                assert video_panel._run_btn.cget("state") == "normal"

            app._select(app.QUEUE_ID)
            app.update()
            queue_panel = app._panels[app.QUEUE_ID]
            assert isinstance(queue_panel, QueuePanel)
            assert any(item.job_id == job_id for item in app._services.queue.history())
            assert any(item.job_id == icon_job_id for item in app._services.queue.history())
            if video_job_id:
                assert any(item.job_id == video_job_id for item in app._services.queue.history())
        finally:
            app._on_close()

    print("PASS: CustomTkinter submitted Image Rescale, Icon Normalizer, and "
          "Video Compressor through shell queue; history UI rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

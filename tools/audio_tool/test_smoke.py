"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Two kinds of leg:
  * pure     — build_filters assembles the right `-af` for combos; the
               codec/muxer maps are correct. No deps.
  * ffmpeg   — synthesize a 3s sine wav with the bundled ffmpeg, then run the
               engine to (a) convert wav→mp3, (b) trim to 1s, (c) normalize;
               assert each output exists and is non-empty. Needs no pip audio
               deps. SKIPs cleanly if ffmpeg isn't resolvable.

Run standalone:  python -m tools.audio_tool.test_smoke
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Allow standalone `python test_smoke.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.audio_tool import engine as e  # noqa: E402


def test_pure() -> None:
    # --- muxer / codec maps ---
    assert e._muxer(".mp3") == "mp3" and e._muxer(".wav") == "wav"
    assert e._muxer(".flac") == "flac" and e._muxer(".aac") == "adts"
    assert e._muxer(".m4a") == "ipod" and e._muxer(".ogg") == "ogg"
    assert e._muxer(".opus") == "opus"
    assert e._muxer(".xyz") is None
    assert e._audio_codec(".mp3", "192k") == ["-c:a", "libmp3lame", "-b:a", "192k"]
    assert e._audio_codec(".wav", "192k") == ["-c:a", "pcm_s16le"]
    assert e._audio_codec(".flac", "192k") == ["-c:a", "flac"]
    assert e._audio_codec(".opus", "128k") == ["-c:a", "libopus", "-b:a", "128k"]

    # --- build_filters: single ops ---
    fin = e.build_filters(e.AudioOptions(fade_in=2), None)
    assert fin == "afade=t=in:st=0:d=2", fin
    assert e.build_filters(e.AudioOptions(normalize=True), None) == "loudnorm"
    assert e.build_filters(e.AudioOptions(), None) == ""  # no ops -> empty chain

    # --- build_filters: fade-out uses effective duration for st ---
    out = e.build_filters(e.AudioOptions(fade_out=3), 10.0)
    assert out == "afade=t=out:st=7:d=3", out

    # --- build_filters: fade + normalize combo, correct order + chaining ---
    combo = e.build_filters(
        e.AudioOptions(fade_in=1, fade_out=2, normalize=True), 10.0)
    assert combo == "afade=t=in:st=0:d=1,afade=t=out:st=8:d=2,loudnorm", combo
    print("PASS: pure — filter chain + codec/muxer maps assemble correctly.")


def test_ffmpeg() -> None:
    if not e.resolve_tool("ffmpeg"):
        print("SKIP: ffmpeg not found — audio-processing leg skipped."); return
    ff = e.resolve_tool("ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); out = tmp / "out"
        src = tmp / "tone.wav"
        make = [ff, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(src)]
        if e.run_cmd(make, timeout=120).returncode != 0:
            print("SKIP: could not synthesize a sample with this ffmpeg."); return

        # (a) convert wav -> mp3
        r = e.process(src, e.AudioOptions(target_format="mp3", out_root=out, dry_run=False))
        assert r.action == "processed", f"convert: {r.reason}"
        assert Path(r.out_path).is_file() and Path(r.out_path).stat().st_size > 0, "convert: empty"

        # (b) trim to first 1s (wav -> wav)
        r = e.process(src, e.AudioOptions(target_format="wav", trim_end="1",
                                          out_root=out / "trim", dry_run=False))
        assert r.action == "processed", f"trim: {r.reason}"
        assert Path(r.out_path).is_file() and Path(r.out_path).stat().st_size > 0, "trim: empty"
        dur = e.probe_duration(r.out_path)
        assert not dur["error"] and dur["data"] < 1.6, f"trim length off: {dur}"

        # (c) normalize (wav -> wav, loudnorm)
        r = e.process(src, e.AudioOptions(target_format="wav", normalize=True,
                                          out_root=out / "norm", dry_run=False))
        assert r.action == "processed", f"normalize: {r.reason}"
        assert Path(r.out_path).is_file() and Path(r.out_path).stat().st_size > 0, "normalize: empty"

        # (d) fade-out needs a probe; assert it runs end-to-end too
        r = e.process(src, e.AudioOptions(target_format="mp3", fade_out=1,
                                          out_root=out / "fade", dry_run=False))
        assert r.action == "processed", f"fade: {r.reason}"
        assert Path(r.out_path).is_file() and Path(r.out_path).stat().st_size > 0, "fade: empty"
    print("PASS: ffmpeg — convert, trim, normalize, fade produced real output.")


def main() -> int:
    test_pure()
    test_ffmpeg()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

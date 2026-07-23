"""Smoke test — deterministic safety always; real vtracer when available."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from toolbox.engine_common import CommandCancelled, find_output_collisions  # noqa: E402
from tools.to_svg import engine as e  # noqa: E402


def _with_fake_vtracer(fake, callback):
    original = sys.modules.get("vtracer")
    sys.modules["vtracer"] = SimpleNamespace(convert_image_to_svg_py=fake)
    try:
        return callback()
    finally:
        if original is None:
            sys.modules.pop("vtracer", None)
        else:
            sys.modules["vtracer"] = original


def _deterministic_checks(tmp: Path) -> None:
    src = tmp / "blocks.png"
    src.write_bytes(b"stub raster consumed by fake tracer")
    out = tmp / "out"
    opts = e.SvgOptions(out_root=out, colormode="color", dry_run=False)

    def successful(_src, destination, **_kwargs):
        Path(destination).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
            '<path d="M0 0 L64 0 L64 64 Z"/></svg>',
            encoding="utf-8",
        )

    result = _with_fake_vtracer(
        successful, lambda: e.process(src, opts)
    )
    assert result.action == "converted", result.reason
    destination = Path(result.out_path)
    assert destination.is_file() and result.artifact
    assert result.artifact["paths"] == 1
    assert e.validate_result(result, opts, expected_src=src)

    destination.write_bytes(b"X" * destination.stat().st_size)
    assert not e.validate_result(
        result, opts, expected_src=src
    ), "same-size corruption must invalidate reuse"
    result = _with_fake_vtracer(
        successful, lambda: e.process(src, opts)
    )
    assert e.validate_result(result, opts, expected_src=src)

    def broken(_src, destination, **_kwargs):
        Path(destination).write_text("<svg>partial</svg>", encoding="utf-8")
        raise RuntimeError("forced tracer failure")

    failed = _with_fake_vtracer(broken, lambda: e.process(src, opts))
    assert failed.action == "failed"
    assert not list(out.glob("*.part.svg")), "failed trace left a staged file"
    assert Path(result.out_path).is_file(), "failed trace replaced a valid SVG"

    cancelled = False

    def cancel_after_write(_src, destination, **_kwargs):
        nonlocal cancelled
        Path(destination).write_text("<svg/>", encoding="utf-8")
        cancelled = True

    try:
        _with_fake_vtracer(
            cancel_after_write,
            lambda: e.process(src, opts, cancelled=lambda: cancelled),
        )
    except CommandCancelled:
        pass
    else:
        raise AssertionError("SVG cancellation did not propagate")
    assert not list(out.glob("*.part.svg")), "cancelled trace left a staged file"

    def forbidden(_src, destination, **_kwargs):
        Path(destination).write_text(
            '<!DOCTYPE svg [<!ENTITY x "bad">]><svg>&x;</svg>',
            encoding="utf-8",
        )

    rejected = _with_fake_vtracer(forbidden, lambda: e.process(src, opts))
    assert rejected.action == "failed" and not list(out.glob("*.part.svg"))

    invalid = [
        e.SvgOptions(colormode="bogus"),
        e.SvgOptions(hierarchical="bogus"),
        e.SvgOptions(filter_speckle=-1),
        e.SvgOptions(color_precision=9),
        e.SvgOptions(path_precision=17),
    ]
    for bad in invalid:
        checked, reason = e.normalized_options(bad)
        assert checked is None and reason

    first = tmp / "one" / "same.png"
    second = tmp / "two" / "same.jpg"
    collisions = find_output_collisions(
        [first, second], lambda path: [e.plan_output(path, opts)]
    )
    assert len(collisions) == 1, "flat same-name SVG collision was not detected"


def _real_vtracer_check(tmp: Path) -> str:
    if importlib.util.find_spec("vtracer") is None:
        return "optional real vtracer leg skipped"
    if importlib.util.find_spec("PIL") is None:
        return "optional real vtracer leg skipped (Pillow missing)"
    from PIL import Image

    real_dir = tmp / "real"
    real_dir.mkdir()
    # Two solid colour blocks — a shape vtracer can actually trace into paths.
    img = Image.new("RGB", (64, 64), (240, 240, 240))
    for y in range(16, 48):
        for x in range(16, 48):
            img.putpixel((x, y), (200, 40, 60))
    src = real_dir / "blocks.png"
    img.save(src)
    out = real_dir / "out"
    opts = e.SvgOptions(out_root=out, colormode="color", dry_run=False)
    res = e.process(src, opts)
    assert res.action == "converted", f"expected converted, got {res.action}: {res.reason}"
    dst = Path(res.out_path)
    assert dst.is_file() and e.validate_result(
        res, opts, expected_src=src,
    )
    return f"real vtracer wrote {dst.name}"


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _deterministic_checks(tmp)
        real_status = _real_vtracer_check(tmp)
    print(
        "PASS: to_svg — strict options, collisions, staged cleanup, "
        f"SVG/hash validation, corruption repair, and cancellation verified; {real_status}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

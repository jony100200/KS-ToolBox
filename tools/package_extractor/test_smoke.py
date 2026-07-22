"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Pure legs (no deps): `is_within` rejects `../escape` and accepts a normal child;
`safe_name` sanitises. Full legs (stdlib only — no ffmpeg/Pillow needed):

  (1) Build a real .zip containing good files PLUS a crafted `../evil.txt`
      traversal entry -> extract -> assert the good files land under out_root and
      the traversal entry is REJECTED (recorded, never written outside).
  (2) Build a minimal fake .unitypackage (gzipped tar) with one
      `<guid>/pathname` (= Assets/foo/bar.txt) + `<guid>/asset` (bytes) ->
      extract -> assert Assets/foo/bar.txt is reconstructed with the right bytes.

Both assert the per-archive reports are written. Everything used is stdlib, so
nothing is skipped. customtkinter is needed only to import the package.

Run standalone:  python -m tools.package_extractor.test_smoke
"""
from __future__ import annotations

import gzip  # noqa: F401  (documents the .unitypackage compression; tarfile uses it)
import io
import json
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.package_extractor import engine as e  # noqa: E402


# ---------------------------------------------------------------------------
# pure legs
# ---------------------------------------------------------------------------

def test_safe_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert e.is_within(root, root / "child" / "x.txt"), "normal child must be inside"
        assert not e.is_within(root, root / ".." / "escape.txt"), "../escape must be rejected"
        assert not e.is_within(root, root.parent / "sibling.txt"), "sibling must be rejected"
    # safe_name strips separators / bad chars / reserved names, never empty.
    assert "/" not in e.safe_name("a/b") and "\\" not in e.safe_name("a\\b")
    assert "<" not in e.safe_name("a<b>c") and ">" not in e.safe_name("a<b>c")
    assert e.safe_name("") == "_"
    assert e.safe_name("CON").upper() != "CON", "Windows-reserved name must be defused"
    # a traversal component is rejected outright by _safe_relpath.
    assert e._safe_relpath("../evil.txt") is None
    assert e._safe_relpath("good/a.txt") == Path("good") / "a.txt"
    print("PASS: safe-path — is_within rejects ../escape & sibling, accepts child; safe_name sanitises.")


# ---------------------------------------------------------------------------
# full legs (stdlib)
# ---------------------------------------------------------------------------

def _build_malicious_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("good/a.txt", b"hello")
        zf.writestr("b.txt", b"world")
        zf.writestr("../evil.txt", b"pwned")          # zip-slip traversal


def test_zip_slip_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        zpath = tmp / "malicious.zip"
        _build_malicious_zip(zpath)
        out = tmp / "out"
        res = e.process(zpath, e.ExtractOptions(out_root=out, collision="rename", dry_run=False))

        dest = out / "malicious"
        assert res.action == "extracted", f"expected extracted, got {res.action}: {res.reason}"
        assert (dest / "good" / "a.txt").read_bytes() == b"hello", "good/a.txt missing or wrong"
        assert (dest / "b.txt").read_bytes() == b"world", "b.txt missing or wrong"
        # the traversal entry must NOT exist anywhere in or above out_root.
        assert not (dest / "evil.txt").exists(), "evil.txt leaked into out_root"
        assert not (out / "evil.txt").exists(), "evil.txt leaked beside out_root"
        assert not (tmp / "evil.txt").exists(), "evil.txt ESCAPED the output root!"
        assert res.rejected >= 1, "zip-slip entry was not recorded as rejected"
        # report written and it names the rejection.
        assert res.report_path and Path(res.report_path).is_file(), "report not written"
        report = json.loads(Path(res.report_path).read_text(encoding="utf-8"))
        assert any("evil" in r["name"] for r in report["rejected"]), "rejection not in report"
    print(f"PASS: zip-slip — good files extracted, '../evil.txt' REJECTED "
          f"({res.rejected} rejected, not written outside).")


def _build_unitypackage(path: Path, guid: str, unity_path: str, payload: bytes) -> None:
    with tarfile.open(path, "w:gz") as tf:               # a .unitypackage IS a gzipped tar
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add(f"{guid}/pathname", (unity_path + "\n").encode("utf-8"))
        add(f"{guid}/asset", payload)
        add(f"{guid}/asset.meta", b"fileFormatVersion: 2\n")   # ignored during rebuild


def test_unitypackage_reconstructed() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        upkg = tmp / "pkg.unitypackage"
        payload = b"the-real-asset-bytes-123"
        _build_unitypackage(upkg, "0123456789abcdef", "Assets/foo/bar.txt", payload)
        out = tmp / "out"
        res = e.process(upkg, e.ExtractOptions(out_root=out, dry_run=False))

        dest = out / "pkg"
        rebuilt = dest / "Assets" / "foo" / "bar.txt"
        assert res.action == "extracted", f"expected extracted, got {res.action}: {res.reason}"
        assert rebuilt.is_file(), f"reconstructed path missing: {rebuilt}"
        assert rebuilt.read_bytes() == payload, "reconstructed asset bytes wrong"
        assert res.written == 1, f"expected 1 written, got {res.written}"
        assert res.report_path and Path(res.report_path).is_file(), "report not written"
    print("PASS: unitypackage — 'Assets/foo/bar.txt' reconstructed from pathname with exact bytes.")


def main() -> int:
    test_safe_path()
    test_zip_slip_rejected()
    test_unitypackage_reconstructed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

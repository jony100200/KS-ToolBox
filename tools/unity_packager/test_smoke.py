"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Builds a tiny fake Unity project in a temp folder (stdlib only, nothing skipped) and checks:
  1. plan counts files/folders and reports a file without .meta as skipped;
  2. build writes a valid .unitypackage: `<guid>/pathname|asset|asset.meta`, ancestor
     folders included, hidden/`~`/.tmp items left out, payload bytes preserved;
  3. two builds of the same input are byte-identical (deterministic);
  4. collision policies: copy -> "(2)", error -> refused, overwrite -> replaced;
  5. unsafe/invalid input is refused: `..` traversal, Assets itself, output inside an
     included folder, duplicate GUIDs, wrong extension;
  6. cancellation leaves neither a package nor a `.part` file.

Run standalone:  python -m tools.unity_packager.test_smoke
"""
from __future__ import annotations

import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.unity_packager import engine as e  # noqa: E402


def _meta(guid: str, folder: bool = False) -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\n" + ("folderAsset: yes\n" if folder else "") + "\n"


def _write(path: Path, text: str, guid: str | None = None, folder: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not folder:
        path.write_text(text, encoding="utf-8")
    else:
        path.mkdir(parents=True, exist_ok=True)
    if guid:
        path.with_name(path.name + ".meta").write_text(_meta(guid, folder), encoding="utf-8")


def _project(root: Path) -> None:
    prod = root / "Assets" / "Pub" / "Prod"
    _write(root / "Assets" / "Pub", "", "a" * 32, folder=True)
    _write(prod, "", "b" * 32, folder=True)
    _write(prod / "Hello.cs", "class Hello {}", "c" * 32)
    _write(prod / "Sub", "", "d" * 32, folder=True)
    _write(prod / "Sub" / "Data.txt", "payload-bytes", "e" * 32)
    _write(prod / "NoMeta.txt", "no meta here")                 # skipped: no .meta
    _write(prod / ".hidden.txt", "hidden", "f" * 32)            # ignored by Unity
    _write(prod / "Backup~", "", "1" * 32, folder=True)         # ignored by Unity
    _write(prod / "scratch.tmp", "tmp", "2" * 32)               # ignored by Unity


def _entries(package: Path) -> dict[str, dict[str, bytes]]:
    out: dict[str, dict[str, bytes]] = {}
    with tarfile.open(package, "r:gz") as tar:
        for member in tar.getmembers():
            guid, _, kind = member.name.partition("/")
            out.setdefault(guid, {})[kind] = tar.extractfile(member).read()
    return out


def test_everything() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "Proj"
        _project(root)
        out = Path(td) / "out" / "Prod.unitypackage"
        opts = e.PackageOptions(root, ("Assets/Pub/Prod",), out, collision="copy")

        planned = e.plan(opts)
        assert not planned["error"], planned
        built = planned["data"]
        assert (built.files, built.folders) == (2, 3), (built.files, built.folders)   # Pub, Prod, Sub
        assert any("NoMeta.txt" in s for s in built.skipped), built.skipped
        assert planned["degraded"] is True
        print("PASS: plan — counts, ancestor folders, skipped item reported.")

        result = e.build(opts)
        assert not result["error"], result
        package = Path(result["data"]["path"])
        entries = _entries(package)
        names = {v["pathname"].decode().strip() for v in entries.values()}
        assert names == {"Assets/Pub", "Assets/Pub/Prod", "Assets/Pub/Prod/Hello.cs",
                         "Assets/Pub/Prod/Sub", "Assets/Pub/Prod/Sub/Data.txt"}, names
        assert entries["e" * 32]["asset"] == b"payload-bytes"
        assert "asset" not in entries["b" * 32] and "asset.meta" in entries["b" * 32]   # folders carry no payload
        print("PASS: build — valid package layout, payload preserved, junk excluded.")

        first = result["data"]["sha256"]
        second = e.build(e.PackageOptions(root, opts.includes, out, collision="overwrite"))
        assert second["data"]["sha256"] == first, "same input must give identical bytes"
        print("PASS: deterministic — identical sha256 across builds.")

        copy = e.build(opts)
        assert Path(copy["data"]["path"]).name == "Prod (2).unitypackage", copy
        refused = e.build(e.PackageOptions(root, opts.includes, out, collision="error"))
        assert refused["error"] and refused["error_type"] == "output-exists"
        print("PASS: collision policies — copy, error, overwrite.")

        bad = [
            e.PackageOptions(root, ("Assets/../Library",), out),
            e.PackageOptions(root, ("Assets",), out),
            e.PackageOptions(root, ("Assets/Pub/Prod",), root / "Assets" / "Pub" / "Prod" / "x.unitypackage"),
            e.PackageOptions(root, ("Assets/Pub/Prod",), Path(td) / "wrong.zip"),
            e.PackageOptions(root, (), out),
        ]
        for case in bad:
            assert e.plan(case)["error"], case
        print("PASS: invalid input — traversal, Assets root, output inside include, extension, empty.")

        _write(root / "Assets" / "Pub" / "Prod" / "Dup.txt", "dup", "c" * 32)           # same GUID as Hello.cs
        dup = e.plan(opts)
        assert dup["error"] and "duplicate GUID" in dup["details"], dup
        print("PASS: duplicate GUID refused.")

        (root / "Assets" / "Pub" / "Prod" / "Dup.txt").unlink()
        (root / "Assets" / "Pub" / "Prod" / "Dup.txt.meta").unlink()
        cancelled_out = Path(td) / "cancel" / "C.unitypackage"
        res = e.build(e.PackageOptions(root, opts.includes, cancelled_out), cancelled=lambda: True)
        assert res["error"] and res["error_type"] == "cancelled", res
        assert not cancelled_out.exists() and not cancelled_out.with_name("C.unitypackage.part").exists()
        print("PASS: cancellation leaves no package and no .part file.")


def main() -> int:
    try:
        test_everything()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

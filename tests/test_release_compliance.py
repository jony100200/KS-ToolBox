from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmarks.build_release_compliance import (
    _atomic_write,
    _validate_spdx_references,
    classify_ffmpeg_license,
    discover_distribution_names,
    require_supported_bundled_ffmpeg,
)


class ReleaseComplianceTests(unittest.TestCase):
    def test_windows_build_script_is_ascii_and_release_requirements_are_pinned(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        # Windows PowerShell 5.1 reads a BOM-free script through the legacy
        # code page. Keeping this entry point ASCII prevents punctuation from
        # turning into a parser error on the supported Windows build host.
        root.joinpath("build.ps1").read_bytes().decode("ascii")
        for requirement_file in ("requirements.txt", "requirements-build.txt"):
            entries = [
                line.partition("#")[0].strip()
                for line in root.joinpath(requirement_file).read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            entries = [entry for entry in entries if entry]
            self.assertTrue(entries, requirement_file)
            self.assertTrue(
                all("==" in entry for entry in entries),
                f"{requirement_file} contains an unpinned release dependency",
            )

    def test_ffmpeg_configuration_selects_exact_redistribution_license(self) -> None:
        self.assertEqual(
            classify_ffmpeg_license("--enable-gpl --enable-version3 --enable-libx265"),
            "GPL-3.0-or-later",
        )
        self.assertEqual(
            classify_ffmpeg_license("--enable-gpl --enable-libx264"),
            "GPL-2.0-or-later",
        )
        self.assertEqual(
            classify_ffmpeg_license("--enable-version3"),
            "LGPL-3.0-or-later",
        )
        with self.assertRaisesRegex(RuntimeError, "must not be distributed"):
            classify_ffmpeg_license("--enable-gpl --enable-nonfree")

    def test_portable_release_rejects_non_lgpl_ffmpeg(self) -> None:
        require_supported_bundled_ffmpeg({"license": "LGPL-3.0-or-later"})
        with self.assertRaisesRegex(RuntimeError, "must remain LGPL"):
            require_supported_bundled_ffmpeg({"license": "GPL-3.0-or-later"})

    def test_actual_toc_modules_map_to_distribution_owners(self) -> None:
        payload = (
            [
                (
                    "customtkinter.windows",
                    r"C:\venv\Lib\site-packages\customtkinter\windows.py",
                    "PYMODULE",
                ),
                (
                    "PIL._imaging",
                    r"C:\venv\Lib\site-packages\PIL\_imaging.pyd",
                    "EXTENSION",
                ),
                (
                    "pyi_rth_pkgutil",
                    (
                        r"C:\venv\Lib\site-packages\PyInstaller\hooks\rthooks"
                        r"\pyi_rth_pkgutil.py"
                    ),
                    "PYSOURCE",
                ),
                ("os", r"C:\Python\Lib\os.py", "PYMODULE"),
            ],
        )
        with tempfile.TemporaryDirectory() as temp:
            toc = Path(temp) / "Analysis-00.toc"
            toc.write_text(repr(payload), encoding="utf-8")
            names = discover_distribution_names(
                toc,
                package_map={
                    "customtkinter": ["customtkinter"],
                    "PIL": ["Pillow"],
                },
            )
        self.assertEqual(names, {"customtkinter", "Pillow"})

    def test_unowned_site_package_module_fails_closed(self) -> None:
        payload = (
            [
                (
                    "mystery",
                    r"C:\venv\Lib\site-packages\mystery.py",
                    "PYMODULE",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as temp:
            toc = Path(temp) / "Analysis-00.toc"
            toc.write_text(repr(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "no distribution metadata"):
                discover_distribution_names(toc, package_map={})

    def test_pyinstaller_hook_name_outside_pyinstaller_directory_fails(self) -> None:
        payload = (
            [
                (
                    "pyi_rth_mystery",
                    r"C:\venv\Lib\site-packages\mystery\pyi_rth_mystery.py",
                    "PYSOURCE",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as temp:
            toc = Path(temp) / "Analysis-00.toc"
            toc.write_text(repr(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "pyi_rth_mystery"):
                discover_distribution_names(toc, package_map={})

    def test_atomic_text_replaces_complete_content_without_staging_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "SBOM.spdx.json"
            target.write_text("old", encoding="utf-8")
            _atomic_write(target, '{"spdxVersion":"SPDX-2.3"}\n')
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"spdxVersion":"SPDX-2.3"}\n',
            )
            self.assertEqual(list(root.glob("*.part")), [])
            self.assertEqual(list(root.glob(".*.part")), [])

    def test_spdx_graph_rejects_unknown_relationship_targets(self) -> None:
        document = {
            "spdxVersion": "SPDX-2.3",
            "packages": [{"SPDXID": "SPDXRef-app"}],
            "relationships": [
                {
                    "spdxElementId": "SPDXRef-app",
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": "SPDXRef-missing",
                }
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "unknown element"):
            _validate_spdx_references(document)
        document["relationships"][0].update(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-app",
            }
        )
        _validate_spdx_references(document)


if __name__ == "__main__":
    unittest.main()

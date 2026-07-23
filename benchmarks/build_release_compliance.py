"""Build and verify release licensing artifacts from the actual PyInstaller TOC.

The release gate is deliberately strict: a bundled Python distribution without
an audited SPDX expression aborts packaging. Outputs are written atomically into
the release folder:

* ``SBOM.spdx.json`` — SPDX 2.3 package inventory.
* ``DEPENDENCY_MANIFEST.json`` — machine-readable component/file evidence.
* ``RELEASE_COMPONENTS.md`` — human-readable release decision table.
* ``THIRD_PARTY_NOTICES.md`` and exact license texts.

Run after PyInstaller:

    python -m benchmarks.build_release_compliance \
        --dist "dist/KS ToolBox" \
        --analysis-toc "build/KS ToolBox/Analysis-00.toc" \
        --require-ffmpeg
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from toolbox import __version__


ROOT = Path(__file__).resolve().parents[1]
MAX_NOTICE_BYTES = 2 * 1024 * 1024
REQUIRED_BUNDLED_FFMPEG_LICENSE = "LGPL-3.0-or-later"


@dataclass(frozen=True)
class PackagePolicy:
    spdx: str
    download: str
    notice_required: bool = True


# Audited runtime distributions only. If a new direct or transitive package
# reaches the PyInstaller TOC, the build fails until its exact terms are added.
PACKAGE_POLICIES = {
    "customtkinter": PackagePolicy("MIT", "https://github.com/TomSchimansky/CustomTkinter"),
    "darkdetect": PackagePolicy("BSD-3-Clause", "https://github.com/albertosottile/darkdetect"),
    "numpy": PackagePolicy("BSD-3-Clause", "https://github.com/numpy/numpy"),
    "packaging": PackagePolicy(
        "Apache-2.0 OR BSD-2-Clause",
        "https://github.com/pypa/packaging",
    ),
    "pillow": PackagePolicy("HPND", "https://github.com/python-pillow/Pillow"),
    "send2trash": PackagePolicy(
        "BSD-3-Clause",
        "https://github.com/arsenetar/send2trash",
    ),
}


def normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_toc(value: Any) -> Iterable[tuple[str, str, str]]:
    if (
        isinstance(value, tuple)
        and len(value) == 3
        and all(isinstance(item, str) for item in value)
    ):
        yield value
        return
    if isinstance(value, (tuple, list)):
        for child in value:
            yield from _walk_toc(child)


def discover_distribution_names(
    analysis_toc: Path,
    package_map: dict[str, list[str]] | None = None,
) -> set[str]:
    """Map bundled site-package module paths to installed distributions."""
    payload = ast.literal_eval(analysis_toc.read_text(encoding="utf-8"))
    mapping = (
        importlib.metadata.packages_distributions()
        if package_map is None
        else package_map
    )
    discovered: set[str] = set()
    unresolved: set[str] = set()
    for name, source, _kind in _walk_toc(payload):
        normalized_source = source.replace("/", "\\").lower()
        if "\\site-packages\\" not in normalized_source:
            continue
        top_level = re.split(r"[.\\]", name, maxsplit=1)[0]
        if top_level.endswith(("-info", "-data")):
            continue
        owners = mapping.get(top_level, [])
        if owners:
            discovered.update(owners)
        elif (
            top_level.startswith("pyi_rth_")
            and "\\pyinstaller\\hooks\\rthooks\\" in normalized_source
        ):
            # These modules belong to PyInstaller itself but are intentionally
            # absent from packages_distributions(). PyInstaller is inventoried
            # separately as the bootloader/build component below.
            continue
        elif ".dist-info\\" not in normalized_source:
            unresolved.add(top_level)
    if unresolved:
        raise RuntimeError(
            "bundled site-package modules have no distribution metadata: "
            + ", ".join(sorted(unresolved))
        )
    return discovered


def classify_ffmpeg_license(configuration: str) -> str:
    """Derive FFmpeg's license expression from its configure flags."""
    flags = set(configuration.split())
    if "--enable-nonfree" in flags:
        raise RuntimeError("FFmpeg is --enable-nonfree and must not be distributed")
    if "--enable-gpl" in flags:
        return (
            "GPL-3.0-or-later"
            if "--enable-version3" in flags
            else "GPL-2.0-or-later"
        )
    return (
        "LGPL-3.0-or-later"
        if "--enable-version3" in flags
        else "LGPL-2.1-or-later"
    )


def inspect_ffmpeg(executable: Path) -> dict[str, str]:
    completed = subprocess.run(
        [str(executable), "-version"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=(0x08000000 if os.name == "nt" else 0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{executable.name} -version failed: {completed.stderr.strip()}"
        )
    lines = completed.stdout.splitlines()
    if not lines:
        raise RuntimeError(f"{executable.name} returned no version information")
    match = re.match(r"ffmpeg version (\S+)", lines[0])
    if match is None:
        raise RuntimeError(f"could not parse FFmpeg version line: {lines[0]}")
    configuration = next(
        (line.partition(":")[2].strip() for line in lines if line.startswith("configuration:")),
        "",
    )
    if not configuration:
        raise RuntimeError("FFmpeg did not report its build configuration")
    version = match.group(1)
    commit = re.search(r"-g([0-9a-f]{7,40})(?:-|$)", version)
    return {
        "version": version,
        "license": classify_ffmpeg_license(configuration),
        "configuration": configuration,
        "version_line": lines[0],
        "source": (
            f"https://github.com/FFmpeg/FFmpeg/tree/{commit.group(1)}"
            if commit else "https://github.com/FFmpeg/FFmpeg"
        ),
        "binary_provider": "https://github.com/BtbN/FFmpeg-Builds/releases",
    }


def require_supported_bundled_ffmpeg(info: dict[str, str]) -> None:
    """Reject a portable runtime that is not the approved LGPL build class."""
    if info.get("license") != REQUIRED_BUNDLED_FFMPEG_LICENSE:
        raise RuntimeError(
            "bundled FFmpeg must remain LGPL-3.0-or-later; audit before release: "
            + str(info.get("license", "unknown"))
        )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.part"
    )
    try:
        shutil.copyfile(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _copy_distribution_notices(
    distribution: importlib.metadata.Distribution,
    destination: Path,
) -> list[str]:
    copied: list[str] = []
    for entry in distribution.files or ():
        basename = Path(str(entry)).name.upper()
        if not basename.startswith(("LICENSE", "COPYING", "NOTICE")):
            continue
        source = Path(distribution.locate_file(entry))
        if not source.is_file() or source.stat().st_size > MAX_NOTICE_BYTES:
            continue
        target_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(entry))
        target = destination / target_name
        _atomic_copy(source, target)
        copied.append(target.name)
    return sorted(set(copied))


def _component(
    name: str,
    version: str,
    license_expression: str,
    *,
    ships: bool = True,
    modified: bool = False,
    notice_required: bool = True,
    source_required: bool = False,
    download: str = "NOASSERTION",
    decision: str = "Include",
) -> dict[str, Any]:
    return {
        "component": name,
        "version": version,
        "license": license_expression,
        "ships_with_app": ships,
        "modified": modified,
        "notice_required": notice_required,
        "source_required": source_required,
        "commercially_usable": "Verified",
        "decision": decision,
        "download_location": download,
    }


def _spdx_id(name: str) -> str:
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-")


def _validate_spdx_references(document: dict[str, Any]) -> None:
    """Validate the bounded SPDX graph we generate (not the full SPDX schema)."""
    if document.get("spdxVersion") != "SPDX-2.3":
        raise RuntimeError("release SBOM must use SPDX-2.3")
    package_ids = [package.get("SPDXID") for package in document.get("packages", ())]
    if not package_ids or any(not isinstance(value, str) for value in package_ids):
        raise RuntimeError("release SBOM contains an invalid package identifier")
    if len(package_ids) != len(set(package_ids)):
        raise RuntimeError("release SBOM contains duplicate package identifiers")
    known_ids = set(package_ids)
    known_ids.add("SPDXRef-DOCUMENT")
    for relationship in document.get("relationships", ()):
        source = relationship.get("spdxElementId")
        target = relationship.get("relatedSpdxElement")
        if source not in known_ids or target not in known_ids:
            raise RuntimeError(
                f"release SBOM relationship references an unknown element: "
                f"{source!r} -> {target!r}"
            )


def _created_timestamp() -> str:
    source_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = (
        datetime.fromtimestamp(int(source_epoch), tz=timezone.utc)
        if source_epoch
        else datetime.now(tz=timezone.utc)
    )
    return when.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def generate_release_compliance(
    dist: Path,
    analysis_toc: Path,
    *,
    require_ffmpeg: bool,
) -> dict[str, Any]:
    dist = dist.resolve()
    if not dist.is_dir():
        raise FileNotFoundError(f"release folder not found: {dist}")
    if not analysis_toc.is_file():
        raise FileNotFoundError(f"PyInstaller analysis TOC not found: {analysis_toc}")
    app_binary = next(
        (candidate for candidate in (dist / "KS ToolBox.exe", dist / "KS ToolBox")
         if candidate.is_file()),
        None,
    )
    if app_binary is None:
        raise FileNotFoundError("release executable not found")

    licenses_dir = dist / "licenses"
    _atomic_copy(ROOT / "LICENSE", licenses_dir / "KS-ToolBox-MIT.txt")
    _atomic_copy(ROOT / "THIRD_PARTY_NOTICES.md", dist / "THIRD_PARTY_NOTICES.md")
    _atomic_copy(
        ROOT / "licenses" / "Font-Awesome-LICENSE.txt",
        licenses_dir / "Font-Awesome-LICENSE.txt",
    )

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError(f"Python license not found: {python_license}")
    _atomic_copy(python_license, licenses_dir / "Python-LICENSE.txt")

    components = [
        _component(
            "KS ToolBox",
            __version__,
            "MIT",
            notice_required=True,
            download="https://github.com/jony100200/KS-ToolBox",
        ),
        _component(
            "Python",
            ".".join(map(str, sys.version_info[:3])),
            "PSF-2.0",
            download="https://www.python.org/downloads/source/",
        ),
        _component(
            "Tcl/Tk",
            f"{getattr(__import__('tkinter'), 'TclVersion', 'unknown')}",
            "TCL",
            download="https://www.tcl.tk/software/tcltk/download.html",
        ),
        _component(
            "Font Awesome Free font",
            "6.x",
            "OFL-1.1",
            download="https://github.com/FortAwesome/Font-Awesome/tree/6.x",
        ),
    ]

    pyinstaller = importlib.metadata.distribution("pyinstaller")
    components.append(
        _component(
            "PyInstaller bootloader",
            pyinstaller.version,
            "GPL-2.0-or-later WITH Bootloader-exception",
            download="https://github.com/pyinstaller/pyinstaller",
        )
    )
    _copy_distribution_notices(pyinstaller, licenses_dir / "PyInstaller")

    bundled_distributions = discover_distribution_names(analysis_toc)
    for distribution_name in sorted(
        bundled_distributions, key=lambda value: normalize_distribution(value)
    ):
        normalized = normalize_distribution(distribution_name)
        policy = PACKAGE_POLICIES.get(normalized)
        if policy is None:
            raise RuntimeError(
                f"Unknown license policy for bundled distribution "
                f"{distribution_name!r}; do not ship until audited"
            )
        distribution = importlib.metadata.distribution(distribution_name)
        notices = _copy_distribution_notices(
            distribution,
            licenses_dir / re.sub(r"[^A-Za-z0-9_.-]+", "-", distribution_name),
        )
        if policy.notice_required and not notices:
            raise RuntimeError(
                f"{distribution_name} requires a notice but no license/notice "
                "file was found in its installed metadata"
            )
        components.append(
            _component(
                distribution.metadata.get("Name") or distribution_name,
                distribution.version,
                policy.spdx,
                notice_required=policy.notice_required,
                download=policy.download,
            )
        )

    ffmpeg = dist / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    ffprobe = dist / "bin" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if ffmpeg.exists() != ffprobe.exists():
        raise RuntimeError("release must contain both ffmpeg and ffprobe or neither")
    ffmpeg_info: dict[str, str] | None = None
    if ffmpeg.is_file():
        ffmpeg_info = inspect_ffmpeg(ffmpeg)
        require_supported_bundled_ffmpeg(ffmpeg_info)
        _atomic_copy(
            ROOT / "licenses" / "FFmpeg-COPYING.LGPLv3",
            licenses_dir / "FFmpeg-COPYING.LGPLv3",
        )
        _atomic_write(
            licenses_dir / "FFmpeg-BUILD-AND-SOURCE.txt",
            "\n".join(
                (
                    ffmpeg_info["version_line"],
                    f"License: {ffmpeg_info['license']}",
                    f"Binary provider: {ffmpeg_info['binary_provider']}",
                    f"Corresponding FFmpeg source: {ffmpeg_info['source']}",
                    "",
                    "Build configuration:",
                    ffmpeg_info["configuration"],
                    "",
                    "When publishing the binaries, keep equivalent no-charge access",
                    "to corresponding source and clear directions beside the download.",
                    "",
                )
            ),
        )
        components.append(
            _component(
                "FFmpeg and ffprobe",
                ffmpeg_info["version"],
                ffmpeg_info["license"],
                source_required=True,
                download=ffmpeg_info["source"],
            )
        )
    elif require_ffmpeg:
        raise FileNotFoundError("portable release requires bundled ffmpeg and ffprobe")

    files = []
    evidence_files = [app_binary]
    if ffmpeg.is_file():
        evidence_files.extend((ffmpeg, ffprobe))
    font = next(
        iter((dist / "_internal" / "assets").rglob("fa-solid-900.ttf")),
        None,
    )
    if font is None:
        raise FileNotFoundError("bundled Font Awesome font not found")
    evidence_files.append(font)
    for path in evidence_files:
        files.append(
            {
                "path": path.relative_to(dist).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    app_hash = next(item["sha256"] for item in files if item["path"] == app_binary.name)
    manifest = {
        "schema": "ks-release-components.v1",
        "generated_at": _created_timestamp(),
        "application_version": __version__,
        "components": components,
        "files": files,
        "ffmpeg": ffmpeg_info,
    }
    _atomic_write(
        dist / "DEPENDENCY_MANIFEST.json",
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )

    app_spdx = _spdx_id(f"KS ToolBox-{__version__}")
    packages = []
    relationships = []
    for component in components:
        identifier = _spdx_id(
            f"{component['component']}-{component['version']}"
        )
        packages.append(
            {
                "name": component["component"],
                "SPDXID": identifier,
                "versionInfo": component["version"],
                "downloadLocation": component["download_location"],
                "filesAnalyzed": False,
                "licenseConcluded": component["license"],
                "licenseDeclared": component["license"],
                "copyrightText": "NOASSERTION",
            }
        )
        if component["component"] != "KS ToolBox":
            relationships.append(
                {
                    "spdxElementId": app_spdx,
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": identifier,
                }
            )
    relationships.insert(
        0,
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": app_spdx,
        },
    )
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"KS-ToolBox-{__version__}",
        "documentNamespace": (
            "https://github.com/jony100200/KS-ToolBox/spdx/"
            f"{__version__}/{app_hash[:16]}"
        ),
        "creationInfo": {
            "created": manifest["generated_at"],
            "creators": ["Tool: KS release compliance generator"],
        },
        "packages": packages,
        "relationships": relationships,
    }
    _validate_spdx_references(sbom)
    _atomic_write(
        dist / "SBOM.spdx.json",
        json.dumps(sbom, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )

    lines = [
        "# Release Components",
        "",
        "| Component | Version | Licence | Ships with app? | Modified? | "
        "Notice required? | Source required? | Commercially usable? | Decision |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for component in components:
        lines.append(
            "| {component} | {version} | {license} | {ships} | {modified} | "
            "{notice} | {source} | {commercial} | {decision} |".format(
                component=component["component"],
                version=component["version"],
                license=component["license"],
                ships="Yes" if component["ships_with_app"] else "No",
                modified="Yes" if component["modified"] else "No",
                notice="Yes" if component["notice_required"] else "No",
                source="Yes" if component["source_required"] else "No",
                commercial=component["commercially_usable"],
                decision=component["decision"],
            )
        )
    lines.extend(
        (
            "",
            "Generated from the actual PyInstaller analysis. Unknown bundled "
            "distributions fail the build.",
            "",
        )
    )
    _atomic_write(dist / "RELEASE_COMPONENTS.md", "\n".join(lines))

    # Parse our own outputs so a truncated write cannot pass the build.
    json.loads((dist / "DEPENDENCY_MANIFEST.json").read_text(encoding="utf-8"))
    persisted_sbom = json.loads(
        (dist / "SBOM.spdx.json").read_text(encoding="utf-8")
    )
    _validate_spdx_references(persisted_sbom)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--analysis-toc", type=Path, required=True)
    parser.add_argument("--require-ffmpeg", action="store_true")
    args = parser.parse_args(argv)
    manifest = generate_release_compliance(
        args.dist,
        args.analysis_toc,
        require_ffmpeg=args.require_ffmpeg,
    )
    print(
        f"Release compliance OK: {len(manifest['components'])} components, "
        f"{len(manifest['files'])} hashed release files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fast, dependency-free documentation gate for KS ToolBox.

Run from the repository root:
    python -m benchmarks.check_docs

Checks local Markdown links, requires one guide per discovered first-party tool,
and rejects a small set of stale product claims from current operator/developer
documents. Historical records are intentionally outside that wording check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "bin", "build", "dist", "models"}
LINK_RE = re.compile(r"!?\[[^]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
CURRENT_DOCS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "ADDING_A_TOOL.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "BATCH_CORE.md",
    ROOT / "docs" / "MODULE_SYSTEM.md",
    ROOT / "docs" / "PERFORMANCE_BUDGETS.md",
    ROOT / "docs" / "RECOVERY_MODEL.md",
    ROOT / "docs" / "UI_GUIDELINES.md",
)
STALE_CURRENT_PHRASES = (
    "16 batch workflows",
    "16 of 17 tools",
    "Clean Cutout",
)


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def local_target(source: Path, target: str) -> Path | None:
    target = target.split("#", maxsplit=1)[0]
    if not target or "://" in target or target.startswith(("mailto:", "#")):
        return None
    return (source.parent / target).resolve()


def check_links(files: list[Path]) -> list[str]:
    failures: list[str] = []
    for source in files:
        text = source.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            resolved = local_target(source, target)
            if resolved is not None and not resolved.exists():
                failures.append(
                    f"{source.relative_to(ROOT)} links to missing {target!r}"
                )
    return failures


def check_tool_guides() -> list[str]:
    tool_dirs = sorted(path.parent for path in (ROOT / "tools").glob("*/tool.py"))
    return [
        f"{tool_dir.relative_to(ROOT)} has tool.py but no README.md"
        for tool_dir in tool_dirs
        if not tool_dir.joinpath("README.md").is_file()
    ]


def check_current_wording() -> list[str]:
    failures: list[str] = []
    for path in CURRENT_DOCS:
        text = path.read_text(encoding="utf-8")
        for phrase in STALE_CURRENT_PHRASES:
            if phrase in text:
                failures.append(f"{path.relative_to(ROOT)} retains stale phrase {phrase!r}")
    return failures


def main() -> int:
    files = markdown_files()
    failures = check_links(files) + check_tool_guides() + check_current_wording()
    if failures:
        print("Documentation check failed:")
        print("\n".join(f"  - {failure}" for failure in failures))
        return 1
    print(f"PASS: documentation — {len(files)} Markdown files, local links and tool guides valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import unittest
from dataclasses import dataclass

from toolbox.catalog import (
    CATEGORIES,
    category_by_id,
    search_tools,
    tools_in_category,
    validate_catalog,
)
from toolbox.discovery import discover
from toolbox.tool import ToolMeta


@dataclass(frozen=True)
class _Tool:
    meta: ToolMeta

    def build_panel(self, parent, services):  # pragma: no cover - metadata stub
        raise NotImplementedError


def _tool(
    tool_id: str,
    title: str,
    category: str,
    subtitle: str = "",
) -> _Tool:
    return _Tool(ToolMeta(tool_id, title, "", category, subtitle))


class ToolCatalogTests(unittest.TestCase):
    def test_every_discovered_tool_has_one_known_nonempty_category(self) -> None:
        tools = discover().all()
        validate_catalog(tools)
        self.assertEqual(len(tools), 19)
        grouped_ids = [
            tool.meta.id
            for category in CATEGORIES
            for tool in tools_in_category(tools, category.id)
        ]
        self.assertEqual(len(grouped_ids), len(set(grouped_ids)))
        self.assertEqual(set(grouped_ids), {tool.meta.id for tool in tools})
        self.assertTrue(
            all(tools_in_category(tools, category.id) for category in CATEGORIES)
        )

    def test_search_matches_title_subtitle_identifier_and_category(self) -> None:
        tools = [
            _tool("video_compressor", "Video Compressor", "video_audio"),
            _tool(
                "alpha_doctor",
                "Alpha Doctor",
                "images",
                "Remove difficult backgrounds",
            ),
            _tool("dataset_manager", "Dataset Manager", "files_data"),
        ]
        self.assertEqual(
            [tool.meta.id for tool in search_tools(tools, "video comp")],
            ["video_compressor"],
        )
        self.assertEqual(
            [tool.meta.id for tool in search_tools(tools, "background")],
            ["alpha_doctor"],
        )
        self.assertEqual(
            [tool.meta.id for tool in search_tools(tools, "files")],
            ["dataset_manager"],
        )
        self.assertEqual(search_tools(tools, "no such utility"), [])
        self.assertEqual(search_tools(tools, "  "), [])

    def test_exact_title_ranks_before_category_and_subtitle_matches(self) -> None:
        tools = [
            _tool("other", "Other Tool", "images", "Alpha Doctor helper"),
            _tool("alpha_doctor", "Alpha Doctor", "images"),
        ]
        self.assertEqual(
            [tool.meta.id for tool in search_tools(tools, "alpha doctor")],
            ["alpha_doctor", "other"],
        )

    def test_unknown_categories_fail_closed(self) -> None:
        broken = _tool("broken", "Broken", "unknown")
        with self.assertRaisesRegex(ValueError, "unknown tool category"):
            category_by_id("unknown")
        with self.assertRaisesRegex(ValueError, "broken"):
            validate_catalog([broken])
        with self.assertRaisesRegex(ValueError, "unknown tool category"):
            search_tools([broken], "broken")


if __name__ == "__main__":
    unittest.main()

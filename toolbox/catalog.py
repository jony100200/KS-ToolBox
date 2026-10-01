"""Deterministic tool catalog and search primitives.

The registry owns which tools exist. This module owns only how first-party tools
are grouped and found in the presentation. It has no widget, filesystem, queue,
or engine dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from toolbox.icons import Icons
from toolbox.tool import Tool


@dataclass(frozen=True)
class ToolCategory:
    id: str
    title: str
    description: str
    icon: str


CATEGORIES = (
    ToolCategory(
        "images",
        "Images",
        "Clean, resize, stylize, vectorize, and present image assets.",
        Icons.GRID,
    ),
    ToolCategory(
        "video_audio",
        "Video & Audio",
        "Compress, convert, split, and normalize media in reliable batches.",
        Icons.VIDEO,
    ),
    ToolCategory(
        "game_assets",
        "Game Assets",
        "Prepare sprites, materials, textures, and tiles for production.",
        Icons.LAYERS,
    ),
    ToolCategory(
        "files_data",
        "Files & Data",
        "Audit collections, extract packages, and prepare training datasets.",
        Icons.FOLDER,
    ),
)

_CATEGORY_BY_ID = {category.id: category for category in CATEGORIES}


def category_by_id(category_id: str) -> ToolCategory:
    try:
        return _CATEGORY_BY_ID[category_id]
    except KeyError as ex:
        raise ValueError(f"unknown tool category: {category_id!r}") from ex


def validate_catalog(tools: Iterable[Tool]) -> None:
    """Fail visibly when a discovered tool cannot be placed in the catalog."""
    invalid = sorted(
        tool.meta.id for tool in tools if tool.meta.category not in _CATEGORY_BY_ID
    )
    if invalid:
        raise ValueError(
            "tools use unknown categories: " + ", ".join(invalid)
        )


def tools_in_category(tools: Iterable[Tool], category_id: str) -> list[Tool]:
    category_by_id(category_id)
    return sorted(
        (tool for tool in tools if tool.meta.category == category_id),
        key=lambda tool: tool.meta.title.casefold(),
    )


def search_tools(tools: Iterable[Tool], query: str) -> list[Tool]:
    """Return stable, relevance-ranked matches for every whitespace term."""
    normalized = " ".join(query.casefold().split())
    if not normalized:
        return []
    terms = normalized.split()
    ranked: list[tuple[int, str, Tool]] = []
    for tool in tools:
        category = category_by_id(tool.meta.category)
        title = tool.meta.title.casefold()
        identifier = tool.meta.id.replace("_", " ").casefold()
        category_text = category.title.casefold()
        subtitle = tool.meta.subtitle.casefold()
        haystack = f"{title} {identifier} {category_text} {subtitle}"
        if not all(term in haystack for term in terms):
            continue
        if title == normalized:
            score = 0
        elif title.startswith(normalized):
            score = 1
        elif all(term in title for term in terms):
            score = 2
        elif all(term in identifier for term in terms):
            score = 3
        elif all(term in category_text for term in terms):
            score = 4
        else:
            score = 5
        ranked.append((score, title, tool))
    return [tool for _score, _title, tool in sorted(ranked)]

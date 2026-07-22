"""The LEGO contract — what makes something a ToolBox tool.

A tool is a self-contained block: it knows its identity and builds its own
panel into a parent frame. The shell knows nothing about any tool's internals;
it only lists them and asks the selected one to render. Add a tool = register
one Tool; the shell does not change. (CodingPrinciples: small interface, one
purpose, minimal coupling.)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import customtkinter as ctk


@dataclass(frozen=True)
class ToolMeta:
    id: str                       # stable slug, e.g. "video_compressor"
    title: str                    # sidebar label, e.g. "Video Compressor"
    icon: str                     # FontAwesome glyph or emoji
    subtitle: str = ""            # one-line description
    os_support: tuple[str, ...] = ("windows", "linux", "macos")


@runtime_checkable
class Tool(Protocol):
    """Every tool implements this. build_panel is called lazily — only when the
    user opens the tool — so nothing idle is ever constructed."""
    meta: ToolMeta

    def build_panel(self, parent: ctk.CTkFrame) -> ctk.CTkBaseClass:
        """Construct and return the tool's UI, parented under `parent`."""
        ...


class ToolRegistry:
    """Explicit registry — tools are registered, never auto-discovered by magic."""

    def __init__(self) -> None:
        self._tools: list[Tool] = []

    def register(self, tool: Tool) -> None:
        if any(t.meta.id == tool.meta.id for t in self._tools):
            raise ValueError(f"duplicate tool id: {tool.meta.id}")
        self._tools.append(tool)

    def all(self) -> list[Tool]:
        return list(self._tools)

    def get(self, tool_id: str) -> Tool | None:
        return next((t for t in self._tools if t.meta.id == tool_id), None)

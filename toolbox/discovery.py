"""Plugin auto-discovery — the heart of "many tools, one UI".

Every folder under tools/ that exposes a module-level `TOOL` becomes a tab.
Drop a tool folder in, it appears; remove it, it's gone. No edits to the shell
or main — that's what makes the ToolBox extensible (and public-contributable).
"""
from __future__ import annotations

import importlib
import pkgutil
import sys

from toolbox.tool import ToolRegistry


def discover(package: str = "tools") -> ToolRegistry:
    reg = ToolRegistry()
    pkg = importlib.import_module(package)
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if not info.ispkg:
            continue
        # A single broken plugin (bad contribution, import-time error) must not
        # take down the whole ToolBox — skip it with a visible warning. Tools are
        # expected to keep heavy/optional deps out of import (lazy in build_panel),
        # so a well-formed tool with an uninstalled dep still registers fine.
        try:
            module = importlib.import_module(f"{package}.{info.name}")
            tool = getattr(module, "TOOL", None)
        except Exception as ex:                 # noqa: BLE001 — isolate plugin failures
            print(f"[toolbox] skipped tool '{info.name}': {type(ex).__name__}: {ex}", file=sys.stderr)
            continue
        if tool is not None:
            reg.register(tool)
    return reg

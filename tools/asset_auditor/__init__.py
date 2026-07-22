"""Asset Auditor plugin. Exposing `TOOL` is the entire contract with the
ToolBox — discovery finds it, the shell shows it. Nothing else is required.
"""
from .tool import AssetAuditorTool

TOOL = AssetAuditorTool()

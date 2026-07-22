"""Alpha Doctor plugin. Exposing `TOOL` is the entire contract with the ToolBox —
discovery finds it, the shell shows it. Nothing else is required.
"""
from .tool import AlphaDoctorTool

TOOL = AlphaDoctorTool()

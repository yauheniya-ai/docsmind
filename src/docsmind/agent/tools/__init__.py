"""The Mind's tools."""

from __future__ import annotations

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput, ToolRegistry
from docsmind.agent.tools.delegate import DelegateTool
from docsmind.agent.tools.documents import ListDocumentsTool, OutlineTool, ReadPagesTool
from docsmind.agent.tools.search import GrepTool, SearchTool

__all__ = ["Tool", "ToolContext", "ToolOutput", "ToolRegistry", "build_registry"]


def build_registry() -> ToolRegistry:
    """Every tool the main executor can call. Subagents get a subset (see
    `agent/subagents/specs.py`) and never `delegate`."""
    return ToolRegistry(
        [
            SearchTool(),
            GrepTool(),
            ReadPagesTool(),
            OutlineTool(),
            ListDocumentsTool(),
            DelegateTool(),
        ]
    )

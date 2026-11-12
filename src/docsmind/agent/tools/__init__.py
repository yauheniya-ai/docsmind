"""The Mind's tools."""

from __future__ import annotations

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput, ToolRegistry
from docsmind.agent.tools.delegate import DelegateTool
from docsmind.agent.tools.documents import ListDocumentsTool, OutlineTool, ReadPagesTool
from docsmind.agent.tools.search import GrepTool, SearchTool
from docsmind.agent.tools.web import FetchUrlTool, WebSearchTool

__all__ = ["Tool", "ToolContext", "ToolOutput", "ToolRegistry", "build_registry"]


def build_registry() -> ToolRegistry:
    """Every tool. The main executor gets all but the `subagent_only` ones (the web tools);
    subagents get the subset their spec lists (see `agent/subagents/specs.py`) and never `delegate`."""
    return ToolRegistry(
        [
            SearchTool(),
            GrepTool(),
            ReadPagesTool(),
            OutlineTool(),
            ListDocumentsTool(),
            DelegateTool(),
            WebSearchTool(),
            FetchUrlTool(),
        ]
    )

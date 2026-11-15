"""The Mind's tools."""

from __future__ import annotations

from docsmind.agent.tools.base import Tool, ToolContext, ToolOutput, ToolRegistry
from docsmind.agent.tools.change import LoadTableTool, PageVisionTool
from docsmind.agent.tools.change_table import (
    AddRowTool,
    ArchiveRowTool,
    CheckVersionsTool,
    FindChangelogTool,
    GetRowTool,
    GetTableTool,
    RestoreRowTool,
    ScanDocumentsTool,
    UpdateRowTool,
    VerifyFindingsTool,
    WriteReportTool,
)
from docsmind.agent.tools.delegate import DelegateTool
from docsmind.agent.tools.documents import ListDocumentsTool, OutlineTool, ReadPagesTool
from docsmind.agent.tools.files import CreateFileTool
from docsmind.agent.tools.search import GrepTool, SearchTool
from docsmind.agent.tools.web import ReadUrlTool, WebSearchTool

__all__ = ["Tool", "ToolContext", "ToolOutput", "ToolRegistry", "build_registry"]


def build_registry() -> ToolRegistry:
    """Every tool, defined once. The Mind gets all of them; the Change Analyzer's loops get the subset
    its spec lists (see `agent/subagents/specs.py`) and never `delegate`."""
    return ToolRegistry(
        [
            SearchTool(),
            GrepTool(),
            ReadPagesTool(),
            OutlineTool(),
            ListDocumentsTool(),
            DelegateTool(),
            CreateFileTool(),
            WebSearchTool(),
            ReadUrlTool(),
            PageVisionTool(),
            LoadTableTool(),
            CheckVersionsTool(),
            ScanDocumentsTool(),
            FindChangelogTool(),
            VerifyFindingsTool(),
            WriteReportTool(),
            GetTableTool(),
            GetRowTool(),
            AddRowTool(),
            UpdateRowTool(),
            ArchiveRowTool(),
            RestoreRowTool(),
        ]
    )

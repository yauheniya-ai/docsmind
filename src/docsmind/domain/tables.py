"""Persistent tables the agent produces (e.g. from compare_versions) that the
user can then edit in the UI and export. Revisions are append-only so neither
side clobbers the other's work silently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TableColumnType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    SEVERITY = "severity"
    CITATIONS = "citations"


class TableColumn(BaseModel):
    key: str
    label: str
    type: TableColumnType = TableColumnType.TEXT


class CreatedBy(str, Enum):
    AGENT = "agent"
    USER = "user"


class AgentTable(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    workspace_id: str
    title: str
    description: str | None = None
    columns: list[TableColumn]
    data: list[dict[str, Any]] = Field(default_factory=list)
    created_by: CreatedBy = CreatedBy.AGENT
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TableRevision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    table_id: UUID
    revision: int
    data: list[dict[str, Any]]
    note: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

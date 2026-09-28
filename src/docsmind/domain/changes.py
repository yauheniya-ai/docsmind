"""Domain models for version-to-version change analysis (the diffra subagent).

The pipeline that produces these is deliberately hybrid:
1. structurally align sections between two versions (by `Section.path`)
2. run a deterministic text diff on aligned pairs
3. only then ask an LLM to classify each change and assess its implications

This keeps the output reproducible and cheap to re-run, and keeps the LLM's
job narrow: classify and explain a diff it's shown, not discover it unaided.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from docsmind.domain.citations import Citation


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    UNCHANGED = "unchanged"


class Severity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ImpactAssessment(BaseModel):
    severity: Severity
    rationale: str
    affected_obligations: list[str] = Field(default_factory=list)


class Change(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    section_path: str
    kind: ChangeKind
    before: str | None = None
    after: str | None = None
    impact: ImpactAssessment | None = None
    citations: list[Citation] = Field(default_factory=list)


class ChangeSet(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    from_version_id: UUID
    to_version_id: UUID
    changes: list[Change] = Field(default_factory=list)

    def by_severity(self, minimum: Severity) -> list[Change]:
        order = list(Severity)
        return [
            c for c in self.changes
            if c.impact and order.index(c.impact.severity) >= order.index(minimum)
        ]

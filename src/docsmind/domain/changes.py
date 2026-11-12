"""Domain models for version-to-version change analysis (the Change Analyzer).

One `ChangeAnalysis` compares a current version (A) with a previous version (B)
and owns a table of `ChangeRow`s, one per change. The table is the single
source of truth: the UI displays and edits it, verification fills in `verified`
and `proof`, and the Excel export writes it.

The rules a row must obey live here, in code, not in a prompt: whatever the
scan model returns goes through `ChangeRow.checked()` before it is stored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Nature(str, Enum):
    NEW = "new"
    MODIFIED = "modified"
    EDITORIAL = "editorial"
    REMOVED = "removed"
    RELOCATED = "relocated"


class Binding(str, Enum):
    NORMATIVE = "normative"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    INFORMATIVE = "informative"
    UNCHANGED = "unchanged"


# What models write instead of the exact words, mapped to the exact words.
NATURE_ALIASES = {
    "added": "new",
    "addition": "new",
    "changed": "modified",
    "amended": "modified",
    "reworded": "editorial",
    "deleted": "removed",
    "deletion": "removed",
    "moved": "relocated",
}
BINDING_ALIASES = {
    "mandatory": "normative",
    "required": "normative",
    "must": "normative",
    "should": "recommended",
    "may": "optional",
    "informational": "informative",
    "none": "unchanged",
    "n/a": "unchanged",
}

NO_CHANGE = "No change"
_NO_DELTA = {"", "no change", "none", "n/a", "-"}

# Nature values that can carry a compliance requirement (the others are structure or wording only).
SUBSTANTIVE = (Nature.NEW, Nature.MODIFIED, Nature.REMOVED)

# The exported columns, in order: (field, label). Verified, Proof and Approved are UI-only.
EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("current_clause", "Current clause (A)"),
    ("previous_clause", "Previous clause (B)"),
    ("change_description", "Change description"),
    ("nature", "Nature"),
    ("category", "Category"),
    ("compliance_requirement", "Compliance requirement"),
    ("binding", "Binding"),
    ("applicability_scope", "Applicability scope"),
)


def coerce_nature(value: Any) -> Nature | None:
    word = str(value or "").strip().lower()
    word = NATURE_ALIASES.get(word, word)
    try:
        return Nature(word)
    except ValueError:
        return None


def coerce_binding(value: Any) -> Binding | None:
    word = str(value or "").strip().lower()
    word = BINDING_ALIASES.get(word, word)
    try:
        return Binding(word)
    except ValueError:
        return None


class Proof(BaseModel):
    """A verbatim passage that backs a row. `page` is None when it could not be pinned down."""

    doc: Literal["current", "previous"]
    text: str
    page: int | None = None


class ChangeRow(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    analysis_id: UUID | None = None
    ordinal: int = 0
    current_clause: str = ""
    previous_clause: str = ""
    change_description: str = ""
    nature: Nature = Nature.MODIFIED
    category: str = ""
    compliance_requirement: str = NO_CHANGE
    binding: Binding = Binding.UNCHANGED
    applicability_scope: str = ""
    # UI-only columns (not exported):
    verified: bool = False
    proof: list[Proof] = Field(default_factory=list)
    verification_note: str | None = None  # why a row is not verified: failed, or not verifiable
    approved: bool = False  # a human's check
    origin: Literal["scan", "changelog", "user"] = "scan"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def has_delta(self) -> bool:
        """Does the row carry a compliance requirement (as opposed to 'No change')?"""
        return self.compliance_requirement.strip().lower() not in _NO_DELTA

    @property
    def actionable(self) -> bool:
        """A compliance delta exists and it is normative. Only these rows are verified in depth."""
        return self.nature in SUBSTANTIVE and self.has_delta and self.binding == Binding.NORMATIVE

    def checked(self) -> ChangeRow:
        """Apply the rules the model is not trusted to follow.

        - Editorial and relocated rows change no requirement: binding is `unchanged`,
          compliance requirement is 'No change'.
        - Only new, modified and removed rows may carry a normative, recommended or optional binding.
        """
        row = self.model_copy(deep=True)
        if row.nature in (Nature.EDITORIAL, Nature.RELOCATED):
            row.binding = Binding.UNCHANGED
            row.compliance_requirement = NO_CHANGE
        elif row.binding == Binding.UNCHANGED and not row.has_delta:
            row.compliance_requirement = NO_CHANGE
        if not row.compliance_requirement.strip():
            row.compliance_requirement = NO_CHANGE
        return row

    def export_values(self) -> list[str]:
        return [
            getattr(self, key).value if isinstance(getattr(self, key), Enum) else getattr(self, key)
            for key, _ in EXPORT_COLUMNS
        ]


AnalysisStatus = Literal["running", "done", "failed", "stopped", "identical", "unrelated"]
AnalysisMode = Literal["diff", "full", "changelog", "none"]


class ChangeAnalysis(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    current_version_id: UUID
    previous_version_id: UUID
    status: AnalysisStatus = "running"
    mode: AnalysisMode = "none"
    diff_ratio: float | None = None
    public: bool | None = None  # does the Change Analyzer treat these as public documents?
    verdict_note: str = ""  # what the versions check saw, in plain words
    # {"found": bool, "source": str, "summary": str, "entries": [...], "unmatched": [...]}
    changelog: dict[str, Any] = Field(default_factory=dict)
    report: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)  # usage, rounds, parts, candidates not found
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ChangeResult(BaseModel):
    """What a comparison returns to code that is not the chat: the report text, and the stored
    analysis with its rows (None when no table was made: identical or unrelated documents)."""

    message: str
    analysis: ChangeAnalysis | None = None
    rows: list[ChangeRow] = Field(default_factory=list)

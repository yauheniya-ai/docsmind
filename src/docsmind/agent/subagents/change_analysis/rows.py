"""Reading what the models write: JSON objects and change rows.

Models wrap JSON in code fences, add a sentence before it, or use near-miss
words ("added" for "new"). Everything is tolerated here, once, so the rest of
the pipeline works with clean `ChangeRow`s only.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from docsmind.domain.changes import ChangeRow, coerce_binding, coerce_nature

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*$")

# Names models use for the table columns, mapped to ours.
_FIELD_ALIASES = {
    "current_clause": ("current_clause", "clause_a", "current", "clause"),
    "previous_clause": ("previous_clause", "clause_b", "previous"),
    "change_description": ("change_description", "description", "change"),
    "nature": ("nature", "type", "change_type"),
    "category": ("category",),
    "compliance_requirement": ("compliance_requirement", "requirement", "compliance"),
    "binding": ("binding",),
    "applicability_scope": ("applicability_scope", "scope", "applies_to", "applicability"),
}


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model reply, or None."""
    text = text.strip()
    if not text:
        return None
    lines = [line for line in text.splitlines() if not _FENCE.match(line)]
    cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(_text(v) for v in value if v)
    return " ".join(str(value).split())


def row_from_object(obj: dict[str, Any], *, origin: str = "scan") -> ChangeRow | None:
    """A model's JSON object -> a checked `ChangeRow`, or None when it is not a usable row
    (no recognisable nature, or nothing identifying the clause)."""
    values: dict[str, Any] = {}
    for field, names in _FIELD_ALIASES.items():
        for name in names:
            if name in obj and obj[name] is not None:
                values[field] = obj[name]
                break
    nature = coerce_nature(values.get("nature"))
    if nature is None:
        return None
    current = _text(values.get("current_clause"))
    previous = _text(values.get("previous_clause"))
    if not current and not previous:
        return None
    binding = coerce_binding(values.get("binding"))
    row = ChangeRow(
        current_clause=current,
        previous_clause=previous,
        change_description=_text(values.get("change_description")),
        nature=nature,
        category=_text(values.get("category")),
        compliance_requirement=_text(values.get("compliance_requirement")) or "",
        applicability_scope=_text(values.get("applicability_scope")),
        origin=origin,  # type: ignore[arg-type]
        **({"binding": binding} if binding else {}),
    )
    return row.checked()


def parse_row_line(line: str) -> tuple[str, ChangeRow | None]:
    """One line of the scan's JSONL output -> ("row", row) | ("done", None) | ("skip", None)
    | ("broken", None). Fences, blank lines and stray prose are skipped; a line that starts like JSON
    but is not valid JSON is broken (a cut-off or corrupt row)."""
    stripped = line.strip().rstrip(",")
    if not stripped or _FENCE.match(stripped):
        return "skip", None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return ("broken", None) if stripped[0] in "{[" else ("skip", None)
    if not isinstance(obj, dict):
        return "skip", None
    if obj.get("done") is True and len(obj) <= 2:
        return "done", None
    row = row_from_object(obj)
    if row is None:
        log.info("Scan row without a usable nature/clause was dropped: %s", stripped[:200])
        return "skip", None
    return "row", row


def row_key(row: ChangeRow) -> str:
    """Identity used to drop a row a resumed scan call repeats."""
    return "|".join(
        [
            " ".join(row.current_clause.lower().split()),
            " ".join(row.previous_clause.lower().split()),
            row.nature.value,
            " ".join(row.change_description.lower().split())[:80],
        ]
    )


def row_for_model(row: ChangeRow) -> dict[str, Any]:
    """A row as the models see it (no UI-only fields)."""
    return {
        "id": str(row.id),
        "current_clause": row.current_clause,
        "previous_clause": row.previous_clause,
        "change_description": row.change_description,
        "nature": row.nature.value,
        "category": row.category,
        "compliance_requirement": row.compliance_requirement,
        "binding": row.binding.value,
        "applicability_scope": row.applicability_scope,
    }


def row_line(row: ChangeRow) -> str:
    """One JSONL line, as in the scan's own output (used to show a resumed call what exists)."""
    data = row_for_model(row)
    data.pop("id")
    return json.dumps(data, ensure_ascii=False)


def row_to_json(row: ChangeRow) -> dict[str, Any]:
    """A row as the UI gets it (every field, JSON-native)."""
    return row.model_dump(mode="json")


__all__ = [
    "extract_json",
    "parse_row_line",
    "row_for_model",
    "row_from_object",
    "row_key",
    "row_line",
    "row_to_json",
]

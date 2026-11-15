"""Row handles (R1, R2, ...) and the code-side similarity used to spot duplicates when a row is added."""

from __future__ import annotations

import re

from docsmind.domain.changes import ChangeRow

_WORDS = re.compile(r"[a-z0-9]{3,}")
_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def row_handle(row: ChangeRow) -> str:
    """How the agent refers to a row: R1, R2, ... (ordinal + 1), stable and never reused."""
    return f"R{row.ordinal + 1}"


def parse_handle(text: str) -> int | None:
    """"R12" (or "12") -> the row's ordinal (11); None when it is not a handle."""
    match = re.fullmatch(r"\s*[Rr]?\s*(\d+)\s*", str(text))
    return int(match.group(1)) - 1 if match and int(match.group(1)) >= 1 else None


def _tokens(row: ChangeRow) -> set[str]:
    return set(_WORDS.findall(f"{row.current_clause} {row.previous_clause} {row.change_description}".lower()))


def _clause_number(row: ChangeRow) -> str:
    match = _NUMBER.match(row.current_clause or row.previous_clause)
    return match.group(1) if match else ""


def _score(a: ChangeRow, b: ChangeRow) -> float:
    ta, tb = _tokens(a), _tokens(b)
    overlap = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    number = _clause_number(a)
    if number and number == _clause_number(b):
        overlap += 1.0  # the same clause number is the strongest hint
    title = " ".join(a.current_clause.lower().split())
    if title and title == " ".join(b.current_clause.lower().split()):
        overlap += 1.0  # so is the same clause title
    return overlap


def similarity(a: ChangeRow, b: ChangeRow) -> float:
    """How alike two rows are (token overlap, plus 1 for the same clause number)."""
    return _score(a, b)


def neighbours(candidate: ChangeRow, existing: list[ChangeRow], limit: int) -> list[ChangeRow]:
    """The `limit` existing rows most likely to describe the same change as `candidate`."""
    ranked = sorted(existing, key=lambda r: _score(candidate, r), reverse=True)
    return ranked[:limit]

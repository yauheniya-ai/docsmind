"""Row handles (R1, R2, ...) and the clause identifiers that tie a finding of one version to a clause of the other."""

from __future__ import annotations

import re

from docsmind.domain.changes import ChangeRow

_NUMBER = re.compile(r"\d+(?:\.\d+)+")  # 3.1.8, 03.01.08, A... "3.1" counts, a bare "3" does not


def row_handle(row: ChangeRow) -> str:
    """How the agent refers to a row: R1, R2, ... (ordinal + 1), stable and never reused."""
    return f"R{row.ordinal + 1}"


def parse_handle(text: str) -> int | None:
    """"R12" (or "12") -> the row's ordinal (11); None when it is not a handle."""
    match = re.fullmatch(r"\s*[Rr]?\s*(\d+)\s*", str(text))
    return int(match.group(1)) - 1 if match and int(match.group(1)) >= 1 else None


def normalize_number(number: str) -> str:
    """"03.01.08" -> "3.1.8": the same clause is numbered with leading zeros in one version and without in the other."""
    return ".".join(str(int(part)) for part in number.split("."))


def numbers_in(text: str) -> set[str]:
    return {normalize_number(m) for m in _NUMBER.findall(text or "")}


def clause_id(text: str) -> str:
    """The most specific number in a clause label, normalized: "3.1 ACCESS CONTROL - 3.1.8 Limit ..." -> "3.1.8",
    "03.01.08 Unsuccessful Logon Attempts" -> "3.1.8". "" when the label has no number."""
    found = numbers_in(text)
    return max(found, key=lambda n: (n.count("."), len(n))) if found else ""


def row_clause_ids(row: ChangeRow) -> set[str]:
    """The clause of a row in A and in B, as identifiers: two rows about one clause share one."""
    return {i for i in (clause_id(row.current_clause), clause_id(row.previous_clause)) if i}


def title_key(row: ChangeRow) -> str:
    return " ".join((row.current_clause or "").lower().split())

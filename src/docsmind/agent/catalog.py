"""The set of documents the Mind may use for one turn, with short handles.

The model refers to documents as `D1`, `D2`, ... (cheaper than UUIDs and
impossible to mistype into a different valid id). The catalog is built from
the request's scope, so it is also the enforcement point: a tool can only
ever touch documents that are in here, whatever the model asks for.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from docsmind.domain.errors import ToolInputError


@dataclass(frozen=True)
class CatalogDoc:
    handle: str
    version_id: UUID
    title: str
    collection: str
    label: str
    page_count: int | None
    word_count: int | None

    def line(self) -> str:
        parts = [f"{self.handle} | {self.title} | {self.collection} / {self.label}"]
        if self.page_count:
            parts.append(f"{self.page_count} pages")
        if self.word_count:
            parts.append(f"{self.word_count:,} words")
        return " | ".join(parts)


class Catalog:
    def __init__(self, docs: list[CatalogDoc]) -> None:
        self.docs = docs
        self._by_handle = {d.handle: d for d in docs}
        self._by_version = {d.version_id: d for d in docs}

    @classmethod
    def from_rows(cls, rows: list[dict]) -> Catalog:
        return cls(
            [
                CatalogDoc(
                    handle=f"D{i}",
                    version_id=row["id"],
                    title=row["title"],
                    collection=row["collection"],
                    label=row["label"],
                    page_count=row.get("page_count"),
                    word_count=row.get("word_count"),
                )
                for i, row in enumerate(rows, start=1)
            ]
        )

    @property
    def version_ids(self) -> list[UUID]:
        return [d.version_id for d in self.docs]

    def __len__(self) -> int:
        return len(self.docs)

    def by_version(self, version_id: UUID) -> CatalogDoc | None:
        return self._by_version.get(version_id)

    def resolve(self, handle: str) -> CatalogDoc:
        doc = self._by_handle.get(handle.strip().upper())
        if doc is None:
            valid = ", ".join(self._by_handle) or "none"
            raise ToolInputError(f"Unknown document '{handle}'. Valid documents: {valid}.")
        return doc

    def resolve_many(self, handles: list[str] | None) -> list[CatalogDoc]:
        """`None`/empty means "all documents in scope"."""
        if not handles:
            return list(self.docs)
        return [self.resolve(h) for h in handles]

    def render(self) -> str:
        if not self.docs:
            return "(no documents are available)"
        return "\n".join(d.line() for d in self.docs)

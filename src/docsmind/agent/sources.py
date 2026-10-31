"""Source registry: the bridge between what tools return and what the answer cites.

Every passage a tool shows the model gets a short handle (`S1`, `S2`, ...).
The model cites with `[S1]`; after the answer is written the harness checks
each marker against this registry, drops any the model made up, and turns the
rest into real `Citation`s for the UI. The model never handles raw UUIDs.
"""

from __future__ import annotations

import re
from uuid import UUID

from docsmind.domain.citations import Citation

# `[S1]`, `[S1, S2]`, `[S1][S2]`
_GROUP_RE = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")
_HANDLE_RE = re.compile(r"S\d+")
# Used to hide handles from old assistant turns replayed as history (handles
# are per-turn; replaying them would collide with the new turn's S1, S2, ...).
_ANY_MARKER_RE = re.compile(r"\s*\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]")


class SourceRegistry:
    def __init__(self) -> None:
        self._by_chunk: dict[UUID, Citation] = {}
        self._by_handle: dict[str, Citation] = {}

    def add(
        self,
        *,
        chunk_id: UUID,
        version_id: UUID,
        title: str,
        collection: str,
        label: str,
        page_start: int | None,
        page_end: int | None,
        snippet: str,
    ) -> Citation:
        existing = self._by_chunk.get(chunk_id)
        if existing:
            return existing
        citation = Citation(
            chunk_id=chunk_id,
            document_title=title,
            version_label=label,
            page_start=page_start,
            page_end=page_end,
            snippet=snippet[:600],
            handle=f"S{len(self._by_handle) + 1}",
            version_id=version_id,
            collection=collection,
        )
        self._by_chunk[chunk_id] = citation
        self._by_handle[citation.handle] = citation  # type: ignore[index]
        return citation

    def get(self, handle: str) -> Citation | None:
        return self._by_handle.get(handle)

    def __len__(self) -> int:
        return len(self._by_handle)


def finalize_citations(text: str, registry: SourceRegistry) -> tuple[str, list[Citation]]:
    """Verify and tidy the model's citations.

    Unknown handles are dropped, groups are normalised to `[S1][S2]`, and the
    survivors are renumbered S1, S2, ... in order of first appearance — the
    model's handles are whatever order its tools ran in (S22, S3, ...), which
    reads badly next to the answer. Returns the rewritten text and the
    citations actually used, in the new order."""
    order: dict[str, str] = {}  # model handle -> final handle
    cited: list[Citation] = []

    def replace(match: re.Match[str]) -> str:
        valid: list[str] = []
        for handle in _HANDLE_RE.findall(match.group(1)):
            citation = registry.get(handle)
            if citation is None:
                continue
            if handle not in order:
                order[handle] = f"S{len(order) + 1}"
                cited.append(citation.model_copy(update={"handle": order[handle]}))
            if order[handle] not in valid:
                valid.append(order[handle])
        return "".join(f"[{h}]" for h in valid)

    cleaned = _GROUP_RE.sub(replace, text)
    return cleaned, cited


def strip_markers(text: str) -> str:
    return _ANY_MARKER_RE.sub("", text)

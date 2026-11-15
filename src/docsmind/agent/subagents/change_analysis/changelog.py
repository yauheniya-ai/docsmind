"""The changelog side of a comparison.

- `find_changelog`: a tool loop that looks for a changelog (in the documents; for public ones also
  online) and reads its entries. Runs in parallel with the scan, in a small context.
- `match_entries`: one small model call that pairs changelog entries with table rows.
- Entries that match no row are then checked by `verify_row` gap loops (see the analyzer): a real
  change is added with proof, an unreal one is listed as "claimed in changelog, no change found".
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.agent.subagents.change_analysis.rows import extract_json, row_for_model
from docsmind.agent.subagents.specs import CHANGELOG_LOCAL_TOOLS as LOCAL_TOOLS
from docsmind.agent.subagents.specs import CHANGELOG_WEB_TOOLS as WEB_TOOLS
from docsmind.agent.tools.base import Emit
from docsmind.domain.changes import ChangeRow

log = logging.getLogger(__name__)

MAX_ENTRIES = 80
KINDS = {"new", "modified", "removed", "other"}


def _clean_entries(raw) -> list[dict]:
    entries: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())
        clause = " ".join(str(item.get("clause") or "").split())
        if not text and not clause:
            continue
        kind = str(item.get("kind") or "other").strip().lower()
        relevant = item.get("compliance_relevant")
        entries.append(
            {
                "clause": clause,
                "kind": kind if kind in KINDS else "other",
                "text": text,
                "compliance_relevant": relevant if isinstance(relevant, bool) else kind in ("new", "modified", "removed"),
            }
        )
        if len(entries) >= MAX_ENTRIES:
            break
    return entries


async def find_changelog(
    env: RunEnv, a: CatalogDoc, b: CatalogDoc, *, public: bool, query: str, notes: str, emit: Emit, tag: dict
) -> dict:
    """-> {"found", "source", "summary", "entries"}. Never raises for model trouble: a failed
    search is reported as "not found" so the comparison goes on without a changelog."""
    tools = LOCAL_TOOLS + (WEB_TOOLS if public else ())
    task = [f"A (current version) = {a.handle} · {a.title}", f"B (previous version) = {b.handle} · {b.title}"]
    if public and query:
        task.append(f"Web search hint: {query}")
    if not public:
        task.append("These are internal documents: do not search the web.")
    if notes:
        task.append(f"<focus>\n{notes}\n</focus>")
    try:
        outcome = await env.loop(
            system=prompts.CHANGELOG_FINDER,
            task="\n".join(task),
            catalog=Catalog([a, b]),
            tool_names=tools,
            max_steps=env.config.change_changelog_max_steps,
            max_tokens=4_096,
            emit=emit,
            tag=tag,
            tool="find_changelog",
        )
    except Exception as exc:  # noqa: BLE001 - the comparison goes on without a changelog
        log.warning("Changelog search failed: %s: %s", type(exc).__name__, exc)
        return {"found": False, "source": "", "summary": "", "entries": [], "error": type(exc).__name__}

    answer = extract_json(outcome.text) or {}
    entries = _clean_entries(answer.get("entries"))
    found = bool(answer.get("found")) and bool(entries)
    return {
        "found": found,
        "source": " ".join(str(answer.get("source") or "").split()) if found else "",
        "summary": " ".join(str(answer.get("summary") or "").split()) if found else "",
        "entries": entries if found else [],
    }


async def match_entries(env: RunEnv, entries: list[dict], rows: list[ChangeRow]) -> dict[int, UUID | None]:
    """Entry index -> the row it describes, or None. Entries the model leaves out count as unmatched."""
    matches: dict[int, UUID | None] = {i: None for i in range(len(entries))}
    if not entries or not rows:
        return matches
    ids = {str(r.id): r.id for r in rows}
    payload = {
        "entries": [{"entry": i, "clause": e["clause"], "kind": e["kind"], "text": e["text"]} for i, e in enumerate(entries)],
        "rows": [
            {k: v for k, v in row_for_model(r).items() if k in ("id", "current_clause", "previous_clause", "change_description", "nature")}
            for r in rows
        ],
    }
    answer = await env.json_call(
        env.fast_llm, prompts.CHANGELOG_MATCHER, json.dumps(payload, ensure_ascii=False), max_tokens=4_096, tool="find_changelog"
    )
    for item in (answer or {}).get("matches") or []:
        if not isinstance(item, dict):
            continue
        index, row_id = item.get("entry"), str(item.get("row") or "")
        if isinstance(index, int) and index in matches and row_id in ids:
            matches[index] = ids[row_id]
    return matches

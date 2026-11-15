"""The changelog side of a comparison.

- `find_changelog`: a tool loop that looks for a changelog (in the documents; for public ones also
  online) and reads its entries. Runs in parallel with the scan, in a small context.
- `match_entries`: one small model call that pairs changelog entries with table rows.
- Entries that match no row are then checked by `verify_row` gap loops (see the analyzer): a real
  change is added with proof, an unreal one is listed as "claimed in changelog, no change found".
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.agent.subagents.change_analysis.rows import extract_json, plain_text, row_for_model
from docsmind.agent.subagents.specs import CHANGELOG_LOCAL_TOOLS as LOCAL_TOOLS
from docsmind.agent.subagents.specs import CHANGELOG_WEB_TOOLS as WEB_TOOLS
from docsmind.agent.tools.base import Emit
from docsmind.domain.changes import ChangeRow

log = logging.getLogger(__name__)

MAX_ENTRIES = 80
MAX_TABLE_ENTRIES = 600  # entries read from a spreadsheet changelog by code
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


_NO_CHANGE = ("no change", "unchanged", "editorial", "no significant", "not changed", "none")
_KIND_WORDS = (
    ("removed", ("withdraw", "delet", "remov", "incorporated", "superseded", "dropped", "obsolete")),
    ("new", ("new", "added", "addition", "introduc")),
    ("modified", ("modif", "chang", "updat", "revis", "reword", "clarif", "merge", "moved", "split")),
)


def _kind_of(text: str) -> str:
    word = text.lower()
    if any(w in word for w in _NO_CHANGE):
        return "other"
    for kind, stems in _KIND_WORDS:
        if any(stem in word for stem in stems):
            return kind
    return "other"


def _names(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


def _cell(row: list[str], i: int) -> str:
    return row[i].strip() if i < len(row) else ""


def _column(header: list[str], name: str) -> int | None:
    wanted = " ".join(name.lower().split())
    for i, cell in enumerate(header):
        if " ".join(cell.lower().split()) == wanted:
            return i
    for i, cell in enumerate(header):
        if wanted and wanted in " ".join(cell.lower().split()):
            return i
    return None


async def entries_from_table(env: RunEnv, spec: dict) -> list[dict]:
    """Every row of a spreadsheet or CSV changelog as an entry, read by code: the model only says which columns hold
    the clause, the kind of change and the description. -> [] when the file or the columns cannot be read."""
    from docsmind.agent.tools.change import parse_table
    from docsmind.agent.tools.web import fetch_bytes

    url = str(spec.get("url") or "").strip()
    clause_cols, text_cols = _names(spec.get("clause_column")), _names(spec.get("text_column"))
    kind_cols = _names(spec.get("kind_column"))
    if not url or not clause_cols:
        return []
    final_url, content_type, data, encoding = await fetch_bytes(
        url, env.config, allowed=None, max_bytes=env.config.change_table_max_bytes
    )
    sheets = await asyncio.to_thread(parse_table, data, final_url, content_type, encoding)
    wanted = str(spec.get("sheet") or "").strip().lower()
    candidates = [rows for name, rows in sheets.items() if name.lower() == wanted] or list(sheets.values())
    for rows in candidates:
        for h, header in enumerate(rows[:15]):  # the header row may follow a title
            clause_idx = [i for i in (_column(header, n) for n in clause_cols) if i is not None]
            if not clause_idx:
                continue
            text_idx = [i for i in (_column(header, n) for n in text_cols) if i is not None]
            kind_idx = [i for i in (_column(header, n) for n in kind_cols) if i is not None]
            entries: list[dict] = []
            for row in rows[h + 1 :]:
                clause = " / ".join(c for c in (_cell(row, i) for i in clause_idx) if c)
                text = " ".join(c for c in (_cell(row, i) for i in text_idx) if c)
                if not clause and not text:
                    continue
                kind = _kind_of(" ".join(_cell(row, i) for i in kind_idx)) if kind_idx else "modified"
                entries.append(
                    {"clause": clause, "kind": kind, "text": text[:600], "compliance_relevant": kind in ("new", "modified", "removed")}
                )
                if len(entries) >= MAX_TABLE_ENTRIES:
                    break
            if entries:
                return entries
    return []


async def find_changelog(
    env: RunEnv,
    a: CatalogDoc,
    b: CatalogDoc,
    *,
    public: bool,
    query: str,
    notes: str,
    emit: Emit,
    tag: dict,
    url: str = "",
) -> dict:
    """-> {"found", "source", "summary", "entries"}. Never raises for model trouble: a failed
    search is reported as "not found" so the comparison goes on without a changelog."""
    tools = LOCAL_TOOLS + (WEB_TOOLS if public else ())
    task = [f"A (current version) = {a.handle} · {a.title}", f"B (previous version) = {b.handle} · {b.title}"]
    if public and query:
        task.append(f"Web search hint: {query}")
    if public and url:
        task.append(f"<changelog_url>{url}</changelog_url>")
    if public and not env.config.web_search_api_key:
        task.append(
            "web_search is not available here: find the publication's URL or DOI on the first pages of the documents "
            "and open it with read_url."
        )
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
    imported = 0
    if isinstance(answer.get("table"), dict):  # a spreadsheet changelog: code reads all of its rows
        try:
            from_table = await entries_from_table(env, answer["table"])
        except Exception as exc:  # noqa: BLE001 - fall back to the entries the model listed
            log.warning("Could not import the changelog table: %s: %s", type(exc).__name__, exc)
            from_table = []
        if from_table:
            entries, imported = from_table, len(from_table)
            answer["found"] = True
            answer["source"] = answer.get("source") or str(answer["table"].get("url") or "")
    found = bool(answer.get("found")) and bool(entries)
    scope = {
        "public": public,
        # whether the search really went online: a web search, or a page read by URL
        "online": any(name in ("web_search", "read_url") for name in outcome.tools_called),
        "web_search": bool(env.config.web_search_api_key),
    }

    def clean(text: object) -> str:
        return plain_text(str(text or ""), a.handle, b.handle)

    for entry in entries:
        entry["clause"], entry["text"] = clean(entry["clause"]), clean(entry["text"])
    return {
        **scope,
        "imported": imported,  # entries read from a spreadsheet by code
        "found": found,
        "source": clean(answer.get("source")) if found else "",
        "summary": clean(answer.get("summary")) if found else "",
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

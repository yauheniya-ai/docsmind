"""The report, short on purpose: the table has the detail, the report says what it means.

Order: Compliance impact (the model: a high-level summary for executives and operators, no references), the
count of substantive changes with the editorial and relocated clauses by identifier (code), Methodology
(code, from the run's real numbers: texts, scan, changelog and where it was looked for, verification, effort
and cost) and the AI disclaimer. Nothing countable is left to the model. Candidate rows that could not be
proven are simply not in the table: they are neither reported nor stored.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from docsmind.agent import prompts
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.domain.changes import ChangeAnalysis, ChangeRow, Nature
from docsmind.pricing import usd

log = logging.getLogger(__name__)

MAX_ROWS_IN_PROMPT = 200
MAX_IDS = 150
_CLAUSE_ID = re.compile(r"^\s*((?:Section|Clause|Appendix|Annex)\s+)?([A-Za-z]{0,2}\d[\w.\-]*[\w])", re.IGNORECASE)


def clause_name(row: ChangeRow) -> str:
    return row.current_clause or row.previous_clause or "(unnamed)"


def pages(row: ChangeRow) -> str:
    parts = []
    for side, label in (("current", "A"), ("previous", "B")):
        parts += [f"{label} p.{n}" for n in sorted({p.page for p in row.proof if p.doc == side and p.page})]
    return ", ".join(parts)


def counts(rows: list[ChangeRow]) -> dict[str, int]:
    out = {n.value: 0 for n in Nature}
    for r in rows:
        out[r.nature.value] += 1
    out["total"] = len(rows)
    out["actionable"] = sum(r.actionable for r in rows)
    out["verified"] = sum(r.verified for r in rows)
    return out


def clause_id(row: ChangeRow) -> str:
    """The identifier of a clause: its number ("3.1.1", "A.2"), or a short form of its title when it has none."""
    text = clause_name(row)
    match = _CLAUSE_ID.match(text)
    if match:
        return (match.group(1) or "") + match.group(2).rstrip(".")
    return text if len(text) <= 40 else text[:39].rstrip() + "…"


def _ids(rows: list[ChangeRow]) -> str:
    seen: list[str] = []
    for r in rows:
        ident = clause_id(r)
        if ident not in seen:
            seen.append(ident)
    shown = seen[:MAX_IDS]
    return ", ".join(shown) + (f" and {len(seen) - len(shown)} more" if len(seen) > len(shown) else "")


def _facts(rows: list[ChangeRow], changelog: dict) -> dict[str, Any]:
    """What the model writes the compliance impact from: the substantive changes in brief, no page numbers."""
    substantive = [r for r in rows if r.nature in (Nature.NEW, Nature.MODIFIED, Nature.REMOVED)]
    substantive.sort(key=lambda r: not r.actionable)  # compliance-relevant first
    return {
        "counts": counts(rows),
        "changes": [
            {
                "clause": clause_name(r),
                "nature": r.nature.value,
                "category": r.category,
                "compliance_relevant": r.actionable,
                "change": " ".join(r.change_description.split())[:300],
                "requirement": " ".join(r.compliance_requirement.split())[:300] if r.actionable else "",
                "scope": r.applicability_scope,
            }
            for r in substantive[:MAX_ROWS_IN_PROMPT]
        ],
        "changes_not_shown": max(0, len(substantive) - MAX_ROWS_IN_PROMPT),
        "changelog_summary": changelog.get("summary", "") if changelog.get("found") else "",
    }


def _fallback_narrative(rows: list[ChangeRow]) -> str:
    """Without the model: the themes (categories) of the compliance-relevant changes and how many each has."""
    actionable = [r for r in rows if r.actionable]
    if not actionable:
        return "No normative changes to a compliance requirement were found."
    themes: dict[str, int] = {}
    for r in actionable:
        themes[r.category.strip() or "Other"] = themes.get(r.category.strip() or "Other", 0) + 1
    return "\n".join(f"- **{name}**: {n} compliance-relevant change{'s' if n != 1 else ''}" for name, n in sorted(themes.items(), key=lambda kv: -kv[1]))


def _complete(text: str) -> str:
    """A reply cut off at the output limit ends after its last complete paragraph or bullet."""
    text = text.rstrip()
    if text.endswith((".", "!", "?", ")", "*")) and text.count("(") <= text.count(")"):  # not stopped inside a bracket
        return text
    cut = max(text.rfind("\n\n"), text.rfind("\n- "), text.rfind("\n* "))
    return text[:cut].rstrip() if cut > 0 else text


async def write_narrative(env: RunEnv, rows: list[ChangeRow], changelog: dict) -> str:
    """## Compliance impact: a high-level summary for executives and operators."""
    facts = json.dumps(_facts(rows, changelog), ensure_ascii=False)
    try:
        response = await env.call(env.llm, prompts.REPORT_WRITER, facts, max_tokens=6_000, tool="write_report")
        body = response.text.strip()
        if response.stop_reason == "max_tokens":
            body = _complete(body)
    except Exception as exc:  # noqa: BLE001 - the report must still be written without the narrative
        log.warning("The report writer failed: %s: %s", type(exc).__name__, exc)
        body = ""
    body = re.sub(r"^#{1,6} .*\n+", "", body).strip()  # the heading is the code's
    return "## Compliance impact\n" + (body or _fallback_narrative(rows))


def _changes_section(rows: list[ChangeRow]) -> str:
    """The counts of substantive changes (new, modified, removed, whatever their binding), then the editorial and
    relocated clauses by identifier."""
    c = counts(rows)
    total = c["new"] + c["modified"] + c["removed"]
    editorial = [r for r in rows if r.nature == Nature.EDITORIAL]
    relocated = [r for r in rows if r.nature == Nature.RELOCATED]
    lines = [
        "## Changes",
        (
            f"- Substantive: New {c['new']} · Modified {c['modified']} · Removed {c['removed']}"
            f" ({total} in total, {c['actionable']} of them compliance-relevant)"
        ),
    ]
    if editorial:
        lines.append(f"- Editorial ({len(editorial)}): {_ids(editorial)}")
    if relocated:
        lines.append(f"- Relocated ({len(relocated)}): {_ids(relocated)}")
    return "\n".join(lines)


def _changelog_lines(changelog: dict, *, changelog_mode: bool) -> list[str]:
    """The changelog as part of the methodology: where it was looked for, and what it gave."""
    if changelog_mode:
        return ["- Changelog: the second document is the changelog of the first and every change in the table comes from it."]
    if not changelog:
        return ["- Changelog: not searched."]
    if not changelog.get("public", True):
        where = "in the documents only (internal documents: no web access)"
    elif changelog.get("online"):
        where = "in the documents and online" + ("" if changelog.get("web_search") else " (by the publication's own link: web search is not set up)")
    elif "online" in changelog:
        why = "no source was found to open" if changelog.get("web_search") else "web search is not set up and no link was found"
        where = f"in the documents only; they are public, but nothing was read online ({why})"
    else:
        where = "in the documents"  # an analysis made before the scope was recorded
    if not changelog.get("found"):
        return [f"- Changelog: searched {where}; none was found, so the comparison relies on the text alone."]
    source = f" ({changelog['source']})" if changelog.get("source") else ""
    entries = changelog.get("entries") or []
    relevant = [e for e in entries if e.get("compliance_relevant")]
    explained = f", {changelog['matched']} explained by a row of the table" if "matched" in changelog else ""
    lines = [
        f"- Changelog: searched {where}; found{source}. {changelog.get('summary', '')}".rstrip()
        + (f" All {changelog['imported']} rows of the spreadsheet were read." if changelog.get("imported") else "")
        + f" {len(entries)} entries, {len(relevant)} compliance-relevant{explained}."
    ]
    added = changelog.get("added") or []
    if added:
        lines.append(f"  - Added to the table from the changelog, with proof ({len(added)}): {'; '.join(added)}")
    unmatched = changelog.get("claimed_not_found") or []
    if unmatched:
        lines.append(f"  - Claimed in the changelog, no change found in the text ({len(unmatched)}): {'; '.join(unmatched[:10])}")
    errored = changelog.get("unchecked") or []
    if errored:
        lines.append(f"  - Claimed in the changelog, could not be checked ({len(errored)})")
    return lines


def _verification_line(rows: list[ChangeRow], stats: dict, c: dict) -> str:
    """What was checked against the text, and what was not: everything about verification lives here."""
    line = (
        f"- Verification: {stats.get('verify_attempted', 0)} compliance-relevant rows checked against the text; "
        f"{c['verified']} verified with verbatim proof, {stats.get('verify_errors', 0)} could not be checked. "
        "A candidate change without proof in the text is not listed."
    )
    open_critical = sum(r.actionable and not r.verified for r in rows)
    others = sum(not r.actionable for r in rows)
    if open_critical:
        line += f" {open_critical} compliance-relevant row{'s are' if open_critical != 1 else ' is'} not verified yet."
    if others:
        line += (
            f" The {others} other row{'s' if others != 1 else ''} (editorial, relocated or without a normative "
            "requirement) are listed but not verified; they can be verified on request."
        )
    return line


def _methodology(analysis: ChangeAnalysis, rows: list[ChangeRow], stats: dict, changelog: dict, *, changelog_mode: bool) -> str:
    mode = {
        "diff": f"the current version plus a bounded diff (diff ratio {stats.get('diff_ratio', 0):.0%}, below the {stats.get('threshold', 0):.0%} threshold)",
        "full": f"both versions in full (diff ratio {stats.get('diff_ratio', 0):.0%}, at or above the {stats.get('threshold', 0):.0%} threshold)",
        "changelog": "the document plus its changelog",
    }.get(analysis.mode, analysis.mode)
    scan = stats.get("scan", {})
    c = counts(rows)
    lines = [
        "## Methodology",
        f"- Texts were normalized (headers, footers and page numbers removed) and compared sentence by sentence by code; the scan model saw {mode}.",
        f"- Scan: {scan.get('rows', 0)} candidate rows in {scan.get('parts', 1) - scan.get('skipped', 0)} model call(s)"
        + (f", {scan.get('retries')} relaunch(es)" if scan.get("retries") else "")
        + (f", {scan.get('skipped')} part(s) without changes skipped" if scan.get("skipped") else "")
        + ".",
        *_changelog_lines(changelog, changelog_mode=changelog_mode),
        _verification_line(rows, stats, c),
        (
            f"- Effort{' of all ' + str(stats['requests']) + ' requests on this analysis' if stats.get('requests', 1) > 1 else ''}: "
            f"{stats.get('model_calls', 0)} model calls, "
            f"{stats.get('input_tokens', 0):,} input and {stats.get('output_tokens', 0):,} output tokens, "
            f"{stats.get('seconds', 0)} s."
        ),
    ]
    lines += _cost_lines(stats)
    return "\n".join(lines)


def _cost_lines(stats: dict) -> list[str]:
    cost = stats.get("cost") or {}
    models = cost.get("models") or []
    if not models:
        return []
    total = usd(cost.get("usd", 0.0))
    if not cost.get("complete"):
        head = f"at least {total}"
    elif cost.get("upper_bound"):
        head = f"at most {total}"
    else:
        head = total
    plain = cost.get("usd_without_cache")
    without = f"; without caching it would have been {usd(plain)}" if plain and plain > cost.get("usd", 0.0) else ""
    lines = [f"- Cost: {head} at list prices{without}. Cache hit rate {cost.get('cache_hit_rate', 0.0):.0%}."]
    for m in models:
        if m["usd"] is None:
            lines.append(f"  - {m['model']}: {m['input_tokens']:,} input and {m['output_tokens']:,} output tokens, not priced (no price in the table)")
            continue
        used = (
            f"{m.get('uncached_input_tokens', m['input_tokens']):,} uncached + {m.get('cached_read_tokens', 0):,} cached read + "
            f"{m.get('cache_write_tokens', 0):,} cache write + {m['output_tokens']:,} output"
        )
        note = " (no cached price configured: priced as normal input)" if m.get("upper_bound") else ""
        lines.append(f"  - {m['model']}: {used} = {usd(m['usd'])}{note}")
    tools = [t for t in cost.get("tools") or [] if t["usd"] > 0]
    if tools:
        lines.append("  - By tool: " + ", ".join(f"{t['tool']} {usd(t['usd'])}" for t in tools))
    if stats.get("vision_reads"):
        lines.append(f"  - Page vision: {stats['vision_reads']} read(s), not included")
    return lines


async def build_report(
    env: RunEnv,
    *,
    analysis: ChangeAnalysis,
    rows: list[ChangeRow],
    changelog: dict,
    stats: dict,
    changelog_mode: bool,
) -> str:
    narrative = await write_narrative(env, rows, changelog)
    sections = [
        narrative,
        _changes_section(rows),
        _methodology(analysis, rows, stats, changelog, changelog_mode=changelog_mode),
        f"*{prompts.REPORT_DISCLAIMER}*",
    ]
    return "\n\n".join(s for s in sections if s)

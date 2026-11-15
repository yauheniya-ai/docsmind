"""The report: the model writes the narrative, code adds everything that must be exact.

Model (narrative): substantive changes, compliance impact, other noticeable findings.
Code: editorial / relocated / non-normative removed clauses by name, the changelog section, a
methodology note built from the run's real numbers, and the AI disclaimer. Nothing countable is
left to the model. Candidate rows that could not be proven are simply not in the table: they are
neither reported nor stored.

Pages are cited as "(A p.12)" (A = current version, B = previous); the UI turns them into links.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from docsmind.agent import prompts
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.domain.changes import ChangeAnalysis, ChangeRow, Nature
from docsmind.pricing import usd

log = logging.getLogger(__name__)

MAX_ACTIONABLE_IN_PROMPT = 150
MAX_OTHER_IN_PROMPT = 60
MAX_NAMES = 100


def clause_name(row: ChangeRow) -> str:
    return row.current_clause or row.previous_clause or "(unnamed)"


def pages(row: ChangeRow) -> str:
    parts = []
    for side, label in (("current", "A"), ("previous", "B")):
        parts += [f"{label} p.{n}" for n in sorted({p.page for p in row.proof if p.doc == side and p.page})]
    return ", ".join(parts)


def _names(items: list[str]) -> str:
    shown = items[:MAX_NAMES]
    more = len(items) - len(shown)
    return "; ".join(shown) + (f"; and {more} more" if more > 0 else "")


def counts(rows: list[ChangeRow]) -> dict[str, int]:
    out = {n.value: 0 for n in Nature}
    for r in rows:
        out[r.nature.value] += 1
    out["total"] = len(rows)
    out["actionable"] = sum(r.actionable for r in rows)
    out["verified"] = sum(r.verified for r in rows)
    return out


def _facts(rows: list[ChangeRow], changelog: dict) -> dict[str, Any]:
    actionable = [r for r in rows if r.actionable]
    other = [r for r in rows if r.nature in (Nature.NEW, Nature.MODIFIED, Nature.REMOVED) and not r.actionable]
    return {
        "counts": counts(rows),
        "actionable_rows": [
            {
                "clause_a": r.current_clause,
                "clause_b": r.previous_clause,
                "nature": r.nature.value,
                "category": r.category,
                "change": r.change_description,
                "compliance_requirement": r.compliance_requirement,
                "scope": r.applicability_scope,
                "verified": r.verified,
                "pages": pages(r),
                "note": r.verification_note or "",
            }
            for r in actionable[:MAX_ACTIONABLE_IN_PROMPT]
        ],
        "actionable_rows_not_shown": max(0, len(actionable) - MAX_ACTIONABLE_IN_PROMPT),
        "other_substantive_rows": [
            {
                "clause": clause_name(r),
                "nature": r.nature.value,
                "binding": r.binding.value,
                "change": r.change_description,
            }
            for r in other[:MAX_OTHER_IN_PROMPT]
        ],
        "other_rows_not_shown": max(0, len(other) - MAX_OTHER_IN_PROMPT),
        "changelog": {"found": bool(changelog.get("found")), "summary": changelog.get("summary", "")},
    }


def _fallback_narrative(rows: list[ChangeRow]) -> str:
    actionable = [r for r in rows if r.actionable]
    lines = ["## Substantive changes"]
    if not actionable:
        lines.append("No normative changes to a compliance requirement were found.")
    for r in actionable:
        mark = "" if r.verified else " (not verified)"
        cite = f" ({pages(r)})" if pages(r) else ""
        lines.append(f"- **{clause_name(r)}** ({r.nature.value}){mark}: {r.change_description}{cite}")
    return "\n".join(lines)


async def write_narrative(env: RunEnv, rows: list[ChangeRow], changelog: dict) -> str:
    facts = json.dumps(_facts(rows, changelog), ensure_ascii=False)
    try:
        response = await env.call(env.llm, prompts.REPORT_WRITER, facts, max_tokens=4_096, tool="write_report")
    except Exception as exc:  # noqa: BLE001 - the report must still be written without the narrative
        log.warning("The report writer failed: %s: %s", type(exc).__name__, exc)
        return _fallback_narrative(rows)
    text = response.text.strip()
    return text if "## " in text else _fallback_narrative(rows)


def _other_findings(rows: list[ChangeRow]) -> str:
    editorial = [clause_name(r) for r in rows if r.nature == Nature.EDITORIAL]
    relocated = [
        f"{r.current_clause or '(unnamed)'}" + (f" (was {r.previous_clause})" if r.previous_clause and r.previous_clause != r.current_clause else "")
        for r in rows
        if r.nature == Nature.RELOCATED
    ]
    removed = [clause_name(r) for r in rows if r.nature == Nature.REMOVED and not r.actionable]
    if not (editorial or relocated or removed):
        return ""
    lines = ["## Editorial, relocated and non-normative changes", "These change no compliance requirement."]
    if editorial:
        lines.append(f"- Editorial ({len(editorial)}): {_names(editorial)}")
    if relocated:
        lines.append(f"- Relocated ({len(relocated)}): {_names(relocated)}")
    if removed:
        lines.append(f"- Removed, not normative ({len(removed)}): {_names(removed)}")
    return "\n".join(lines)


def _changelog_section(changelog: dict, *, changelog_mode: bool) -> str:
    lines = ["## Changelog"]
    if changelog_mode:
        lines.append("The second document was used as the changelog; every change in the table comes from it.")
        return "\n".join(lines)
    if not changelog.get("found"):
        lines.append("No changelog was found, so the comparison relies on the text alone.")
        return "\n".join(lines)
    source = f" ({changelog['source']})" if changelog.get("source") else ""
    lines.append(f"Found{source}: {changelog.get('summary', '')}".strip())
    entries = changelog.get("entries") or []
    relevant = [e for e in entries if e.get("compliance_relevant")]
    explained = f", {changelog['matched']} explained by a row of the table" if "matched" in changelog else ""
    lines.append(f"{len(entries)} entries, {len(relevant)} compliance-relevant{explained}.")
    added = changelog.get("added") or []
    if added:
        lines.append(f"- Added to the table from the changelog, with proof ({len(added)}): {'; '.join(added)}")
    unmatched = changelog.get("claimed_not_found") or []
    if unmatched:
        lines.append(f"- Claimed in the changelog, no change found in the text ({len(unmatched)}):")
        lines += [f"  - {item}" for item in unmatched]
    errored = changelog.get("unchecked") or []
    if errored:
        lines.append(f"- Claimed in the changelog, could not be checked ({len(errored)}):")
        lines += [f"  - {item}" for item in errored]
    return "\n".join(lines)


def _methodology(analysis: ChangeAnalysis, rows: list[ChangeRow], stats: dict) -> str:
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
        (
            f"- Verification: {stats.get('verify_attempted', 0)} compliance-relevant rows checked against the text; "
            f"{c['verified']} verified with verbatim proof, {stats.get('verify_errors', 0)} could not be checked. "
            "A candidate change without proof in the text is not listed."
        ),
        (
            f"- Effort: {stats.get('model_calls', 0)} model calls ({stats.get('tool_steps', 0)} in tool loops), "
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
        _other_findings(rows),
        _changelog_section(changelog, changelog_mode=changelog_mode),
        _methodology(analysis, rows, stats),
        f"*{prompts.REPORT_DISCLAIMER}*",
    ]
    return "\n\n".join(s for s in sections if s)

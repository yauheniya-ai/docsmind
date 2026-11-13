"""Row verification: a small tool loop per row, then checks the model is not trusted with.

The verifier model searches and reads, and answers with verbatim proof passages and a few phrases
whose absence from the other version shows that a clause is new (or removed). Everything it claims
is re-checked here, in code, against the stored text:

- every proof passage must occur verbatim in the document it is attributed to (or in what
  `page_vision` read off the page in this loop);
- its page must be one of the chunks around the claimed page, else the page is dropped;
- for new and removed rows, at least one absence phrase must come from the proof, and none may
  occur in the other version.

A row without proof is not verified: the analyzer removes it from the table and lists it in the report.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from docsmind.agent import prompts
from docsmind.agent.catalog import Catalog, CatalogDoc
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.agent.subagents.change_analysis.rows import (
    extract_json,
    row_for_model,
    row_from_object,
)
from docsmind.agent.subagents.specs import VERIFY_TOOLS
from docsmind.domain.changes import ChangeRow, Nature, Proof
from docsmind.domain.errors import LLMConfigurationError

log = logging.getLogger(__name__)

MIN_FRAGMENT = 8  # characters; shorter pieces of a proof are not evidence
MAX_PROOF_CHARS = 1_500

_CHARS = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
        "\u00a0": " ", "\u00ad": None, "\u200b": None,
    }
)  # fmt: skip


def squash(text: str) -> str:
    """Text as compared by the proof checks: case, spacing, quote and dash styles ignored."""
    return " ".join(text.translate(_CHARS).lower().split())


@dataclass
class DocText:
    doc: CatalogDoc
    squashed: str
    # (first page, squashed text) of every chunk, loaded when a proof's page has to be looked up
    chunk_texts: list[tuple[int, str]] | None = None


def contains_verbatim(passage: str, haystack: str) -> bool:
    """Is `passage` (already squashed or not) in `haystack` (squashed)? An ellipsis in the passage
    splits it into fragments that must each be present."""
    pieces = [squash(p) for p in re.split(r"…|\.\.\.", passage)]
    pieces = [p for p in pieces if p]
    long_pieces = [p for p in pieces if len(p) >= MIN_FRAGMENT]
    if not long_pieces:
        return False
    return all(p in haystack for p in long_pieces)


@dataclass
class Verdict:
    status: Literal["verified", "not_found", "error"]
    note: str = ""
    proof: list[Proof] = field(default_factory=list)
    row: ChangeRow | None = None  # the row the verifier built (gap checks only)
    # True when the check itself went wrong (no usable answer, a call that broke, a claimed proof
    # that is not in the text) rather than reaching a conclusion: the loop is then relaunched.
    retry: bool = False


def _task(
    row: ChangeRow | None,
    entry: str | None,
    a: CatalogDoc,
    b: CatalogDoc,
    *,
    changelog_mode: bool,
    notes: str,
    retrying: bool = False,
) -> str:
    b_role = "the changelog of A (not a previous version)" if changelog_mode else "previous version"
    lines = [f"A (current version) = {a.handle} · {a.title}", f"B ({b_role}) = {b.handle} · {b.title}"]
    if row is not None:
        lines += ["", "<row>", json.dumps(row_for_model(row), ensure_ascii=False), "</row>"]
    if entry is not None:
        lines += ["", "<changelog_entry>", entry, "</changelog_entry>"]
    if notes:
        lines += ["", f"<focus>\n{notes}\n</focus>"]
    if retrying:
        lines += [
            "",
            (
                "An earlier attempt at this check did not produce a usable result. Search again, copy proof "
                "passages exactly from the tool results, and reply with ONLY the JSON object."
            ),
        ]
    return "\n".join(lines)


async def verify_row(
    env: RunEnv,
    *,
    row: ChangeRow | None,
    entry: str | None = None,
    a: DocText,
    b: DocText,
    changelog_mode: bool,
    notes: str = "",
) -> Verdict:
    """Verify one scan row, or (with `entry`, no `row`) check a changelog claim that has no row.

    A check that goes wrong technically is started again, up to `change_verify_max_retries` times:
    only a finished check may conclude "verified" or "no proof". If every attempt fails, the last
    verdict stands (an error leaves the row unverified, with a note)."""
    attempts = env.config.change_verify_max_retries + 1
    verdict = Verdict("error", "The check did not run.", retry=True)
    for attempt in range(attempts):
        if attempt:
            env.verify_retries += 1
        verdict = await _verify_once(
            env, row=row, entry=entry, a=a, b=b, changelog_mode=changelog_mode, notes=notes, retrying=attempt > 0
        )
        if not verdict.retry:
            return verdict
        log.warning("Verification attempt %d of %d went wrong: %s", attempt + 1, attempts, verdict.note)
    if verdict.status == "error":
        verdict.note = f"{verdict.note} (tried {attempts} times)"
    return verdict


async def _verify_once(
    env: RunEnv,
    *,
    row: ChangeRow | None,
    entry: str | None,
    a: DocText,
    b: DocText,
    changelog_mode: bool,
    notes: str,
    retrying: bool,
) -> Verdict:
    system = prompts.VERIFIER
    if changelog_mode:
        system += prompts.VERIFIER_NO_PREVIOUS
    if entry is not None:
        system += prompts.VERIFIER_GAP

    catalog = Catalog([a.doc, b.doc])
    try:
        outcome = await env.loop(
            system=system,
            task=_task(row, entry, a.doc, b.doc, changelog_mode=changelog_mode, notes=notes, retrying=retrying),
            catalog=catalog,
            tool_names=VERIFY_TOOLS,
            max_steps=env.config.change_verify_max_steps,
            max_tokens=2_048,
        )
    except LLMConfigurationError:
        raise  # a missing key or model name: relaunching cannot help
    except Exception as exc:  # noqa: BLE001 - a failed model call is "could not check", never "not found"
        log.warning("Verification loop failed: %s: %s", type(exc).__name__, exc)
        return Verdict("error", f"The check could not run ({type(exc).__name__}).", retry=True)

    answer = extract_json(outcome.text)
    if answer is None:
        return Verdict("error", "The verifier gave no usable result.", retry=True)
    reason = " ".join(str(answer.get("reason") or "").split())[:400]

    if entry is not None:
        row = row_from_object(answer.get("row") or {}, origin="changelog") if answer.get("verified") else None
        if answer.get("verified") and row is None:
            return Verdict("error", "The verifier confirmed the change but gave no usable row.", retry=True)
    if not answer.get("verified"):
        return Verdict("not_found", reason or "No proof was found in the text.")
    assert row is not None

    vision = squash(" ".join(outcome.vision_log))
    proofs, dropped = await _checked_proofs(env, answer.get("proof"), a, b, vision)

    # The verifier says "verified" but its proof is not in the text: the check went wrong, so it is
    # started again (retry) rather than concluded.
    nature = row.nature
    has_a = any(p.doc == "current" for p in proofs)
    has_b = any(p.doc == "previous" for p in proofs)
    if changelog_mode:
        if nature == Nature.REMOVED:
            return Verdict("error", "A removal cannot be proven from a changelog alone.")
        if not has_a:
            return Verdict("not_found", _missing("current version (A)", dropped), proofs, row, retry=True)
    elif nature == Nature.REMOVED:
        if not has_b:
            return Verdict("not_found", _missing("previous version (B)", dropped), proofs, row, retry=True)
    elif nature == Nature.NEW:
        if not has_a:
            return Verdict("not_found", _missing("current version (A)", dropped), proofs, row, retry=True)
    else:
        if not (has_a and has_b):
            side = "current version (A)" if not has_a else "previous version (B)"
            return Verdict("not_found", _missing(side, dropped), proofs, row, retry=True)

    if nature in (Nature.NEW, Nature.REMOVED) and not (changelog_mode and nature == Nature.NEW):
        problem = _absence_problem(answer.get("absence_phrases"), nature, proofs, a, b)
        if problem is not None:
            status, note = problem
            return Verdict(status, note, proofs, row, retry=status == "error")

    return Verdict("verified", reason, proofs, row)


def _missing(side: str, dropped: int) -> str:
    extra = f" ({dropped} quoted passage{'s' if dropped != 1 else ''} did not match the text)" if dropped else ""
    return f"No verbatim proof in the {side}{extra}."


async def _checked_proofs(env: RunEnv, raw, a: DocText, b: DocText, vision: str) -> tuple[list[Proof], int]:
    proofs: list[Proof] = []
    dropped = 0
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        side = a if str(item.get("doc", "")).strip().upper().startswith("A") else b
        key = "current" if side is a else "previous"
        text = " ".join(str(item.get("text") or "").split())[:MAX_PROOF_CHARS]
        in_text = bool(text) and contains_verbatim(text, side.squashed)
        in_vision = bool(text) and not in_text and bool(vision) and contains_verbatim(text, vision)
        if not (in_text or in_vision):
            dropped += 1
            continue
        page = item.get("page")
        page = page if isinstance(page, int) and page > 0 else None
        if in_text and (page is None or not await _page_holds(env, side.doc, page, text)):
            # The model gave no page, or not the right one: the page is a fact of the stored text,
            # so it is looked up in code instead of being left out.
            page = await _find_page(env, side, text)
        proofs.append(Proof(doc=key, text=text, page=page))  # type: ignore[arg-type]
    return proofs, dropped


async def _find_page(env: RunEnv, side: DocText, text: str) -> int | None:
    """The first page of the chunk that holds `text` (or of two neighbouring chunks, for a passage
    that runs over a chunk border). None when no chunk holds it, e.g. text a vision model read."""
    if side.chunk_texts is None:
        try:
            chunks = await env.repo.get_chunks_for_pages(side.doc.version_id, 1, side.doc.page_count or 10_000)
        except Exception:
            log.exception("Page lookup failed")
            return None
        side.chunk_texts = [(int(c.get("page_start") or 0), squash(c.get("content") or "")) for c in chunks]
    texts = side.chunk_texts
    for page, content in texts:
        if page and contains_verbatim(text, content):
            return page
    for (page, first), (_, second) in pairwise(texts):
        if page and contains_verbatim(text, f"{first} {second}"):
            return page
    return None


async def _page_holds(env: RunEnv, doc: CatalogDoc, page: int, text: str) -> bool:
    try:
        chunks = await env.repo.get_chunks_for_pages(doc.version_id, max(1, page - 1), page + 1)
    except Exception:
        log.exception("Page check failed")
        return False
    return contains_verbatim(text, squash(" ".join(c.get("content") or "" for c in chunks)))


def _absence_problem(raw, nature: Nature, proofs: list[Proof], a: DocText, b: DocText) -> tuple[str, str] | None:
    """None when absence from the other version holds, else (status, note)."""
    proof_side = "current" if nature == Nature.NEW else "previous"
    other, other_name = (b, "previous version (B)") if nature == Nature.NEW else (a, "current version (A)")
    proof_text = squash(" ".join(p.text for p in proofs if p.doc == proof_side))
    phrases = []
    for phrase in raw if isinstance(raw, list) else []:
        squashed = squash(str(phrase))
        if len(squashed.split()) >= 3 and squashed in proof_text:
            phrases.append(squashed)
    if not phrases:
        return "error", f"Absence from the {other_name} could not be checked (no usable phrase)."
    for phrase in phrases[:3]:
        if phrase in other.squashed:
            return "not_found", f'The text "{phrase[:80]}" also appears in the {other_name}.'
    return None

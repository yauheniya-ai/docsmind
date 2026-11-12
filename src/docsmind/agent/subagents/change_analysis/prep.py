"""Deterministic preparation for a comparison: code, no model.

    extracted text of A and B
      -> normalize: drop headers, footers and page numbers; one sentence per line
      -> line diff of A against B; diff ratio = changed sentences / sentences in A
      -> mode: identical (stop) | diff (A + bounded diff) | full (A + B)
      -> split into parts when the inputs would not fit one scan call

A is the current version, B the previous one. In a rendered diff, lines marked
`A>` exist only in A (added or reworded there) and lines marked `B>` only in B
(removed or reworded).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from docsmind.agent.harness.tokens import count_tokens

CONTEXT_LINES = 4  # unchanged lines shown either side of a change in a bounded diff
MAX_LINE_CHARS = 800  # a "sentence" longer than this is a table or list run together: break it
MIN_SPLIT_TOKENS = 4_000  # never cut a part smaller than this

# "Page 3", "3 of 10", "- 3 -": page furniture whatever the page. A bare number is only a page
# number when it belongs to a run 1, 2, 3, ... (see `_page_number_lines`): a bare "12" in a table
# can be a requirement.
_PAGE_LABEL = re.compile(
    r"^(?:page\s+\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?|\d{1,4}\s*(?:of|/)\s*\d{1,4}|[-–—]\s*\d{1,4}\s*[-–—])$",
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(r"^\d{1,4}$")
# A line that starts a new unit instead of continuing the previous one: "4.2 Title", "(a)", "•", "Annex A".
_UNIT_START = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+\S|[A-Z]\.\d+(?:\.\d+)*\s|\(?[a-z0-9]{1,4}[\).]\s+\S|[•▪◦●■\-–—*]\s+\S"
    r"|(?:Appendix|Annex|Section|Article|Chapter|Clause|Part)\s+\w+)"
)
_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+){0,3}\.?\s+\S|(?:Appendix|Annex|Section|Article|Chapter|Part)\s+\w+)"
)
_SENTENCE_END = re.compile(r"[.!?:;]['\")\]”]?$")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[“\"'])")
_ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "fig.", "no.", "art.", "sec.", "cf.", "approx.", "inc.", "ltd.",
    "dr.", "mr.", "ms.", "st.", "para.", "vol.", "pp.", "p.", "al.",
}  # fmt: skip


# -- normalize ------------------------------------------------------------------------


def _digits_masked(line: str) -> str:
    return re.sub(r"\d+", "#", line.lower())


def _page_number_lines(lines: list[str], pages: int) -> set[int]:
    """Indexes of bare-number lines that form the longest run n, n+1, n+2, ... in reading order:
    the page numbers. Other bare numbers (table cells, list markers) are not touched."""
    length_at: dict[int, tuple[int, int]] = {}  # number -> (run length, line index) of the best run ending there
    previous: dict[int, int | None] = {}
    for i, line in enumerate(lines):
        if not _BARE_NUMBER.match(line):
            continue
        value = int(line)
        before = length_at.get(value - 1)
        length = (before[0] if before else 0) + 1
        previous[i] = before[1] if before else None
        if value not in length_at or length > length_at[value][0]:
            length_at[value] = (length, i)
    if not length_at:
        return set()
    best_length, end = max(length_at.values(), key=lambda item: item[0])
    if best_length < max(3, int(pages * 0.6)):
        return set()
    run: set[int] = set()
    cursor: int | None = end
    while cursor is not None:
        run.add(cursor)
        cursor = previous[cursor]
    return run


def _is_furniture_candidate(line: str) -> bool:
    return 12 <= len(line) <= 140 and " " in line


def _split_sentences(paragraph: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(paragraph):
        before = paragraph[start : match.start()]
        last_word = before.rsplit(None, 1)[-1].lower() if before.strip() else ""
        if last_word in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", last_word):
            continue  # "e.g.", "Fig.", a single-letter initial: not a sentence end
        parts.append(before.strip())
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def normalize(text: str, *, pages: int | None = None) -> list[str]:
    """Extracted text -> one sentence per line, without page furniture.

    Page numbers go by pattern and sequence. Headers and footers go by repetition: a longish line
    that appears (digits masked) on about a third of all pages or more is page furniture.
    Everything else is kept, including short repeated table cells such as "Yes"."""
    raw = [line.strip() for line in text.replace("\u00ad", "").splitlines()]
    raw = [line for line in raw if line and not _PAGE_LABEL.match(line)]

    pages = pages or max(1, len(text) // 3_000)
    page_numbers = _page_number_lines(raw, pages)
    lines = [line for i, line in enumerate(raw) if i not in page_numbers]

    threshold = max(3, pages) if pages <= 5 else max(3, int(pages * 0.3))
    counts: dict[str, int] = {}
    for line in lines:
        if _is_furniture_candidate(line):
            key = _digits_masked(line)
            counts[key] = counts.get(key, 0) + 1
    lines = [
        line
        for line in lines
        if not (_is_furniture_candidate(line) and counts[_digits_masked(line)] >= threshold)
    ]

    paragraphs: list[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue
        continues = (
            not _SENTENCE_END.search(current)
            and not _UNIT_START.match(line)
            and len(current) + len(line) < MAX_LINE_CHARS
        )
        if continues:
            if current.endswith("-") and line[:1].islower():
                current = current[:-1] + line  # hyphenated line break
            else:
                current = f"{current} {line}"
        else:
            paragraphs.append(current)
            current = line
    if current:
        paragraphs.append(current)

    sentences: list[str] = []
    for paragraph in paragraphs:
        sentences.extend(_split_sentences(re.sub(r"\s+", " ", paragraph)))
    return sentences


# -- diff -------------------------------------------------------------------------------

Opcode = tuple[str, int, int, int, int]


@dataclass
class Prepared:
    a: list[str]  # current version, one sentence per line
    b: list[str]  # previous version
    opcodes: list[Opcode]
    ratio: float  # changed sentences / sentences in A (0..1)
    mode: Literal["identical", "diff", "full"]
    tokens_a: int = 0
    tokens_b: int = 0
    _line_tokens_a: list[int] = field(default_factory=list, repr=False)
    _line_tokens_b: list[int] = field(default_factory=list, repr=False)

    @property
    def identical(self) -> bool:
        return self.mode == "identical"


def prepare(
    text_a: str,
    text_b: str,
    *,
    threshold: float,
    pages_a: int | None = None,
    pages_b: int | None = None,
) -> Prepared:
    a = normalize(text_a, pages=pages_a)
    b = normalize(text_b, pages=pages_b)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    opcodes = matcher.get_opcodes()
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in opcodes if tag != "equal")
    ratio = min(1.0, changed / len(a)) if a else (0.0 if not b else 1.0)
    if changed == 0:
        mode: Literal["identical", "diff", "full"] = "identical"
    else:
        mode = "diff" if ratio < threshold else "full"
    tokens_a = [count_tokens(line) + 1 for line in a]
    tokens_b = [count_tokens(line) + 1 for line in b]
    return Prepared(
        a=a,
        b=b,
        opcodes=opcodes,
        ratio=ratio,
        mode=mode,
        tokens_a=sum(tokens_a),
        tokens_b=sum(tokens_b),
        _line_tokens_a=tokens_a,
        _line_tokens_b=tokens_b,
    )


def prepare_changelog(
    text_a: str, text_b: str, *, pages_a: int | None = None, pages_b: int | None = None
) -> Prepared:
    """A document (A) and its changelog (B): normalized, never diffed (they share no text), always
    given to the scan in full. Every part of A comes with the whole changelog."""
    a = normalize(text_a, pages=pages_a)
    b = normalize(text_b, pages=pages_b)
    tokens_a = [count_tokens(line) + 1 for line in a]
    tokens_b = [count_tokens(line) + 1 for line in b]
    return Prepared(
        a=a,
        b=b,
        opcodes=[("replace", 0, len(a), 0, len(b))],
        ratio=1.0,
        mode="full",
        tokens_a=sum(tokens_a),
        tokens_b=sum(tokens_b),
        _line_tokens_a=tokens_a,
        _line_tokens_b=tokens_b,
    )


def _in_range(op: Opcode, lo: int, hi: int, last: bool) -> bool:
    """Does this opcode belong to the part whose A lines are [lo, hi)? A change belongs to the part
    where it starts (an insertion at the very end belongs to the last part)."""
    tag, i1, i2, _, _ = op
    if tag == "equal":
        return i2 > lo and i1 < hi
    return lo <= i1 < hi or (last and i1 == hi)


def render_diff(prep: Prepared, lo: int = 0, hi: int | None = None, *, context: int = CONTEXT_LINES) -> str:
    """The changes that start in A lines [lo, hi), each with `context` unchanged lines either side.
    Neighbouring changes share their context; `…` marks skipped unchanged text between changes."""
    hi = len(prep.a) if hi is None else hi
    ops = [op for op in prep.opcodes if _in_range(op, lo, hi, hi == len(prep.a))]
    out: list[str] = []
    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            lines = prep.a[max(i1, lo) : min(i2, hi)]
            after_change = idx > 0
            before_change = idx + 1 < len(ops)
            if after_change and before_change:
                shown = lines if len(lines) <= 2 * context else [*lines[:context], "…", *lines[-context:]]
            elif after_change:
                shown = lines[:context]
            elif before_change:
                shown = lines[-context:]
            else:
                shown = []
            out.extend(f"  {line}" for line in shown)
            continue
        out.extend(f"A> {line}" for line in prep.a[i1:i2])
        out.extend(f"B> {line}" for line in prep.b[j1:j2])
    return "\n".join(out)


def part_has_changes(prep: Prepared, part: ScanPart) -> bool:
    last = part.hi == len(prep.a)
    return any(op[0] != "equal" and _in_range(op, part.lo, part.hi, last) for op in prep.opcodes)


def _a_to_b(prep: Prepared, k: int) -> int:
    """Where position k of A lands in B, by the alignment of the diff."""
    for tag, i1, i2, j1, j2 in prep.opcodes:
        if i1 <= k <= i2:
            if k == i1:
                return j1
            if k == i2:
                return j2
            if tag == "equal":
                return j1 + (k - i1)
            return j1 + round((k - i1) / (i2 - i1) * (j2 - j1))
    return len(prep.b)


# -- parts ----------------------------------------------------------------------------------


@dataclass
class ScanPart:
    index: int  # 0-based
    lo: int  # A lines [lo, hi)
    hi: int
    b_lo: int  # B lines [b_lo, b_hi) (used in full mode)
    b_hi: int
    first_line: str
    tokens: int  # A plus the other side

    def label(self, total: int) -> str:
        if total == 1:
            return ""
        return f'Part {self.index + 1} of {total}: A lines {self.lo + 1}-{self.hi}, starting at "{self.first_line[:100]}"'


def _other_tokens(prep: Prepared, lo: int, hi: int, b_lo: int, b_hi: int) -> int:
    if prep.mode == "full":
        return sum(prep._line_tokens_b[b_lo:b_hi])
    return count_tokens(render_diff(prep, lo, hi))


def _part(prep: Prepared, lo: int, hi: int) -> ScanPart:
    b_lo, b_hi = _a_to_b(prep, lo), _a_to_b(prep, hi)
    tokens = sum(prep._line_tokens_a[lo:hi]) + _other_tokens(prep, lo, hi, b_lo, b_hi)
    return ScanPart(0, lo, hi, b_lo, b_hi, prep.a[lo] if lo < len(prep.a) else "", tokens)


def _cut(prep: Prepared, lo: int, hi: int, target_tokens: int) -> int:
    """A cut point in (lo, hi) after about `target_tokens` of A, moved back to the nearest heading line."""
    used = 0
    cut = lo
    while cut < hi and used < target_tokens:
        used += prep._line_tokens_a[cut]
        cut += 1
    cut = min(max(cut, lo + 1), hi - 1)
    window = max(1, (cut - lo) // 7)
    for k in range(cut, max(lo, cut - window), -1):
        if k > lo and _HEADING.match(prep.a[k]) and len(prep.a[k]) <= 120:
            return k
    return cut


def plan_parts(prep: Prepared, *, split_tokens: int, max_input_tokens: int) -> list[ScanPart]:
    """One part when A plus the other side fits `max_input_tokens`. Otherwise A is cut at about
    `split_tokens` at a section boundary, and the other side (the diff, or B) follows the same
    sections. A part whose other side is still too big is cut smaller."""
    n = len(prep.a)
    whole = _part(prep, 0, n)
    if whole.tokens <= max_input_tokens or n < 2:
        return [whole]

    parts: list[ScanPart] = []
    lo = 0
    while lo < n:
        target = split_tokens
        while True:
            fits_in_target = sum(prep._line_tokens_a[lo:n]) <= target
            hi = n if (fits_in_target or n - lo < 2) else _cut(prep, lo, n, target)
            part = _part(prep, lo, hi)
            if part.tokens <= max_input_tokens or hi - lo < 2 or target <= MIN_SPLIT_TOKENS:
                break
            target = max(MIN_SPLIT_TOKENS, target // 2)
        parts.append(part)
        lo = hi
    for i, part in enumerate(parts):
        part.index = i
    return parts


def part_inputs(prep: Prepared, part: ScanPart, total_parts: int) -> str:
    """The user-message body for one scan call: A (this part), then the diff or B."""
    a_text = "\n".join(prep.a[part.lo : part.hi])
    header = part.label(total_parts)
    pieces = []
    if header:
        pieces.append(f"<scope>\n{header}. Report only changes whose current clause lies in this part.\n</scope>")
    pieces.append(f"<document_a role=\"current version\">\n{a_text}\n</document_a>")
    if prep.mode == "full":
        b_text = "\n".join(prep.b[part.b_lo : part.b_hi])
        pieces.append(f"<document_b role=\"previous version\">\n{b_text}\n</document_b>")
    else:
        diff = render_diff(prep, part.lo, part.hi)
        pieces.append(f"<diff>\n{diff}\n</diff>")
    return "\n\n".join(pieces)

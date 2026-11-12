"""The scan: one long-context call per part, no tools, rows streamed as JSONL.

Each line the model writes is parsed the moment it is complete, so rows reach the UI while the
model is still writing. A broken line, a missing `{"done": true}` marker, a cut-off (max tokens)
or a network error relaunches the call with the rows so far and "continue after the last row".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from docsmind.agent import prompts
from docsmind.agent.subagents.change_analysis.env import RunEnv
from docsmind.agent.subagents.change_analysis.prep import (
    Prepared,
    ScanPart,
    part_has_changes,
    part_inputs,
)
from docsmind.agent.subagents.change_analysis.rows import parse_row_line, row_key, row_line
from docsmind.domain.changes import ChangeRow
from docsmind.domain.errors import LLMConfigurationError

log = logging.getLogger(__name__)

OnRow = Callable[[ChangeRow], Awaitable[None]]
OnStatus = Callable[[str], None]


class ScanBroken(Exception):
    """The model's output stopped being usable JSONL."""


class ScanFailed(Exception):
    """A scan part failed on every attempt."""


async def scan(
    env: RunEnv,
    prep: Prepared,
    parts: list[ScanPart],
    *,
    changelog_mode: bool,
    notes: str,
    on_row: OnRow,
    on_status: OnStatus,
) -> dict:
    """Scan every part in order. -> stats {parts, skipped, retries, rows}."""
    system = prompts.SCANNER + (prompts.SCANNER_CHANGELOG if changelog_mode else "")
    stats = {"parts": len(parts), "skipped": 0, "retries": 0, "rows": 0}
    for part in parts:
        if not changelog_mode and not part_has_changes(prep, part):
            stats["skipped"] += 1  # nothing differs here: no model call
            continue
        if len(parts) > 1:
            on_status(f"Scanning part {part.index + 1} of {len(parts)}")
        user = part_inputs(prep, part, len(parts))
        if notes:
            user += f"\n\n<focus>\n{notes}\n</focus>"
        rows, retries = await scan_part(env, system, user, on_row=on_row)
        stats["rows"] += rows
        stats["retries"] += retries
    return stats


async def scan_part(env: RunEnv, system: str, user: str, *, on_row: OnRow) -> tuple[int, int]:
    """One part, relaunched until the model finishes. -> (rows stored, relaunches)."""
    seen: set[str] = set()
    produced: list[ChangeRow] = []
    attempts = 0

    while True:
        message = user
        if produced:
            lines = "\n".join(row_line(r) for r in produced)
            message = f"{user}\n\n{prompts.SCANNER_RESUME}\n<rows_so_far>\n{lines}\n</rows_so_far>"

        buffer = ""
        finished = False

        async def handle(line: str) -> None:
            nonlocal finished
            kind, row = parse_row_line(line)
            if kind == "broken":
                raise ScanBroken(line[:120])
            if kind == "done":
                finished = True
            elif kind == "row" and row is not None:
                key = row_key(row)
                if key not in seen:
                    seen.add(key)
                    produced.append(row)
                    await on_row(row)

        async def on_text(delta: str) -> None:
            nonlocal buffer
            buffer += delta
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                await handle(line)

        try:
            response = await env.call(
                env.scan_llm,
                system,
                message,
                max_tokens=env.config.change_scan_max_output_tokens,
                on_text=on_text,
            )
            if buffer.strip():
                await handle(buffer)  # the last line has no newline
            if response.stop_reason == "max_tokens" and not finished:
                raise ScanBroken("the output was cut off")
            if not finished:
                raise ScanBroken("no done marker")
            return len(produced), attempts
        except asyncio.CancelledError:
            raise
        except LLMConfigurationError:
            raise
        except Exception as exc:
            attempts += 1
            log.warning("Scan call failed (%s: %s); attempt %d", type(exc).__name__, exc, attempts)
            if attempts > env.config.change_scan_max_retries:
                raise ScanFailed(
                    f"The scan stopped after {attempts} attempts ({type(exc).__name__})."
                ) from exc

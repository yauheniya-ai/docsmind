"""Summarizer: compresses old turns so a long conversation never outgrows the
context. Runs at the start of a turn (not in the background) so it can't race
with the next question; it only costs a model call when the verbatim history
has actually exceeded its budget."""

from __future__ import annotations

import logging

from docsmind.agent import prompts
from docsmind.agent.harness.tokens import count_tokens
from docsmind.agent.sources import strip_markers
from docsmind.config import DocsmindConfig
from docsmind.domain.agent import Conversation, Message, StoredMessage, SystemBlock
from docsmind.domain.ports import LLM, ConversationStore

log = logging.getLogger(__name__)


class Summarizer:
    def __init__(self, llm: LLM, config: DocsmindConfig) -> None:
        self.llm = llm
        self.config = config

    async def maybe_compress(
        self,
        conversation: Conversation,
        messages: list[StoredMessage],
        store: ConversationStore,
    ) -> Conversation:
        """Return the conversation with an up-to-date summary. Everything
        before `summarized_upto` is represented by the summary; everything
        after is replayed verbatim."""
        upto = conversation.summarized_upto
        pending = messages[upto:]
        if sum(count_tokens(m.content) for m in pending) <= self.config.agent_history_budget_tokens:
            return conversation

        keep = self.config.agent_recent_turns * 2
        cut = max(upto, len(messages) - keep)
        cut -= cut % 2  # stay on a user/assistant boundary
        if cut <= upto:
            return conversation

        try:
            summary = await self._summarize(conversation.summary, messages[upto:cut])
        except Exception:
            log.exception("Conversation summarization failed; replaying history verbatim")
            return conversation

        await store.set_summary(conversation.id, summary, cut)
        return conversation.model_copy(update={"summary": summary, "summarized_upto": cut})

    async def _summarize(self, existing: str | None, turns: list[StoredMessage]) -> str:
        transcript = "\n\n".join(f"{m.role.upper()}: {strip_markers(m.content)}" for m in turns)
        prompt = (
            f"Existing summary:\n{existing or '(none yet)'}\n\nNew turns to merge:\n{transcript}"
        )
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=[SystemBlock(text=prompts.SUMMARIZER)],
            max_tokens=600,
        )
        summary = response.text.strip()
        if not summary:
            raise ValueError("empty summary")
        return summary

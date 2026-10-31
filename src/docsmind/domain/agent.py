"""Pure data shared by the agent, its LLM adapters, stores and the HTTP layer.

Messages use one provider-neutral shape (`Message`); each LLM adapter converts
to/from its own wire format, so the agent never sees Anthropic- or
OpenAI-specific structures.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "tool"]
StopReason = Literal["end", "tool_use", "max_tokens", "other"]
ToolChoice = Literal["auto", "none"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- LLM wire-neutral types -------------------------------------------------


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Set by the adapter when the model emitted arguments that weren't valid
    # JSON; the tool layer reports this back to the model so it can retry.
    parse_error: str | None = None


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only
    is_error: bool = False  # tool only


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)


class SystemBlock(BaseModel):
    """One piece of the system prompt. Blocks are ordered stable-first;
    `cache=True` marks the end of a prefix that rarely changes, so providers
    with explicit caching (Anthropic) can reuse it between calls."""

    text: str
    cache: bool = False


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_input_tokens += other.cached_input_tokens


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = "end"
    usage: Usage = Field(default_factory=Usage)


# --- agent runtime ------------------------------------------------------------


class AgentEvent(BaseModel):
    """One thing the UI/CLI may want to show while a turn is running."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    steps: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    route: Literal["simple", "complex"] = "simple"
    question: str = ""  # the user's question rewritten to stand on its own


class AgentResult(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    conversation_id: UUID | None = None
    route: str = "simple"
    plan: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)


# --- persistence ------------------------------------------------------------------


class StoredMessage(BaseModel):
    """A finished user/assistant turn half. Tool traffic is deliberately not
    persisted: it's large, noisy, and the assistant's answer already carries
    what mattered (with citations)."""

    role: Literal["user", "assistant"]
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    ordinal: int = 0
    created_at: datetime = Field(default_factory=_now)


class Conversation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = ""
    summary: str | None = None  # rolling summary of messages[:summarized_upto]
    summarized_upto: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


MemoryKind = Literal["fact", "doc_note"]


class MemoryItem(BaseModel):
    kind: MemoryKind
    content: str
    version_id: UUID | None = None  # set for doc_note
    source_conversation_id: UUID | None = None
    created_at: datetime = Field(default_factory=_now)

    @property
    def key(self) -> str:
        """Stable identity used to drop duplicate notes."""
        normalized = " ".join(self.content.lower().split())
        raw = f"{self.kind}|{self.version_id or ''}|{normalized}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

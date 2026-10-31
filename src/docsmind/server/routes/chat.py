"""Chat endpoints backing the Workspace UI: stream a turn with the Mind (SSE),
plus conversation history and agent info for the Activity / Agents pages."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from docsmind.agent.agent import MAX_QUESTION_CHARS, DocsmindAgent
from docsmind.domain.agent import AgentEvent

router = APIRouter(tags=["chat"])

PING_INTERVAL_S = 15.0
PREVIEW_CHARS = 600


def get_agent(request: Request) -> DocsmindAgent:
    """One agent per app: it owns the LLM clients and background tasks."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        agent = DocsmindAgent(request.app.state.config)
        request.app.state.agent = agent
    return agent


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_QUESTION_CHARS)
    conversation_id: UUID | None = None
    # Restrict the Mind to these document versions (None = the whole register).
    version_ids: list[UUID] | None = None
    collection: str | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        return value


def _sse(event: AgentEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"event: {event.type}\ndata: {payload}\n\n"


async def _event_stream(events: AsyncIterator[AgentEvent]) -> AsyncIterator[str]:
    """Frame agent events as Server-Sent Events, with keep-alive comments so
    proxies don't close the connection during a slow model call."""
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(events.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=PING_INTERVAL_S)
            if not done:
                yield ": ping\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            pending = None
            yield _sse(event)
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        aclose = getattr(events, "aclose", None)
        if aclose:
            await aclose()


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    agent = get_agent(request)
    if body.conversation_id is not None and await agent.conversations.get(body.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    events = agent.stream(
        body.message,
        conversation_id=body.conversation_id,
        version_ids=body.version_ids,
        collection=body.collection,
    )
    return StreamingResponse(
        _event_stream(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
async def list_conversations(request: Request, limit: int = 50) -> list[dict]:
    rows = await get_agent(request).conversations.list(limit=min(max(limit, 1), 200))
    out = []
    for row in rows:
        seen: dict[str, dict] = {}
        for citation in row.get("last_citations") or []:
            version_id = citation.get("version_id")
            if version_id and version_id not in seen:
                seen[version_id] = {"version_id": version_id, "title": citation.get("document_title", "")}
        answer = row.get("last_answer") or ""
        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "message_count": row["message_count"],
                "last_answer": answer[:PREVIEW_CHARS],
                "sources": list(seen.values()),
            }
        )
    return out


@router.get("/conversations/{conversation_id}")
async def get_conversation(request: Request, conversation_id: UUID) -> dict:
    store = get_agent(request).conversations
    conversation = await store.get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = await store.messages(conversation_id)
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "citations": m.citations,
                "meta": m.meta,
                "created_at": m.created_at,
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(request: Request, conversation_id: UUID) -> dict:
    if not await get_agent(request).conversations.delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"deleted": True, "id": str(conversation_id)}


@router.get("/agent")
async def agent_info(request: Request) -> dict:
    return get_agent(request).describe()

"""End-to-end turns of the Mind with a scripted LLM, a fake repository and
in-memory stores: routing, planning, tool use, delegation, citation
verification, persistence, memory and history."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from fakes import text, tool_use

from docsmind.adapters.storage.memory import InMemoryConversationStore, InMemoryMemoryStore
from docsmind.agent.agent import FALLBACK_ANSWER, DocsmindAgent
from docsmind.domain.agent import Usage


def make_agent(config, llm, repo, *, fast=None):
    return DocsmindAgent(
        config,
        llm=llm,
        fast_llm=fast or llm,
        repository=repo,
        conversations=InMemoryConversationStore(),
        memory=InMemoryMemoryStore(),
    )


async def collect(agent, question, **kw):
    return [e async for e in agent.stream(question, **kw)]


def types(events):
    return [e.type for e in events]


async def test_simple_turn_searches_cites_and_persists(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "How long are invoices kept?"}'))
    llm.queue(
        "executor",
        tool_use(("search", {"query": "invoices retention"})),
        text("Invoices are kept for ten years [S1]. Logs for 90 days [S1, S7]."),
    )
    llm.queue("memory", text('{"facts": ["User works in finance"], "doc_notes": [{"document": "D1", "note": "Retention periods are on p.2"}, {"document": "D9", "note": "bogus"}]}'))
    agent = make_agent(config, llm, repo)

    events = await collect(agent, "How long are invoices kept?")
    await agent.drain()

    assert types(events)[:2] == ["route", "tool_start"]
    assert "plan" not in types(events)  # simple route skips the planner
    assert types(events)[-2:] == ["answer", "done"]
    answer = next(e for e in events if e.type == "answer").data
    assert answer["answer"] == "Invoices are kept for ten years [S1]. Logs for 90 days [S1]."  # S7 was invented
    assert [c["handle"] for c in answer["citations"]] == ["S1"]
    assert answer["citations"][0]["document_title"] == "Retention Policy"
    assert answer["citations"][0]["page_start"] == 2

    # Persisted as a user/assistant pair, with citations.
    stored = await agent.conversations.messages(UUID(answer["conversation_id"]))
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].citations[0]["handle"] == "S1"

    # Memory: valid fact + note stored, note for unknown document dropped.
    recalled = await agent.memory.recall(version_ids=[repo.v1], limit=10)
    assert {m.kind for m in recalled} == {"fact", "doc_note"}
    assert len(recalled) == 2


async def test_complex_turn_plans_and_delegates_in_parallel(config, llm, repo):
    llm.queue("router", text('{"route": "complex", "question": "Compare retention and security policies"}'))
    llm.queue("planner", text('{"steps": ["Find retention rules in D1 (delegate)", "Find security rules in D2 (delegate)", "Synthesise with citations"]}'))
    llm.queue(
        "executor",
        tool_use(
            ("delegate", {"subagent": "researcher", "task": "Find retention periods in D1.", "documents": ["D1"]}),
            ("delegate", {"subagent": "researcher", "task": "Find security rules in D2.", "documents": ["D2"]}),
        ),
        text("Retention: invoices 10 years [S1]. Security: encryption mandatory [S2]."),
    )
    llm.queue(
        "subagent",
        tool_use(("search", {"query": "retention invoices"})),
        text("Invoices are kept ten years [S1]."),
        tool_use(("search", {"query": "encryption"})),
        text("Encryption at rest is mandatory [S2]."),
    )
    llm.queue("memory", text('{"facts": [], "doc_notes": []}'))
    agent = make_agent(config, llm, repo)

    events = await collect(agent, "Compare the retention and security policies")
    await agent.drain()

    t = types(events)
    assert t.index("route") < t.index("plan") < t.index("subagent_start")
    plan = next(e for e in events if e.type == "plan").data["steps"]
    assert len(plan) == 3
    assert t.count("subagent_start") == 2 and t.count("subagent_end") == 2
    # Subagent tool activity is tagged so the UI can nest it.
    inner = [e for e in events if e.type == "tool_start" and e.data["name"] == "search"]
    assert inner and all("subagent" in e.data for e in inner)

    answer = next(e for e in events if e.type == "answer").data
    assert {c["handle"] for c in answer["citations"]} == {"S1", "S2"}  # shared registry across subagents

    # Subagents got a fresh context: only the task, no conversation or plan.
    for call in llm.calls_for("subagent"):
        if len(call["messages"]) == 1:
            assert call["messages"][0].role == "user"
            assert "<plan>" not in "".join(b.text for b in call["system"])
    # The executor's plan reached its system prompt (volatile block, after the cache mark).
    exec_system = llm.calls_for("executor")[0]["system"]
    assert exec_system[1].cache is True and "<plan>" in exec_system[-1].text and exec_system[-1].cache is False


async def test_second_turn_gets_history_without_stale_citation_markers(config, llm, repo):
    agent = make_agent(config, llm, repo)
    llm.queue("router", text('{"route": "simple", "question": "q1"}'), text('{"route": "simple", "question": "And logs?"}'))
    llm.queue("executor", tool_use(("grep", {"text": "ten years"})), text("Ten years [S1]."), text("Ninety days."))
    llm.queue("memory", text("{}"), text("{}"))

    first = await collect(agent, "How long are invoices kept?")
    conversation_id = next(e for e in first if e.type == "answer").data["conversation_id"]
    await agent.drain()
    await collect(agent, "And logs?", conversation_id=UUID(conversation_id))

    last_exec = llm.calls_for("executor")[-1]["messages"]
    assert [m.role for m in last_exec] == ["user", "assistant", "user"]
    assert last_exec[1].content == "Ten years."  # [S1] stripped: handles are per turn
    assert last_exec[2].content == "And logs?"


async def test_step_budget_forces_a_final_answer_with_tools_disabled(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    looping = tool_use(("search", {"query": "retention"}))
    llm.queue("executor", looping, looping, looping, text("Best effort answer [S1]."))
    llm.queue("memory", text("{}"))
    agent = make_agent(config, llm, repo)  # agent_max_steps=4

    events = await collect(agent, "keep searching")
    calls = llm.calls_for("executor")
    assert [c["tool_choice"] for c in calls] == ["auto", "auto", "auto", "none"]
    assert next(e for e in events if e.type == "answer").data["answer"] == "Best effort answer [S1]."


async def test_empty_final_answer_falls_back_and_skips_memory(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    llm.queue("executor", text(""))
    agent = make_agent(config, llm, repo)
    events = await collect(agent, "hi")
    assert next(e for e in events if e.type == "answer").data["answer"] == FALLBACK_ANSWER
    assert not llm.calls_for("memory")


async def test_router_failure_defaults_to_simple(config, llm, repo):
    llm.queue("router", text("I cannot decide"))
    llm.queue("executor", text("Hello!"))
    llm.queue("memory", text("{}"))
    agent = make_agent(config, llm, repo)
    events = await collect(agent, "hello")
    route = next(e for e in events if e.type == "route").data
    assert route["route"] == "simple" and route["question"] == "hello"


async def test_tool_errors_go_back_to_the_model_not_the_user(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    llm.queue(
        "executor",
        tool_use(("read_pages", {"document": "D9", "start_page": 1})),
        text("That document is not available."),
    )
    llm.queue("memory", text("{}"))
    agent = make_agent(config, llm, repo)
    events = await collect(agent, "read D9")
    end = next(e for e in events if e.type == "tool_end")
    assert end.data["ok"] is False
    assert "error" not in types(events)
    tool_result = llm.calls_for("executor")[1]["messages"][-1]
    assert tool_result.role == "tool" and tool_result.is_error and "Valid documents" in tool_result.content


async def test_scope_restricts_the_register(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    llm.queue("executor", text("ok"))
    llm.queue("memory", text("{}"))
    agent = make_agent(config, llm, repo)
    await collect(agent, "hi", version_ids=[repo.v2])
    system = "".join(b.text for b in llm.calls_for("executor")[0]["system"])
    assert "Security Policy" in system and "Retention Policy" not in system


async def test_unknown_conversation_emits_error_event(config, llm, repo):
    agent = make_agent(config, llm, repo)
    events = await collect(agent, "hi", conversation_id=uuid4())
    assert types(events) == ["error"] and events[0].data["code"] == "conversation_not_found"


async def test_executor_prompt_keeps_stable_prefix_identical_across_turns(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "a"}'), text('{"route": "simple", "question": "b"}'))
    llm.queue("executor", text("one"), text("two"))
    llm.queue("memory", text('{"facts": ["User prefers brevity"], "doc_notes": []}'), text("{}"))
    agent = make_agent(config, llm, repo)
    await collect(agent, "a")
    await agent.drain()
    await collect(agent, "b")
    first, second = (c["system"] for c in llm.calls_for("executor"))
    assert first[:2] == second[:2]  # role prompt + register: byte-identical => cache hit
    assert "User prefers brevity" in second[-1].text and second[-1].cache is False


async def test_summarizer_compresses_old_turns_when_history_is_long(config, llm, repo):
    agent = make_agent(config, llm, repo)
    store = agent.conversations
    conversation = await store.create("long chat")
    from docsmind.domain.agent import StoredMessage

    long = "word " * 120
    await store.append(
        conversation.id,
        [StoredMessage(role=r, content=long) for _ in range(6) for r in ("user", "assistant")],
    )
    llm.queue("summarizer", text("The user asked about retention; answers cited D1 p.2."))
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    llm.queue("executor", text("ok"))
    llm.queue("memory", text("{}"))

    await collect(agent, "next question", conversation_id=conversation.id)
    updated = await store.get(conversation.id)
    assert updated.summary and updated.summarized_upto == 12 - config.agent_recent_turns * 2
    system = "".join(b.text for b in llm.calls_for("executor")[0]["system"])
    assert "<conversation_summary>" in system
    replayed = llm.calls_for("executor")[0]["messages"]
    assert len(replayed) <= config.agent_recent_turns * 2 + 1


async def test_consumer_disconnect_cancels_the_turn(config, llm, repo):
    started = asyncio.Event()

    async def slow(*_a, **_k):
        started.set()
        await asyncio.sleep(30)

    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    agent = make_agent(config, llm, repo)
    original = llm.complete

    async def complete(messages, **kw):
        if llm.role_of(kw["system"]) == "executor":
            await slow()
        return await original(messages, **kw)

    llm.complete = complete  # type: ignore[method-assign]
    stream = agent.stream("hi")
    first = await stream.__anext__()
    assert first.type == "route"
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(stream.aclose(), 2)  # must not hang
    assert not agent._background or all(t.done() for t in agent._background)


async def test_usage_is_accumulated_across_roles(config, llm, repo):
    llm.queue("router", text('{"route": "simple", "question": "x"}'))
    llm.queue("executor", text("ok"))
    llm.queue("memory", text("{}"))
    agent = make_agent(config, llm, repo)
    events = await collect(agent, "hi")
    usage = Usage(**next(e for e in events if e.type == "answer").data["usage"])
    assert usage.input_tokens == 20 and usage.output_tokens == 10  # router + executor


def test_events_are_json_serialisable(config, llm, repo):
    async def go():
        llm.queue("router", text('{"route": "simple", "question": "x"}'))
        llm.queue("executor", tool_use(("search", {"query": "retention"})), text("ok [S1]"))
        llm.queue("memory", text("{}"))
        agent = make_agent(config, llm, repo)
        return await collect(agent, "hi")

    for event in asyncio.run(go()):
        json.dumps(event.data, default=str)

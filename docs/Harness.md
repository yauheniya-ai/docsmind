# The harness

This page explains what "the harness" means in docsmind, what each part does, and how one
question travels through it. The diagram is [harness_diagram.mmd](harness_diagram.mmd)
(rendered: [harness_diagram.png](harness_diagram.png)).

## Two meanings, one idea

**The harness is everything around the model that is ordinary code, not model output.** The
model decides *what to say or which tool to call*. The harness decides *what the model sees,
what it may do, when it must stop, and what is kept afterwards*.

The word is used at two levels:

1. **The harness package**, [src/docsmind/agent/harness/](../src/docsmind/agent/harness/): four
   small modules (context builder, summarizer, memory writer, token counter). These feed
   every agent role.
2. **The ten-part harness-engineering checklist** in the diagram: context, loop, tools,
   memory, orchestration, guardrails, evals, human-in-the-loop, observability. Most of those
   live in other files (the loop in `agent/loop.py`, tools in `agent/tools/`, and so on).

## One question, end to end

Everything below happens in `DocsmindAgent._turn` ([agent/agent.py](../src/docsmind/agent/agent.py)).

```
1. gather       load conversation, summarize if too long, build the document register,
                recall memory notes
2. route        small model: simple | complex | delegate
3. plan         only for "complex": small model writes a short step list
4. execute      tool loop (parallel tools, step budget, forced last answer)
                  └─ delegate -> Change Analyzer runs its own loop in a fresh context
5. finalize     code verifies citations [S#], drops invented ones, renumbers the rest
6. persist      save answer + citations + trace + usage to the conversation
7. remember     background: small model proposes notes, code validates and stores them
```

Steps 1, 5 and 6 are pure code. Steps 2, 3, 4 and 7 call models, but each call is built by
the context builder and bounded by the loop.

## The parts

### Context builder, [context.py](../src/docsmind/agent/harness/context.py)

The only place that assembles prompts. Order matters, because providers cache a prompt
*prefix*:

| Layer | Changes | Cached |
| --- | --- | --- |
| Role prompt and rules | never | yes |
| Document register (titles, collections, versions, stored analyses) | when documents change | yes (cache mark ends here) |
| Memory notes, conversation summary, plan | every turn | no |
| Recent history, then the new question | every turn | no |

Anthropic gets explicit `cache_control` markers; OpenAI caches prefixes by itself. Each role
gets its own bundle: the router sees only the last 2 exchanges, the planner sees no history,
the executor sees the history within its budget. Citation markers are stripped from old
messages, since `S1` in a past turn would collide with `S1` in this one. Tool results,
memory and documents are all marked as untrusted data in the prompt.

### Loop, [loop.py](../src/docsmind/agent/loop.py)

`model call -> tool calls? -> run them in parallel -> feed results back -> repeat`. The rules
are in code, not in the prompt:

- **Step budget** (`AGENT_MAX_STEPS`, default 8). On the last step tools are switched off,
  so the model has to answer with what it has.
- **Parallel tools**, at most `AGENT_MAX_PARALLEL_TOOLS` (4) at once.
- **Context pruning**: when the transcript passes `AGENT_CONTEXT_BUDGET_TOKENS` (24,000), the
  oldest tool *results* are replaced by a placeholder. Calls stay visible so the model knows
  what it tried; the two most recent rounds are never pruned.
- Text the model writes *before* a tool call is sent as a `note`, not as the answer.

The Change Analyzer uses the same loop with its own tools and limits.

### Tools, [agent/tools/](../src/docsmind/agent/tools/)

Each tool has a name, a schema and a result. The registry adds the safety net: a 60 s
timeout per call (`AGENT_TOOL_TIMEOUT_S`; whole-pipeline tools like `delegate` are exempt),
results cut at 12,000 characters, and a bad argument becomes an error message *to the model*
so it can retry, never a crash for the user. Web tools are only offered for public documents,
so a query about an internal document can't leak.

### Summarizer, [summarizer.py](../src/docsmind/agent/harness/summarizer.py)

Keeps long chats inside the context. At the start of a turn it checks the not-yet-summarized
history. Only if that exceeds `AGENT_HISTORY_BUDGET_TOKENS` (6,000) does it call the small
model: older turns are merged into a running summary (max 600 tokens), and the last
`AGENT_RECENT_TURNS` (4) exchanges stay verbatim. The conversation stores how far it has
summarized, so nothing is summarized twice. If summarizing fails, history is replayed as is.

### Memory, [memory.py](../src/docsmind/agent/harness/memory.py)

Notes that survive between conversations. Two kinds: `fact` (something the user said about
themselves) and `doc_note` (a verified navigation hint about one document). The model never
writes memory directly. After the answer, in the background, a small model *proposes* up to
3 notes of each kind (240 characters max); code checks them (a note about an unknown
document is dropped) and stores them. Recall is by document scope, newest first, up to 20,
and rendered as "may be outdated, verify before relying". Failure is silent and never
affects the user.

Summarizer vs memory: the summarizer compresses *this* conversation so it fits; memory keeps
a few durable facts for *future* ones.

### Token counting, [tokens.py](../src/docsmind/agent/harness/tokens.py)

`tiktoken` when available, otherwise characters / 4. Budgets only need to be roughly right.

### Cost and trace (in the agent, not the package)

Every model call adds to a `Usage` object (uncached, cached-read and cache-write tokens), and
[pricing.py](../src/docsmind/pricing.py) turns it into USD per model. Each answer is saved
with its route, plan, step count, usage, duration and a trace of up to 600 events, so the UI
can replay what happened after a reload.

## Status against the ten parts

| Part | Status | What exists | What is missing |
| --- | --- | --- | --- |
| 2 Loop | done | step budget, forced answer, parallel tools, pruning | |
| 3 Context | done | stable-first prompt, cache mark, history budget, summary | |
| 4 Tools | done | schemas, timeouts, truncation, errors back to the model | |
| 6 Orchestration | done | router, planner, delegate to a subagent with fresh context | |
| 5 Memory | partial | history + summary, long-term notes with validation | semantic (vector) recall of notes |
| 7 Guardrails | partial | untrusted-data rule, verified citations, verbatim proof checks, caps, web only for public documents | input/output filtering, scoped access, approval before file writes |
| 9 Human in the loop | partial | Change Analyzer rows can be edited, deleted, restored and re-verified | approval gates for the Mind's own actions |
| 10 Observability | partial | saved trace, live activity, usage and cost per run | pluggable tracing (MLflow, Langfuse), metrics, alerts |
| 8 Evals | planned | nothing yet | golden sets, trajectory vs outcome, LLM-as-judge, regression runs |

## Knobs

All are `DOCSMIND_*` settings, defined in [config.py](../src/docsmind/config.py):
`AGENT_FAST_MODEL`, `AGENT_MAX_STEPS`, `AGENT_MAX_OUTPUT_TOKENS`, `AGENT_MAX_PARALLEL_TOOLS`,
`AGENT_TOOL_TIMEOUT_S`, `AGENT_TOOL_RESULT_MAX_CHARS`, `AGENT_CONTEXT_BUDGET_TOKENS`,
`AGENT_HISTORY_BUDGET_TOKENS`, `AGENT_RECENT_TURNS`, `AGENT_MEMORY_ENABLED`.

The small "fast" model (router, planner, summarizer, memory writer) defaults to the main
model; set `AGENT_FAST_MODEL` to a cheaper one to cut cost.

The **Agents** page in the UI shows the live prompts, limits and prices for all of this.

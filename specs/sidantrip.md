# sidantrip — Tech Spec

> The hero open-source repo. CrewAI-powered agent service + bundled activity data + specs. Doubles as a case study for the AI community.

## 1. Overview

| Item | Value |
|---|---|
| Repo | `sidantrip` (public, AGPL-3.0) |
| Language | Python 3.10+ |
| Agent Framework | CrewAI 1.9+ (multi-agent crews) + LiteLLM (direct planner chat) |
| API Layer | FastAPI + uvicorn |
| Streaming | SSE via `sse-starlette` |
| Queue | Redis (BRPOP consumer for async jobs) |
| LLM Support | Claude, GPT, Gemini (via LiteLLM / CrewAI native LLM) |
| Hosting | Railway / Fly.io (containerized, same cluster as API) |

This is the **main open-source project**. It contains the AI agent service, bundled seed activity data, schema templates, compile pipeline, and technical specs. `sidantrip-db` is included as a git submodule under `data/`.

When deployed as part of the SidanTrip product, this service is **internal only** — `sidantrip-api` is the only client.

## 2. Architecture

```
                    ┌───────────────────────────────────────────────────┐
                    │  sidantrip (Python)                                │
                    │                                                    │
┌─────────┐  HTTP  │  ┌──────────────┐     ┌─────────────────────────┐ │
│sidantrip│───────▶│  │ FastAPI      │────▶│ Planner (LiteLLM)       │ │
│-api     │◀──SSE──│  │ /api/planner │     │  • Direct LLM streaming │ │
│         │        │  │              │     │  • DB Tools              │ │
│         │        │  └──────────────┘     │  • User Memory context   │ │
│         │        │                       └─────────────────────────┘ │
│         │ Redis  │  ┌──────────────┐     ┌─────────────────────────┐ │
│         │ queue  │  │ Worker       │────▶│ ResearcherCrew (CrewAI)  │ │
│         │───────▶│  │ (BRPOP loop) │     │  • Researcher Agent     │ │
│         │◀──HTTP─│  │              │     │  • Reviewer Agent       │ │
└─────────┘webhook │  └──────────────┘     │  • Web Search Tools     │ │
                    │                       └─────────────────────────┘ │
                    │                                                    │
                    │  ┌────────────────────────────────────────────┐   │
                    │  │ Activity DB (bundled data/ submodule)      │   │
                    │  │  data/destinations/seoul/...               │   │
                    │  └────────────────────────────────────────────┘   │
                    └───────────────────────────────────────────────────┘
```

## 3. Hybrid Framework Approach

The repo uses **two approaches** — chosen based on what each agent actually needs:

| Component | Framework | Why |
|---|---|---|
| Planner (chat) | LiteLLM direct | Needs token-level streaming, stateful conversation, fast iteration. Single agent — no orchestration needed. |
| Researcher (pipeline) | CrewAI | Multi-agent handoff (Researcher → Reviewer). Needs tool orchestration, memory. CrewAI's sweet spot. |
| Future crews | CrewAI | Translation, Budget Optimizer, Review Pipeline — all multi-agent. |

This makes a more interesting case study than pure CrewAI — it shows developers **when** to use an orchestration framework and when direct LLM calls are better.

## 4. Project Structure

```
sidantrip/                              ← the hero repo
├── README.md                           ← case study README with architecture, demo GIF
├── LICENSE                             ← AGPL-3.0
├── pyproject.toml
├── docker-compose.yml                  ← one command to run everything
├── Dockerfile
├── .env.example
│
├── data/                               ← git submodule → sidantrip-db
│   ├── destinations/seoul/
│   ├── schema/
│   └── scripts/compile_index.py
│
├── specs/                              ← tech specs for all repos
│   ├── sidantrip.md                    ← this file
│   ├── sidantrip-api.md
│   └── sidantrip-app.md
│
├── src/sidantrip/
│   ├── __init__.py
│   ├── main.py                         ← CLI entry point
│   ├── server.py                       ← FastAPI production entry
│   ├── worker.py                       ← Redis queue consumer
│   │
│   ├── planner/                        ← LiteLLM direct (streaming chat)
│   │   ├── agent.py                    ← PlannerAgent class
│   │   ├── prompts.py                  ← System prompt builder
│   │   └── memory.py                   ← User memory manager
│   │
│   ├── crews/                          ← CrewAI (multi-agent pipelines)
│   │   ├── __init__.py
│   │   ├── researcher_crew.py
│   │   └── config/
│   │       ├── agents.yaml
│   │       └── tasks.yaml
│   │
│   └── tools/                          ← Shared tools (used by both)
│       ├── __init__.py
│       └── db_tools.py
│
└── tests/
    ├── test_planner.py
    ├── test_researcher.py
    └── test_tools.py
```

## 5. Planner Agent (LiteLLM Direct)

The main conversational agent. Uses LiteLLM for direct streaming to avoid CrewAI overhead.

**Why not CrewAI for the Planner?**
- CrewAI is "run crew → get result." The Planner needs an ongoing conversation with state between turns.
- Token-level streaming is critical for chat UX. CrewAI only provides task-level callbacks.
- Single agent with tools — no multi-agent orchestration needed.

**Flow per user message:**
1. `sidantrip-api` sends request with trip context + user message + itinerary state + **user memory**
2. PlannerAgent builds system prompt (personality + activity context + user preferences)
3. Calls LiteLLM `completion()` with streaming enabled
4. Tokens stream back via SSE (event types defined in project-spec.md)
5. On completion: parse response for JSON deltas (format defined in project-spec.md). Delta parsing happens here — the planner module buffers tokens, detects ` ```json ` fences, and emits separate `token` and `delta` SSE events. See `architecture-decisions.md` AD-07.

**User memory injection:** The user's memory profile (schema defined in project-spec.md) is injected into the system prompt as a `## Traveler Profile` section, so the planner naturally adapts its suggestions without being told each time.

**Response format:** The agent embeds itinerary deltas in its text response as fenced ```json blocks. See product-spec.md for the delta format and SSE event contract.

### 5.2 Delta Parsing Algorithm

The planner module splits the raw LLM stream into separate `token` and `delta` SSE events using a state machine with a retry fallback.

**Primary path — fence parsing (state machine):**

| State | Behavior |
|---|---|
| STREAMING_TEXT | Buffer tokens, emit as `token` SSE events. When ` ```json ` detected → switch to BUFFERING_JSON, flush preceding text as final `token` event |
| BUFFERING_JSON | Accumulate tokens in JSON buffer (not emitted to client). When closing ` ``` ` detected → parse + validate JSON. If valid → emit `delta` event. If invalid → trigger retry. Switch back to STREAMING_TEXT |

On stream end while still in BUFFERING_JSON (LLM cut off mid-JSON) → trigger retry.

**Validation before emitting delta:**
1. Valid JSON
2. Has `deltas` array
3. Each delta has valid `action` (add/remove/move/clear_day)
4. For `add`: `activity_id` exists in index (fuzzy match if not exact)
5. For `add`: `start_time`/`end_time` present and properly formatted

**Retry fallback — second LLM call:**

When fence parsing fails (bad JSON, missing fence, invalid schema):
1. Send the failed text to a low-temperature LLM call with a structured extraction prompt: "Extract the itinerary changes from this response as valid JSON. Output ONLY the JSON."
2. Retry succeeds + validates → emit `delta` event
3. Retry also fails → store error context for next turn (see AD-11: conversational clarification with user)

The retry call is cheap (~$0.001) and fires on ~5-10% of responses. It adds 1-3 seconds latency only on failure.

**Token batching:** Text tokens are emitted as `token` SSE events in small batches (every ~5 tokens or ~50ms, whichever comes first) for smooth client-side rendering.

### 5.1 Planner Personality & Behavior Guidelines

**Brand voice:** Casual friend. Warm, slightly cheeky, natural language. Not robotic, not overly formal. Matches the 是但 (whatever/easygoing) brand.

**Target audience:** Busy people who don't want to spend effort on trip planning. They want a plan handed to them, not a menu of options.

**Core behaviors:**
- **Decisive by default.** Make opinionated choices. "I've planned your Day 1 — here's what we're doing" not "Here are 5 options, which do you prefer?" Fill the whole trip in the first few messages.
- **Proactive.** Suggest activities without being asked. Fill gaps in the day. Point out scheduling issues. Offer alternatives for weak spots.
- **Short messages.** These users won't read paragraphs. Quick, punchy, get to the point.
- **Minimize back-and-forth.** Don't ask unnecessary questions. Make decisions, let users override.
- **Adapt on pushback.** When the user disagrees or asks questions, gradually shift into clarification mode — offer alternatives, ask preferences. Each pushback makes the agent slightly more consultative for that conversation.
- **DB-first.** Only recommend activities from the database. If nothing fits, say so honestly and offer to research (v0.2+).
- **Context-aware.** Respect opening hours, travel time between activities, meal timing, physical fatigue. Don't overschedule.
- **Non-travel deflection.** Brief, friendly redirect back to planning. "Haha good question! But let's figure out your Day 2 first 😄"
- **Group trips (v0.3).** Address people by name. Navigate preference conflicts diplomatically — suggest compromises, not one-sided picks.

**Always dual output:** Every response that changes the itinerary must include both chat text AND fenced JSON deltas. Text-only responses are fine for clarifications and conversation.

Detailed prompt wording will be iterated during implementation and testing. These guidelines define the target behavior.

## 6. Researcher Crew (CrewAI)

Two-agent pipeline that researches new activities and validates them. Runs asynchronously via Redis queue.

**Agents:**
1. `researcher` — uses web search + scraping to find activities, produces YAML entries
2. `reviewer` — validates entries against schema, checks accuracy, approves/rejects

**Tools:**
- Researcher: `SerperDevTool`, `ScrapeWebsiteTool`, `load_schema_template`, `load_city_index`, `load_city_meta`
- Reviewer: `load_schema_template`, `load_city_index`

**Flow:**
1. Receive research brief from queue (destination, category, count, neighborhood focus)
2. Researcher agent searches the web, produces YAML entries
3. Reviewer agent validates each entry against schema
4. Return approved entries + review report
5. Webhook callback to `sidantrip-api` with results

Future crews (Translation, Budget Optimizer, Review Pipeline) listed in `project-spec.md` §4.4.

## 7. Session Management

The agent service is **stateless per request** — all state is owned by `sidantrip-api` and passed in with each call.

**What `sidantrip-api` sends per request:**
```python
class PlannerChatRequest(BaseModel):
    destination: str
    start_date: str
    end_date: str
    accommodation: str | None = None
    message: str
    itinerary_state: dict          # Current itinerary JSON
    conversation_history: list     # Last N messages (truncated/summarized)
    user_memory: dict | None       # User preference profile (schema in project-spec.md)
    llm_model: str | None = None
```

**Context window budget (~1M for Gemini Pro):**

| Component | Tokens (est.) | Strategy |
|---|---|---|
| System prompt | ~1,500 | Static per destination |
| Activity index | ~12,000 | Three-layer index (62% savings) |
| Itinerary state | ~500 | Compact JSON |
| User memory | ~5,000 | Global profile + destination memory + learned facts (v0.3) |
| Last 20 messages (verbatim) | ~10,000 | MVP |
| Compact summary (messages 21-100) | ~1,500 | v0.2 |
| **Total baseline** | **~30,500** | ~3% of Gemini Pro context window |

Conversation history strategy is defined in `product-spec.md` §2.5 and `architecture-decisions.md` AD-12. `sidantrip-api` owns the implementation — the agent receives pre-built context per request.

## 8. User Memory (Agent-Side)

The user's memory profile (schema and lifecycle defined in project-spec.md) is injected into the Planner's system prompt as a natural-language `## Traveler Profile` section:

```
## Traveler Profile
This traveler prefers a moderate pace with late mornings. They enjoy temples,
markets, and local culture but avoid shopping malls. They're adventurous with
food (likes Korean BBQ and street food, mid-range budget) but allergic to
shellfish. They typically travel as a couple and prefer public transit.
Accommodation style: central hotel.
```

The Planner sees this as context and naturally adapts — suggesting late-morning starts, avoiding shellfish restaurants, clustering activities near transit stations — without the user having to repeat preferences every trip.

Memory storage, extraction, and lifecycle are owned by `sidantrip-api`.

## 9. FastAPI Server

```python
# server.py — production entry point

from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="SidanTrip Agent Service", version="0.1.0")

@app.post("/api/planner/chat")
async def planner_chat(request: PlannerChatRequest):
    """
    Synchronous chat endpoint. Streams tokens via SSE.
    Called by sidantrip-api's ChatService.
    """
    ...

@app.post("/api/researcher/submit")
async def submit_research(request: ResearchRequest):
    """
    Queue a research job. Returns job ID immediately.
    Results delivered via webhook.
    """
    ...

@app.get("/api/health")
async def health():
    return {"status": "ok", "agents": ["planner", "researcher"]}
```

## 10. LLM Configuration

**Planner (LiteLLM direct):**
```python
import litellm

response = litellm.completion(
    model="anthropic/claude-sonnet-4-20250514",  # or gpt-4o, gemini/gemini-2.5-flash
    messages=[...],
    stream=True,
    temperature=0.7,
)
```

**Researcher Crew (CrewAI native):**
```python
from crewai import LLM
llm = LLM(model="gemini/gemini-2.5-pro", temperature=0.3)  # Always Gemini Pro for DB quality
```

Default model set via `SIDANTRIP_LLM_MODEL` env var. Can be overridden per-request.

## 11. Activity Database Access

The activity database is bundled as a git submodule under `data/`. Tools read YAML files directly from disk.

```yaml
# docker-compose.yml — no external volume needed
services:
  agent:
    build: .
    environment:
      SIDANTRIP_DB_PATH: /app/data
```

In production, a GitHub Action on `sidantrip-db` PR merge calls `POST /api/admin/reload-index` to hot-reload the in-memory indices without restart. Activity IDs are immutable, so hot-reload is safe for in-flight requests. See `architecture-decisions.md` AD-13.

## 12. Environment Variables

```env
# LLM (at least one required)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...
SIDANTRIP_LLM_MODEL=gemini/gemini-2.5-flash    # Default for free tier; overridden per-request by API

# Serper (web search for Researcher)
SERPER_API_KEY=...

# Redis
REDIS_URL=redis://localhost:6379

# Activity DB
SIDANTRIP_DB_PATH=./data

# Server
PORT=8001
API_CALLBACK_URL=http://api:3000
```

## 13. Scaling Configuration

The architecture is designed so scaling is a **config change, not a rewrite**. Every bottleneck has a knob.

### 13.1 What Scales Without Changes

| Component | Why it's fine |
|---|---|
| Activity DB (YAML on disk) | Read-only, loaded into memory on boot. 100 cities × 100 activities = ~10MB. Fits in RAM on any instance. |
| Delta parsing / itinerary state | Pure CPU, sub-millisecond. No concern. |
| FastAPI (agent service) | Async, stateless per request. Horizontal scaling via replicas. |

### 13.2 LLM Concurrency & Backpressure

The biggest bottleneck. Each chat message holds an LLM connection for 3–10 seconds.

**Config: `PLANNER_MAX_CONCURRENT`** (default: 20)

```python
# server.py
import asyncio

_semaphore = asyncio.Semaphore(int(os.environ.get("PLANNER_MAX_CONCURRENT", 20)))

@app.post("/api/planner/chat")
async def planner_chat(request: PlannerChatRequest):
    if _semaphore.locked():
        return JSONResponse(status_code=429, content={
            "error": "busy",
            "retry_after_ms": 2000,
            "queue_position": _semaphore._waiters and len(_semaphore._waiters) or 0,
        })
    async with _semaphore:
        # ... run planner
```

At MVP: single instance, 20 concurrent. At scale: 5 replicas × 20 = 100 concurrent chats.

### 13.3 Multi-Provider Load Balancing

LiteLLM has built-in router support for failover and load distribution:

**Config: `SIDANTRIP_LLM_ROUTER`** (JSON)

```python
# When configured, distributes requests across providers
router_config = {
    "model_list": [
        {"model_name": "planner", "litellm_params": {"model": "anthropic/claude-sonnet-4-20250514"}, "tpm": 100000, "rpm": 500},
        {"model_name": "planner", "litellm_params": {"model": "gpt-4o"}, "tpm": 100000, "rpm": 500},
        {"model_name": "planner", "litellm_params": {"model": "gemini/gemini-2.5-flash"}, "tpm": 200000, "rpm": 1000},
    ],
    "routing_strategy": "usage-based",  # or "latency-based", "least-busy"
    "fallbacks": [{"anthropic/claude-sonnet-4-20250514": ["gpt-4o", "gemini/gemini-2.5-flash"]}],
}
```

MVP: single provider. At scale: flip on the router, add API keys, traffic distributes automatically.

### 13.4 Tiered Model Selection

**Config: `SIDANTRIP_MODEL_FREE` / `SIDANTRIP_MODEL_PRO`**

```env
# Free tier gets cheaper/faster model
SIDANTRIP_MODEL_FREE=gemini/gemini-2.5-flash      # ~$0.003/message
SIDANTRIP_MODEL_PRO=gemini/gemini-2.5-pro         # ~$0.013/message
```

The API passes the user's tier in the request. The agent service selects the model. Single config change, biggest cost lever — cuts free-tier costs by ~75%.

### 13.5 Response Caching

**Config: `SIDANTRIP_CACHE_ENABLED`** (default: false)

For near-duplicate requests (e.g., 100 users asking "plan day 1 in Seoul" with empty itineraries), cache the response. LiteLLM has built-in caching support:

```python
litellm.cache = litellm.Cache(
    type="redis",
    host=os.environ.get("REDIS_HOST", "localhost"),
    supported_call_types=["completion"],
)
```

Conservative approach: cache only when itinerary is empty + same destination + similar message (embedding similarity > 0.95). Invalidate on any itinerary state change.

### 13.6 Horizontal Scaling

The agent service is stateless — just add replicas:

```yaml
# docker-compose.yml or Railway/Fly config
services:
  agent:
    build: .
    replicas: 1          # MVP: 1 instance
    # replicas: 5        # Scale: 5 instances behind load balancer
    environment:
      PLANNER_MAX_CONCURRENT: 20
```

No code changes. The API discovers agent instances via service mesh / load balancer.

### 13.7 SSE Connection Limits

**Config: `SSE_TIMEOUT_SECONDS`** (default: 30)

```python
# Heartbeat keeps connection alive through proxies
# Hard timeout prevents zombie connections
@app.post("/api/planner/chat")
async def planner_chat(request: PlannerChatRequest):
    timeout = int(os.environ.get("SSE_TIMEOUT_SECONDS", 30))
    # ... SSE stream with heartbeat every 5s, hard cutoff at timeout
```

### 13.8 Scaling Summary

See `project-spec.md` §9.3 for the overall scaling roadmap by user tier. Agent-specific knobs are in §13.1–13.7 above.

## 14. MVP Scope

See `project-spec.md` §8 for the consolidated MVP roadmap. This service's MVP deliverables:
- Planner agent with full chat flow + streaming (LiteLLM)
- DB tools (index, clusters, detail, search, meta)
- FastAPI endpoint `/api/planner/chat` with SSE
- Health check endpoint
- CLI demo for local testing
- Docker container
- Bundled Seoul seed data (MVP covers Seoul only; Tokyo, Osaka, Okinawa, Hokkaido seeded post-MVP)

## 15. Testing Strategy

```bash
# Unit tests — tools, delta parsing, context loading
pytest tests/test_tools.py

# Integration test — planner with mocked LLM
pytest tests/test_planner.py

# Manual CLI test — interactive chat
python -m sidantrip.main --destination seoul

# Researcher test
python -m sidantrip.main --mode researcher --destination seoul --category food --num 3
```

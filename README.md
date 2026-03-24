# 是但Trip (SidanTrip)

AI-powered trip planner that actually plans your trip — not just lists options.

Built with LiteLLM + CrewAI + FastAPI. Cantonese-speaking AI agent that makes opinionated travel plans, streams responses via SSE, and embeds structured itinerary changes inline with conversational text.

> **是但** (sei6 daan2) — Cantonese for "whatever / easygoing." The agent plans everything so you don't have to stress.

## Why This Exists

Most AI travel tools give you 10 options and ask you to pick. 是但Trip makes the decision for you — like asking a friend who knows the city. You can always override, but the default is a complete plan, not a quiz.

This repo is also a **case study for the AI developer community**: when to use an agent orchestration framework (CrewAI) vs. direct LLM calls (LiteLLM), in the same project.

## Architecture

```
Flutter App (sidantrip-app)
    → sidantrip-api (Express TS, public-facing)
        → sidantrip (this repo — internal only)
            ├── PlannerAgent   (LiteLLM, sync streaming → SSE)
            ├── ResearcherCrew (CrewAI, async Redis queue)
            └── reads data/    (bundled activity DB)
```

**Two frameworks, one repo:**

| Component | Framework | Why |
|---|---|---|
| **Planner** (chat) | LiteLLM direct | Token-level streaming, stateful conversation, single agent — no orchestration needed |
| **Researcher** (pipeline) | CrewAI | Multi-agent handoff (Researcher → Reviewer), tool orchestration, CrewAI's sweet spot |

## How It Works

1. User sends a message ("plan day 1 in Seoul")
2. PlannerAgent builds a system prompt with the 是但 personality + activity DB context
3. Streams tokens back via SSE as they arrive from the LLM
4. Parses fenced ` ```json ` blocks from the response for structured itinerary deltas
5. Applies deltas (add/remove/move/clear_day) to the itinerary state

The agent is **stateless per request** — all conversation history and itinerary state is passed in by the API layer. Same pattern as ChatGPT/Claude.

### Three-Layer Activity Index

Minimizes token usage when loading destination data:

| Layer | Tokens/activity | Purpose |
|---|---|---|
| `_index.yaml` | ~50 | Compact manifest — always loaded |
| `_clusters.yaml` | ~30 | Geographic groupings for day-planning |
| Full YAML | ~200 | On-demand detail for specific activities |

## Quick Start

```bash
# Clone with activity data submodule
git clone --recurse-submodules https://github.com/HinsonSIDAN/sidantrip.git
cd sidantrip

# Install
pip install -e ".[dev]"

# Set up env (need at least one LLM key)
cp .env.example .env
# Edit .env with your API key

# Interactive CLI
python -m sidantrip.main --destination seoul

# Run tests
pytest

# Run server
uvicorn sidantrip.server:app --port 8001

# Docker
docker compose up
```

## Project Structure

```
sidantrip/
├── src/sidantrip/
│   ├── planner/              ← LiteLLM direct (streaming chat)
│   │   ├── agent.py          ← PlannerAgent: stream() + chat_sync()
│   │   ├── parser.py         ← Delta fence parsing state machine
│   │   └── prompts.py        ← 是但 system prompt (Cantonese)
│   │
│   ├── crews/                ← CrewAI (multi-agent pipelines)
│   │   └── researcher_crew.py
│   │
│   ├── tools/
│   │   └── db_tools.py       ← Activity DB access (index, clusters, detail, search)
│   │
│   ├── server.py             ← FastAPI: /api/planner/chat (SSE), /api/health
│   └── main.py               ← CLI entry point
│
├── data/                     ← git submodule → sidantrip-db
├── specs/                    ← Technical specs + architecture decisions
├── tests/                    ← 60 tests (parsing, application, tools, integration, server)
├── Dockerfile
└── docker-compose.yml
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/planner/chat` | SSE streaming planner chat |
| `GET` | `/api/health` | Health check |
| `POST` | `/api/admin/reload-index` | Hot-reload activity DB indices |

The `/api/planner/chat` endpoint accepts the full trip context per request and streams back SSE events:

- `token` — text chunk for real-time display
- `delta` — parsed itinerary change (add/remove/move/clear_day)
- `done` — final result with full text, all deltas, updated itinerary, token usage
- `error` — on failure

## Delta Format

The agent embeds itinerary changes as fenced JSON in its text response:

```json
{
  "deltas": [
    {"action": "add", "day": 1, "slot": {"activity_id": "gyeongbokgung-palace", "start_time": "10:00", "end_time": "12:00"}},
    {"action": "remove", "day": 2, "activity_id": "some-activity"},
    {"action": "move", "activity_id": "a1", "from_day": 1, "to_day": 2, "start_time": "14:00"},
    {"action": "clear_day", "day": 3}
  ]
}
```

No function_calling / tool_use — this approach works across all LLM providers (Claude, GPT, Gemini).

## Related Repos

| Repo | Description | License |
|---|---|---|
| [sidantrip-db](https://github.com/HinsonSIDAN/sidantrip-db) | Open-source YAML activity database | MIT |
| sidantrip-api | Express TS backend (auth, billing, sessions) | Private |
| sidantrip-app | Flutter mobile app | Private |

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `SIDANTRIP_LLM_MODEL` | `gemini/gemini-2.5-flash` | Default LLM (LiteLLM format) |
| `SIDANTRIP_DB_PATH` | `./data` | Path to activity database |
| `PLANNER_MAX_CONCURRENT` | `20` | Max concurrent LLM requests (429 beyond this) |
| `PORT` | `8001` | Server port |

## License

AGPL-3.0 — see [LICENSE](LICENSE).

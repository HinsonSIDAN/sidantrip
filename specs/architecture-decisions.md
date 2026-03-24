# SidanTrip — Architecture Decisions

Decisions made during pre-implementation spec review (2026-03-23). Each decision resolves a conflict or ambiguity across the spec documents.

---

## AD-01: Authentication Strategy

**Decision:** Supabase Auth + Supabase-managed PostgreSQL. Firebase removed entirely from the stack.

**MVP:** Supabase Anonymous Auth — zero user-facing friction (no sign-up screen). Provides a stable user ID for rate limiting and usage tracking from day one.

**v0.3:** Upgrade anonymous accounts to real accounts via email + Google + Apple sign-in using Supabase Auth's `linkIdentity`.

**Rationale:** Firebase Auth only makes sense paired with Firestore. Since the data model is relational (PostgreSQL), Firebase becomes an awkward second source of truth for identity. Supabase unifies auth + database under one managed service, with Prisma ORM on top. Self-hostable and open source (GoTrue), minimizing vendor lock-in.

**Impact:** Remove all Firebase references from `sidantrip-api.md`, `sidantrip-app.md`, and `feature-requirements.md`. Replace with Supabase Auth.

---

## AD-02: Itinerary Editing Model (Minimal Hybrid)

**Decision:** Minimal hybrid — users can subtract and rearrange directly, but only the AI can add.

**Direct manipulation (no AI round-trip):**
- **Remove** an activity — swipe left on timeline card
- **Reorder within the same day** — long-press and drag

**Chat-only (AI judgment required):**
- Adding activities
- Moving activities between days
- Replacing or swapping activities

**Removed:** The "Add to Day X" button in the Activity Detail sheet. If a user wants to add, they tell the AI.

**System messages:** When a user directly removes or reorders, a system message appears in chat (e.g., *"You removed Gwangjang Market from Day 1"*) so the AI has context on the next turn.

**Rationale:** Chat-only is a strong product decision, but forcing users to type "swap the first two activities" for a trivial reorder is frustrating. This hybrid lets users do the obvious stuff while keeping the AI as the sole authority for constructive changes.

---

## AD-03: Canonical API Endpoints

**Decision:** RESTful, trip-centric URL structure.

| Endpoint | Method | Description |
|---|---|---|
| `/api/trips/:id/chat` | POST | Send message (fire-and-forget, returns 202) |
| `/api/trips/:id/stream` | GET | Persistent SSE connection (all events) |
| `/api/trips` | GET/POST | List/create trips |
| `/api/trips/:id` | GET/PATCH/DELETE | Trip CRUD |
| `/api/destinations/:city/activities` | GET | Activity index for a destination |
| `/api/destinations/:city/activities/:activityId` | GET | Full activity detail |
| `/api/destinations` | GET | List available destinations |

**Rationale:** The trip is the primary resource. Putting `tripId` in the URL (not the body) is RESTful, easier to log, and aligns with the group sharing endpoints.

---

## AD-04: SSE Event Names

**Decision:** Standardize on `token`, `delta`, `done`.

The `state_delta` name used in one example in `product-spec.md` is retired. All specs use `delta`.

**Full SSE event list (including collaboration events for v0.3):**

| Event | Milestone | Description |
|---|---|---|
| `token` | MVP | AI response text chunk |
| `delta` | MVP | Itinerary change (structured JSON) |
| `done` | MVP | AI response complete, includes usage stats |
| `user_message` | v0.3 | A collaborator sent a message |
| `typing` | v1.0 | A collaborator is composing |
| `member_joined` | v0.3 | New collaborator joined trip |
| `member_left` | v0.3 | Collaborator left or removed |
| `queue_position` | v0.3 | Message queued behind in-flight AI request |

---

## AD-05: Canonical Data Model

**Decision:** The Prisma schema in `sidantrip-api.md` is the canonical data model. The raw SQL in `product-spec.md` is removed.

**Rationale:** The project uses Prisma — the Prisma schema is what gets implemented. The normalized `Conversation` + `Message` model is better than `chat_history JSONB` for per-message delta tracking, token usage, and model attribution. Tables not needed until later milestones (`subscriptions`, `payments`, `contributions`) will be added to the Prisma schema when those milestones arrive.

`product-spec.md` should describe *what* the data model represents conceptually, not duplicate the schema definition.

---

## AD-06: Activity Data Ownership

**Decision:** Both services reference `sidantrip-db` independently, serving different purposes.

| Service | Data format | Purpose |
|---|---|---|
| Agent (`sidantrip`) | `_index.yaml`, `_clusters.yaml`, full YAML from disk | AI context for planning (per-destination, loaded into memory) |
| API (`sidantrip-api`) | PostgreSQL `activities` table | Search, filtering, detail views for Flutter app (scales to millions) |

**Source of truth:** YAML files in GitHub (`sidantrip-db`).

**Sync mechanism:** Event-driven. A GitHub Action on PR merge to `sidantrip-db`:
1. Runs `compile_index.py` (generates AI indices — existing behavior)
2. Calls `POST /api/admin/sync-activities` on `sidantrip-api` (upserts YAML into PostgreSQL)
3. Calls `POST /api/admin/reload-index` on `sidantrip` agent service (hot-reloads in-memory indices)

**Activity IDs are immutable** — once assigned, never changed. This eliminates consistency concerns during hot-reload or sync timing differences.

**MVP:** Seed database at deploy time. Live webhook sync added when contribution volume grows.

---

## AD-07: Delta Parsing Location

**Decision:** Delta parsing lives in the agent service (`sidantrip`), inside the planner module.

The LLM produces a single text stream mixing natural language with fenced ` ```json ` blocks. The planner module:
1. Buffers incoming tokens
2. Detects ` ```json ` fence open/close
3. Extracts the JSON as a `delta` SSE event
4. Passes surrounding text as `token` SSE events

`sidantrip-api` receives pre-separated `token` and `delta` events and proxies them to the client without any parsing.

---

## AD-08: Unified SSE Model

**Decision:** One SSE pattern for both solo and group trips.

- `GET /api/trips/:id/stream` — persistent SSE connection. All events flow here (tokens, deltas, done, user messages). Client opens this when entering the trip screen.
- `POST /api/trips/:id/chat` — fire-and-forget. Sends the message, returns `202 Accepted` with no response body.

**Rationale:** Unifying solo and group under one pattern means no refactoring when collaboration is added in v0.3. The persistent SSE connection is the same whether one or eight people are connected.

---

## AD-09: API Required from MVP

**Decision:** `sidantrip-api` exists from MVP.

Even without real accounts, the API handles:
- Supabase Anonymous Auth (device-level identity)
- Rate limiting (protects LLM costs)
- SSE connection management
- Activity serving from PostgreSQL
- Usage event logging
- Conversation/message persistence

The Flutter app never talks to the agent service directly.

---

## AD-10: Researcher Default Model

**Decision:** Researcher crew defaults to Gemini Pro (`gemini/gemini-2.5-pro`).

DB quality is more important than cost savings for research. The Researcher always uses Gemini Pro regardless of user tier. This is a v0.2 concern (Researcher is post-MVP).

---

## AD-11: Delta Error Handling

**Decision:** Failed deltas are handled conversationally, not via system messages or retries.

**Flow:**
1. AI responds with text + broken/invalid delta
2. Agent service detects validation failure (malformed JSON, bad activity_id, etc.)
3. Text response streams to user as normal
4. Validation error stored as context for next turn
5. On next turn, error context injected into system prompt — AI naturally asks user to clarify
6. If user ignores the clarification 2-3 times, discard the failed delta silently

**Fuzzy matching:** Before flagging a delta as failed, attempt fuzzy matching on `activity_id` (e.g., `gyeongbokgung-palace` → `seoul-sightseeing-gyeongbokgung-palace`).

**Rationale:** The text response is always valuable — the user shouldn't lose the AI's reply because a delta is broken. Conversational clarification feels natural, not like an error state.

---

## AD-12: Conversation History Strategy

**Decision:** Three-tier history management.

| Range | Treatment | Milestone |
|---|---|---|
| Last 20 messages | Sent verbatim to agent | MVP |
| Messages 21-100 | Compacted summary (~1,500 tokens) | v0.2 |
| Messages 100+ | Discarded (covered by summary + memory) | v0.2 |

**Context payload sent to agent per request:**
```
[system prompt + activity index + trip context]
[user memory / traveler profile]           ← v0.3
[compact summary of messages 21-100]       ← v0.2
[last 20 messages verbatim]
[current user message]
```

**Summarization triggers:**
- First triggered when conversation exceeds 40 messages (20 verbatim + 20 to summarize)
- Regenerated every 10 new messages past the threshold
- Background LLM call owned by `sidantrip-api`

**Full message history stored in PostgreSQL from MVP** regardless of what's sent to the agent. Write everything, read selectively.

---

## AD-13: Activity Index Freshness

**Decision:** Hot-reload from MVP. Activity IDs are immutable.

When `sidantrip-db` is updated (PR merge), the GitHub Action calls `POST /api/admin/reload-index` on the agent service. The agent atomically swaps the in-memory index — in-flight requests keep their reference to the old data, new requests get the updated data.

No restart required. No consistency issues since activity IDs never change.

---

## AD-14: Itinerary State Authority

**Decision:** `sidantrip-api` is authoritative for itinerary state.

**Flow:**
1. Agent returns deltas via SSE
2. API applies deltas to the trip's `itineraryState` in PostgreSQL
3. API streams the same deltas to the client via SSE
4. Client applies deltas locally for UI animation

Both API and client end up in sync, but the API is the source of truth. If the client crashes mid-stream, the state is safe server-side.

---

## AD-15: User Memory Strategy

**Decision:** 5K token budget. Global profile + destination memory + learned facts with confidence-based decay.

### Memory structure

| Component | Budget | Cap |
|---|---|---|
| Global profile | ~800 tokens | Fixed fields, overwrite on update |
| Destination memory | ~3,000 tokens | 10 destinations × ~300 tokens each |
| Learned facts | ~750 tokens | 30 facts × ~25 tokens each |
| Headroom | ~450 tokens | — |

### Signal types
- **Explicit:** "I hate waking up early" → high confidence
- **Behavioral:** user keeps removing morning activities → lower confidence
- **Factual:** "I'm allergic to shellfish" → highest confidence

### Decay rules (triggered after each conversation's memory extraction job)

**Learned facts:**

| Condition | Action |
|---|---|
| Confidence < 0.3 | Remove immediately |
| Confidence 0.3-0.5 + older than 90 days | Remove |
| Confidence 0.5-0.7 + older than 180 days | Reduce confidence by 0.1 |
| Count exceeds 30 | Drop lowest confidence fact |
| User-confirmed (confidence 1.0) | Never decays |

**Destination memory:**

| Condition | Action |
|---|---|
| More than 10 destinations | Drop least recently visited |
| Not visited in 12 months | Compress to highlights (~100 tokens) |
| Not visited in 24 months | Remove entirely |

**Global profile:** No decay. Fields overwritten by higher-confidence signals.

### Lifecycle

| Event | Action |
|---|---|
| First trip | Empty memory. Planner asks discovery questions. |
| After each conversation | Background extraction job. Adds signals, runs decay. |
| Returning user | Memory loaded into system prompt as "Traveler Profile" section. |
| User corrects a preference | Manual override, confidence 1.0. |
| User requests reset | Clear all memory. |

### Group trips
When User A says "I'm allergic to shellfish" in a shared trip, it is extracted into User A's personal memory.

### Pro-only
Memory only stored and used for Pro users. Free users have no persistent memory.

---

*Created: 2026-03-23. Derived from spec review Q&A session.*

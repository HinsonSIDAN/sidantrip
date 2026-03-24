"""
FastAPI server for the SidanTrip agent service.

Endpoints:
    POST /api/planner/chat  — SSE streaming planner chat
    GET  /api/health        — health check
    POST /api/admin/reload-index — hot-reload activity DB indices
"""

import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .planner.agent import PlannerAgent

app = FastAPI(title="SidanTrip Agent Service", version="0.1.0")

# Concurrency limiter for LLM calls
_max_concurrent = int(os.environ.get("PLANNER_MAX_CONCURRENT", "20"))
_semaphore = asyncio.Semaphore(_max_concurrent)

# Cache PlannerAgent instances per destination
_agents: dict[str, PlannerAgent] = {}


def _get_agent(destination: str) -> PlannerAgent:
    if destination not in _agents:
        _agents[destination] = PlannerAgent(destination)
    return _agents[destination]


class PlannerChatRequest(BaseModel):
    destination: str
    start_date: str
    end_date: str
    accommodation: str | None = None
    message: str
    itinerary_state: dict
    conversation_history: list[dict] = []
    user_memory: dict | None = None
    llm_model: str | None = None


@app.post("/api/planner/chat")
async def planner_chat(request: PlannerChatRequest):
    """Stream planner response via SSE. Returns 429 if at capacity."""
    if _semaphore.locked():
        waiters = getattr(_semaphore, "_waiters", None)
        queue_pos = len(waiters) if waiters else 0
        return JSONResponse(
            status_code=429,
            content={
                "error": "busy",
                "retry_after_ms": 2000,
                "queue_position": queue_pos,
            },
        )

    agent = _get_agent(request.destination)

    async def event_generator():
        async with _semaphore:
            async for event in agent.stream(
                message=request.message,
                conversation_history=request.conversation_history,
                itinerary_state=request.itinerary_state,
                start_date=request.start_date,
                end_date=request.end_date,
                accommodation=request.accommodation,
                user_memory=request.user_memory,
                llm_model=request.llm_model,
            ):
                event_type = event.pop("type", "token")
                yield {
                    "event": event_type,
                    "data": json.dumps(event, ensure_ascii=False),
                }

    return EventSourceResponse(event_generator())


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "agents": ["planner", "researcher"],
        "max_concurrent": _max_concurrent,
    }


@app.post("/api/admin/reload-index")
async def reload_index(request: Request):
    """Hot-reload activity DB indices for all cached destinations."""
    for agent in _agents.values():
        agent.reload_context()
    return {"status": "ok", "reloaded": list(_agents.keys())}

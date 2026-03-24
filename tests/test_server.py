"""Tests for FastAPI server endpoints."""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient

from sidantrip.server import app, _agents, _semaphore


@pytest.fixture(autouse=True)
def reset_state():
    """Reset server state between tests."""
    _agents.clear()
    # Reset semaphore to default
    yield


@pytest.fixture
def client():
    return TestClient(app)


def _chat_payload(**overrides):
    base = {
        "destination": "seoul",
        "start_date": "2026-05-21",
        "end_date": "2026-05-25",
        "message": "plan day 1",
        "itinerary_state": {"days": {}},
        "conversation_history": [],
    }
    base.update(overrides)
    return base


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "planner" in data["agents"]
        assert "researcher" in data["agents"]

    def test_health_includes_max_concurrent(self, client):
        resp = client.get("/api/health")
        assert "max_concurrent" in resp.json()


class TestReloadIndex:
    def test_reload_empty(self, client):
        resp = client.post("/api/admin/reload-index")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["reloaded"] == []

    def test_reload_with_cached_agents(self, client):
        # Pre-populate an agent
        from sidantrip.planner.agent import PlannerAgent
        mock_agent = MagicMock(spec=PlannerAgent)
        _agents["seoul"] = mock_agent

        resp = client.post("/api/admin/reload-index")
        assert resp.status_code == 200
        assert "seoul" in resp.json()["reloaded"]
        mock_agent.reload_context.assert_called_once()


class TestPlannerChat:
    def test_chat_streams_sse(self, client):
        """Test that /api/planner/chat returns SSE events."""

        async def mock_stream(**kwargs):
            yield {"type": "token", "content": "Hello "}
            yield {"type": "token", "content": "world!"}
            yield {"type": "done", "text": "Hello world!", "deltas": [], "itinerary": {"days": {}}, "usage": {}}

        mock_agent = MagicMock()
        mock_agent.stream = mock_stream

        with patch("sidantrip.server._get_agent", return_value=mock_agent):
            with client.stream("POST", "/api/planner/chat", json=_chat_payload()) as resp:
                assert resp.status_code == 200
                lines = list(resp.iter_lines())

        # Should contain SSE event lines
        event_lines = [l for l in lines if l.startswith("event:") or l.startswith("data:")]
        assert len(event_lines) > 0

    def test_chat_with_model_override(self, client):
        """Test that llm_model is passed through."""
        received_kwargs = {}

        async def mock_stream(**kwargs):
            received_kwargs.update(kwargs)
            yield {"type": "done", "text": "ok", "deltas": [], "itinerary": {"days": {}}, "usage": {}}

        mock_agent = MagicMock()
        mock_agent.stream = mock_stream

        with patch("sidantrip.server._get_agent", return_value=mock_agent):
            with client.stream(
                "POST",
                "/api/planner/chat",
                json=_chat_payload(llm_model="gpt-4o"),
            ) as resp:
                list(resp.iter_lines())  # consume stream

        assert received_kwargs.get("llm_model") == "gpt-4o"

    def test_chat_invalid_payload(self, client):
        resp = client.post("/api/planner/chat", json={"message": "hi"})
        assert resp.status_code == 422  # validation error


class TestSemaphore429:
    def test_returns_429_when_full(self, client):
        """When semaphore is fully acquired, should return 429."""

        # Acquire all semaphore slots
        async def drain_semaphore():
            for _ in range(_semaphore._value):
                await _semaphore.acquire()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(drain_semaphore())

            resp = client.post("/api/planner/chat", json=_chat_payload())
            assert resp.status_code == 429
            data = resp.json()
            assert data["error"] == "busy"
            assert "retry_after_ms" in data
        finally:
            # Release all slots
            for _ in range(_semaphore._value if hasattr(_semaphore, '_value') else 20):
                try:
                    _semaphore.release()
                except ValueError:
                    break
            loop.close()

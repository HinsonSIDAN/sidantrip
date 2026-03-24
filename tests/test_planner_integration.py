"""Integration tests for PlannerAgent with mocked LiteLLM responses."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from sidantrip.planner.agent import PlannerAgent
from sidantrip.planner.parser import apply_deltas


@pytest.fixture
def mock_db(monkeypatch):
    """Mock DB tools to return canned Seoul data."""
    import sidantrip.planner.agent as agent_mod

    monkeypatch.setattr(agent_mod, "load_city_meta", lambda d: "city: Seoul\ntimezone: Asia/Seoul")
    monkeypatch.setattr(agent_mod, "load_city_index", lambda d: "# Seoul Index (2 activities)\n- [a1] Palace | Jongno")
    monkeypatch.setattr(agent_mod, "load_clusters", lambda d: "# Seoul Neighborhoods\n## Jongno\n- [a1] Palace")


def _make_sync_response(content: str):
    """Create a mock litellm sync response."""
    choice = MagicMock()
    choice.message.content = content

    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    usage.total_tokens = 150

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


class TestPlannerSync:
    def test_text_only_response(self, mock_db):
        agent = PlannerAgent(destination="seoul")
        mock_resp = _make_sync_response("冇問題！你想去邊？")

        with patch("litellm.completion", return_value=mock_resp):
            result = agent.chat_sync(
                message="hello",
                conversation_history=[],
                itinerary_state={"days": {}},
                start_date="2026-05-21",
                end_date="2026-05-25",
            )

        assert result["text"] == "冇問題！你想去邊？"
        assert result["deltas"] == []
        assert result["usage"]["total"] == 150

    def test_response_with_deltas(self, mock_db):
        content = """好啦Day 1咁行：

```json
{"deltas": [{"action": "add", "day": 1, "slot": {"activity_id": "a1", "start_time": "10:00", "end_time": "12:00", "notes": "go early"}}]}
```

記住帶護照！"""
        agent = PlannerAgent(destination="seoul")
        mock_resp = _make_sync_response(content)

        with patch("litellm.completion", return_value=mock_resp):
            result = agent.chat_sync(
                message="plan day 1",
                conversation_history=[],
                itinerary_state={"days": {}},
                start_date="2026-05-21",
                end_date="2026-05-25",
            )

        assert len(result["deltas"]) == 1
        assert result["deltas"][0]["action"] == "add"
        assert "1" in result["itinerary"]["days"]
        assert result["itinerary"]["days"]["1"]["slots"][0]["activity_id"] == "a1"
        assert "記住帶護照" in result["text"]
        assert "```json" not in result["text"]

    def test_malformed_json_stores_error(self, mock_db):
        content = """Here's the plan:

```json
{invalid json here}
```"""
        agent = PlannerAgent(destination="seoul")
        mock_resp = _make_sync_response(content)

        with patch("litellm.completion", return_value=mock_resp):
            result = agent.chat_sync(
                message="plan",
                conversation_history=[],
                itinerary_state={"days": {}},
                start_date="2026-05-21",
                end_date="2026-05-25",
            )

        assert result["deltas"] == []
        assert result.get("parse_errors") is not None

    def test_context_loading_cached(self, mock_db):
        agent = PlannerAgent(destination="seoul")
        ctx1 = agent.load_context()
        ctx2 = agent.load_context()
        assert ctx1 is ctx2  # same object, cached

    def test_reload_context(self, mock_db):
        agent = PlannerAgent(destination="seoul")
        ctx1 = agent.load_context()
        agent.reload_context()
        ctx2 = agent.load_context()
        assert ctx1 is not ctx2  # different object after reload


class TestPlannerAsync:
    @pytest.mark.asyncio
    async def test_stream_yields_events(self, mock_db):
        """Test that stream() yields token and done events."""

        async def mock_acompletion(**kwargs):
            chunks = [
                _make_chunk("OK "),
                _make_chunk("plan!"),
                _make_chunk(None, finish=True, usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}),
            ]
            for c in chunks:
                yield c

        agent = PlannerAgent(destination="seoul")

        with patch("litellm.acompletion", return_value=mock_acompletion()):
            events = []
            async for event in agent.stream(
                message="hi",
                conversation_history=[],
                itinerary_state={"days": {}},
                start_date="2026-05-21",
                end_date="2026-05-25",
            ):
                events.append(event)

        event_types = [e["type"] for e in events]
        assert "done" in event_types


def _make_chunk(content, finish=False, usage=None):
    """Create a mock streaming chunk."""
    chunk = MagicMock()

    if content is not None:
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    elif finish:
        delta = MagicMock()
        delta.content = None
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    else:
        chunk.choices = []

    if usage:
        u = MagicMock()
        u.prompt_tokens = usage["prompt_tokens"]
        u.completion_tokens = usage["completion_tokens"]
        u.total_tokens = usage["total_tokens"]
        chunk.usage = u
    else:
        chunk.usage = None

    return chunk

"""
PlannerAgent — stateless per-request LiteLLM streaming planner.

Pre-loads activity context (index + clusters + meta) once per session.
Each chat() call builds the full message list and streams via litellm.completion().
Delta parsing happens on the accumulated response after streaming completes.
"""

import json
import os
from collections.abc import AsyncGenerator

import litellm

from .parser import StreamDeltaParser, apply_deltas, format_itinerary, parse_deltas
from .prompts import build_system_prompt
from ..tools.db_tools import load_city_index, load_city_meta, load_clusters


class PlannerAgent:
    """
    Stateless-per-request planner. Receives all context from the caller
    (conversation history, itinerary state, user memory) — owns nothing.

    Usage:
        agent = PlannerAgent(destination="seoul")
        async for event in agent.stream(request):
            # event: {"type": "token"|"delta"|"done"|"error", ...}
    """

    def __init__(self, destination: str):
        self.destination = destination
        self._activity_context: str | None = None

    def load_context(self) -> str:
        """Pre-load activity DB context for this destination. Cached per instance."""
        if self._activity_context is None:
            parts = []
            parts.append(load_city_meta(self.destination))
            parts.append(load_city_index(self.destination))
            parts.append(load_clusters(self.destination))
            self._activity_context = "\n\n".join(parts)
        return self._activity_context

    def reload_context(self):
        """Force reload of activity context (e.g., after admin reload-index)."""
        self._activity_context = None

    def _build_messages(
        self,
        message: str,
        conversation_history: list[dict],
        itinerary_state: dict,
        start_date: str,
        end_date: str,
        accommodation: str | None = None,
        user_memory: dict | None = None,
    ) -> list[dict]:
        """Build the full message list for a planner request."""
        context = self.load_context()

        # Inject user memory into context if present
        if user_memory:
            memory_text = _format_user_memory(user_memory)
            context = f"{context}\n\n## Traveler Profile\n\n{memory_text}"

        itinerary_text = format_itinerary(itinerary_state)

        system_prompt = build_system_prompt(
            destination=self.destination,
            start_date=start_date,
            end_date=end_date,
            accommodation=accommodation or "未指定",
            destination_context=context,
            itinerary_state=itinerary_text,
        )

        messages = [{"role": "system", "content": system_prompt}]

        # Append conversation history (already truncated/summarized by API layer)
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Append current user message
        messages.append({"role": "user", "content": message})

        return messages

    async def stream(
        self,
        message: str,
        conversation_history: list[dict],
        itinerary_state: dict,
        start_date: str,
        end_date: str,
        accommodation: str | None = None,
        user_memory: dict | None = None,
        llm_model: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream a planner response. Yields events:
            {"type": "token", "content": str}
            {"type": "delta", "data": {"deltas": [...]}}
            {"type": "done", "text": str, "deltas": [...], "itinerary": dict, "usage": dict}
            {"type": "error", "message": str}
        """
        model = llm_model or os.environ.get(
            "SIDANTRIP_LLM_MODEL", "gemini/gemini-2.5-flash"
        )

        messages = self._build_messages(
            message=message,
            conversation_history=conversation_history,
            itinerary_state=itinerary_state,
            start_date=start_date,
            end_date=end_date,
            accommodation=accommodation,
            user_memory=user_memory,
        )

        parser = StreamDeltaParser()
        usage = {}

        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                stream=True,
                temperature=0.7,
            )

            async for chunk in response:
                choice = chunk.choices[0] if chunk.choices else None
                if not choice:
                    continue

                # Collect usage from the final chunk
                if chunk.usage:
                    usage = {
                        "input": chunk.usage.prompt_tokens or 0,
                        "output": chunk.usage.completion_tokens or 0,
                        "total": chunk.usage.total_tokens or 0,
                    }

                delta = choice.delta
                if delta and delta.content:
                    events = parser.feed(delta.content)
                    for event in events:
                        yield event

            # Finalize stream
            final_events = parser.finish()
            for event in final_events:
                yield event

        except Exception as e:
            yield {"type": "error", "message": str(e)}
            return

        # Apply deltas to itinerary
        updated_itinerary = apply_deltas(
            itinerary_state, parser.deltas
        )

        # Build done event
        done_event = {
            "type": "done",
            "text": parser.text,
            "deltas": parser.deltas,
            "itinerary": updated_itinerary,
            "usage": usage,
        }
        if parser.error:
            done_event["parse_errors"] = parser.error

        yield done_event

    def chat_sync(
        self,
        message: str,
        conversation_history: list[dict],
        itinerary_state: dict,
        start_date: str,
        end_date: str,
        accommodation: str | None = None,
        user_memory: dict | None = None,
        llm_model: str | None = None,
    ) -> dict:
        """
        Non-streaming chat for CLI usage. Returns full result dict.
        """
        model = llm_model or os.environ.get(
            "SIDANTRIP_LLM_MODEL", "gemini/gemini-2.5-flash"
        )

        messages = self._build_messages(
            message=message,
            conversation_history=conversation_history,
            itinerary_state=itinerary_state,
            start_date=start_date,
            end_date=end_date,
            accommodation=accommodation,
            user_memory=user_memory,
        )

        response = litellm.completion(
            model=model,
            messages=messages,
            stream=False,
            temperature=0.7,
        )

        raw = response.choices[0].message.content or ""

        usage = {}
        if response.usage:
            usage = {
                "input": response.usage.prompt_tokens or 0,
                "output": response.usage.completion_tokens or 0,
                "total": response.usage.total_tokens or 0,
            }

        text, deltas, error = parse_deltas(raw)
        updated_itinerary = apply_deltas(itinerary_state, deltas)

        result = {
            "text": text,
            "deltas": deltas,
            "itinerary": updated_itinerary,
            "usage": usage,
        }
        if error:
            result["parse_errors"] = error

        return result


def _format_user_memory(memory: dict) -> str:
    """Format user memory dict into natural language for system prompt."""
    parts = []
    if memory.get("profile"):
        parts.append(memory["profile"])
    if memory.get("destination_preferences"):
        for dest, prefs in memory["destination_preferences"].items():
            parts.append(f"{dest}: {prefs}")
    if memory.get("learned_facts"):
        for fact in memory["learned_facts"]:
            parts.append(f"- {fact}")
    return "\n".join(parts) if parts else ""

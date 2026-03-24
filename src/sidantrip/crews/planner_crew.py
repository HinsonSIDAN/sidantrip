"""
Planner Crew — the main conversational agent that helps users build itineraries.
Runs synchronously per user message, returns text + itinerary deltas.
"""

import os
import json
import re
import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai.project import CrewBase, agent, task, crew

from ..tools.db_tools import (
    load_city_index,
    load_clusters,
    load_activity_detail,
    load_city_meta,
    search_activities,
)


@CrewBase
class PlannerCrew:
    """Single-agent crew for interactive trip planning."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, trip_config: dict):
        """
        trip_config: {
            destination: str,
            start_date: str,
            end_date: str,
            accommodation: str,
            llm_model: str (optional),
        }
        """
        self.trip = trip_config
        self.itinerary_state = {"days": {}}
        self.conversation_history = []
        self.total_tokens = {"input": 0, "output": 0}

    def _get_llm(self) -> LLM:
        model = self.trip.get("llm_model", os.environ.get(
            "SIDANTRIP_LLM_MODEL", "anthropic/claude-sonnet-4-20250514"
        ))
        return LLM(model=model, temperature=0.7)

    def _build_activity_context(self) -> str:
        """Pre-load the three-layer index for the destination."""
        dest = self.trip["destination"]
        context_parts = []
        context_parts.append(load_city_meta.run(destination=dest))
        context_parts.append(load_city_index.run(destination=dest))
        context_parts.append(load_clusters.run(destination=dest))
        return "\n\n".join(context_parts)

    @agent
    def planner(self) -> Agent:
        return Agent(
            config=self.agents_config["planner"],
            llm=self._get_llm(),
            tools=[load_activity_detail, search_activities],
            verbose=False,
            max_iter=5,
            allow_delegation=False,
        )

    @task
    def plan_itinerary(self) -> Task:
        return Task(
            config=self.tasks_config["plan_itinerary"],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def chat(self, user_message: str) -> dict:
        """
        Process a user message and return response + itinerary changes.

        Returns: {
            "text": str,          # Conversational response
            "deltas": list,       # Itinerary changes
            "itinerary": dict,    # Updated full itinerary state
            "usage": dict,        # Token usage for this turn
        }
        """
        self.conversation_history.append({"role": "user", "content": user_message})

        # Build dynamic inputs for the crew
        activity_context = self._build_activity_context()
        itinerary_json = json.dumps(self.itinerary_state, indent=2)

        result = self.crew().kickoff(inputs={
            "destination": self.trip["destination"],
            "start_date": self.trip["start_date"],
            "end_date": self.trip["end_date"],
            "accommodation": self.trip.get("accommodation", "Not specified"),
            "user_message": user_message,
            "itinerary_state": itinerary_json if self.itinerary_state["days"] else "Empty — no activities planned yet.",
            "activity_context": activity_context,
        })

        # Parse response: extract text and JSON deltas
        raw_output = result.raw
        text, deltas = self._parse_response(raw_output)

        # Apply deltas to itinerary state
        if deltas:
            self._apply_deltas(deltas)

        self.conversation_history.append({"role": "assistant", "content": text})

        # Track tokens
        usage = {}
        if hasattr(result, "token_usage") and result.token_usage:
            usage = {
                "input": getattr(result.token_usage, "prompt_tokens", 0),
                "output": getattr(result.token_usage, "completion_tokens", 0),
                "total": getattr(result.token_usage, "total_tokens", 0),
            }
            self.total_tokens["input"] += usage.get("input", 0)
            self.total_tokens["output"] += usage.get("output", 0)

        return {
            "text": text,
            "deltas": deltas,
            "itinerary": self.itinerary_state,
            "usage": usage,
        }

    def _parse_response(self, raw: str) -> tuple[str, list]:
        """Extract conversational text and JSON delta block from LLM response."""
        json_pattern = r"```json\s*(\{.*?\})\s*```"
        match = re.search(json_pattern, raw, re.DOTALL)

        deltas = []
        if match:
            try:
                parsed = json.loads(match.group(1))
                deltas = parsed.get("deltas", [])
            except json.JSONDecodeError:
                pass
            # Remove JSON block from text
            text = re.sub(json_pattern, "", raw, flags=re.DOTALL).strip()
        else:
            text = raw.strip()

        return text, deltas

    def _apply_deltas(self, deltas: list):
        """Apply itinerary change deltas to the state."""
        for delta in deltas:
            action = delta.get("action")
            day_key = str(delta.get("day", ""))

            if action == "add":
                if day_key not in self.itinerary_state["days"]:
                    self.itinerary_state["days"][day_key] = {"slots": []}
                self.itinerary_state["days"][day_key]["slots"].append(delta["slot"])
                # Sort by start_time
                self.itinerary_state["days"][day_key]["slots"].sort(
                    key=lambda s: s.get("start_time", "99:99")
                )

            elif action == "remove":
                if day_key in self.itinerary_state["days"]:
                    self.itinerary_state["days"][day_key]["slots"] = [
                        s for s in self.itinerary_state["days"][day_key]["slots"]
                        if s.get("activity_id") != delta.get("activity_id")
                    ]

            elif action == "move":
                from_day = str(delta.get("from_day", ""))
                to_day = str(delta.get("to_day", ""))
                activity_id = delta.get("activity_id")
                # Find and remove from source
                slot = None
                if from_day in self.itinerary_state["days"]:
                    for s in self.itinerary_state["days"][from_day]["slots"]:
                        if s.get("activity_id") == activity_id:
                            slot = s
                            break
                    if slot:
                        self.itinerary_state["days"][from_day]["slots"].remove(slot)
                        slot["start_time"] = delta.get("start_time", slot.get("start_time"))
                        if to_day not in self.itinerary_state["days"]:
                            self.itinerary_state["days"][to_day] = {"slots": []}
                        self.itinerary_state["days"][to_day]["slots"].append(slot)
                        self.itinerary_state["days"][to_day]["slots"].sort(
                            key=lambda s: s.get("start_time", "99:99")
                        )

            elif action == "clear_day":
                if day_key in self.itinerary_state["days"]:
                    self.itinerary_state["days"][day_key]["slots"] = []

    def get_itinerary_summary(self) -> str:
        """Pretty-print the current itinerary."""
        if not self.itinerary_state["days"]:
            return "No activities planned yet."

        lines = []
        for day_num in sorted(self.itinerary_state["days"].keys(), key=int):
            day = self.itinerary_state["days"][day_num]
            lines.append(f"\n📅 Day {day_num}")
            for slot in day.get("slots", []):
                time = slot.get("start_time", "??:??")
                end = slot.get("end_time", "")
                name = slot.get("activity_id", "?")
                notes = slot.get("notes", "")
                time_str = f"{time}-{end}" if end else time
                lines.append(f"  {time_str}  {name}" + (f" — {notes}" if notes else ""))
        return "\n".join(lines) if lines else "No activities planned yet."

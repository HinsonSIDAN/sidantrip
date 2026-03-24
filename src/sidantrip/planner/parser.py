"""
Delta fence parsing and itinerary state application.

The parser extracts fenced ```json blocks from LLM responses containing
itinerary deltas. Uses re.findall for multi-block matching.

On parse failure, no retry LLM call — error context is stored for the
next conversational turn (AD-11 conversational fallback).
"""

import json
import re


# Pattern matches all fenced JSON blocks in a response
_JSON_FENCE_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

VALID_ACTIONS = {"add", "remove", "move", "clear_day"}


def parse_deltas(raw: str) -> tuple[str, list[dict], str | None]:
    """
    Extract conversational text and JSON delta blocks from an LLM response.

    Returns:
        (text, deltas, error) — error is a string describing parse failure,
        or None on success. Error context can be injected into the next turn.
    """
    matches = _JSON_FENCE_PATTERN.findall(raw)

    # Strip all JSON fence blocks from text
    text = _JSON_FENCE_PATTERN.sub("", raw).strip()

    if not matches:
        return text, [], None

    all_deltas = []
    errors = []

    for i, json_str in enumerate(matches):
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            errors.append(f"Block {i + 1}: invalid JSON — {e}")
            continue

        if not isinstance(parsed, dict):
            errors.append(f"Block {i + 1}: expected object, got {type(parsed).__name__}")
            continue

        deltas = parsed.get("deltas")
        if not isinstance(deltas, list):
            errors.append(f"Block {i + 1}: missing or invalid 'deltas' array")
            continue

        for j, delta in enumerate(deltas):
            validated = _validate_delta(delta)
            if validated is not None:
                errors.append(f"Block {i + 1}, delta {j + 1}: {validated}")
                continue
            all_deltas.append(delta)

    error_msg = "; ".join(errors) if errors else None
    return text, all_deltas, error_msg


def _validate_delta(delta: dict) -> str | None:
    """Validate a single delta object. Returns error string or None if valid."""
    action = delta.get("action")
    if action not in VALID_ACTIONS:
        return f"invalid action '{action}'"

    if action == "add":
        if "day" not in delta:
            return "add: missing 'day'"
        slot = delta.get("slot")
        if not isinstance(slot, dict):
            return "add: missing or invalid 'slot'"
        if "activity_id" not in slot:
            return "add: slot missing 'activity_id'"
        if "start_time" not in slot or "end_time" not in slot:
            return "add: slot missing 'start_time' or 'end_time'"

    elif action == "remove":
        if "day" not in delta:
            return "remove: missing 'day'"
        if "activity_id" not in delta:
            return "remove: missing 'activity_id'"

    elif action == "move":
        for field in ("activity_id", "from_day", "to_day", "start_time"):
            if field not in delta:
                return f"move: missing '{field}'"

    elif action == "clear_day":
        if "day" not in delta:
            return "clear_day: missing 'day'"

    return None


class StreamDeltaParser:
    """
    State machine for parsing deltas from a streaming LLM response.

    States:
        STREAMING_TEXT — buffering text tokens, emitting them
        BUFFERING_JSON — accumulating JSON inside a ```json fence
    """

    def __init__(self):
        self._buffer = ""
        self._json_buffer = ""
        self._state = "STREAMING_TEXT"
        self._text_parts: list[str] = []
        self._deltas: list[dict] = []
        self._errors: list[str] = []

    def feed(self, token: str) -> list[dict]:
        """
        Feed a token from the LLM stream.

        Returns a list of events:
            {"type": "token", "content": str}
            {"type": "delta", "data": dict}
        """
        self._buffer += token
        events = []

        if self._state == "STREAMING_TEXT":
            events.extend(self._process_text())
        elif self._state == "BUFFERING_JSON":
            events.extend(self._process_json())

        return events

    def finish(self) -> list[dict]:
        """Call when the stream ends. Returns any final events."""
        events = []

        if self._state == "BUFFERING_JSON":
            # Stream cut off mid-JSON — try to parse what we have
            self._errors.append("Stream ended inside JSON fence (truncated)")
            parsed = self._try_parse_json(self._json_buffer)
            if parsed:
                events.append({"type": "delta", "data": parsed})
            self._json_buffer = ""
            self._state = "STREAMING_TEXT"

        # Flush any remaining text buffer
        if self._buffer:
            events.append({"type": "token", "content": self._buffer})
            self._text_parts.append(self._buffer)
            self._buffer = ""

        return events

    @property
    def text(self) -> str:
        return "".join(self._text_parts).strip()

    @property
    def deltas(self) -> list[dict]:
        return self._deltas

    @property
    def error(self) -> str | None:
        return "; ".join(self._errors) if self._errors else None

    def _process_text(self) -> list[dict]:
        events = []
        fence_start = self._buffer.find("```json")

        if fence_start == -1:
            # No fence marker yet — emit buffered text if long enough
            # Keep last 10 chars in case "```json" spans token boundary
            if len(self._buffer) > 10:
                emit = self._buffer[:-10]
                self._buffer = self._buffer[-10:]
                events.append({"type": "token", "content": emit})
                self._text_parts.append(emit)
        else:
            # Emit text before the fence
            if fence_start > 0:
                text_before = self._buffer[:fence_start]
                events.append({"type": "token", "content": text_before})
                self._text_parts.append(text_before)
            # Switch to JSON buffering
            after_marker = self._buffer[fence_start + 7:]  # len("```json") == 7
            self._buffer = ""
            self._json_buffer = after_marker
            self._state = "BUFFERING_JSON"

        return events

    def _process_json(self) -> list[dict]:
        events = []
        self._json_buffer += ""  # noop — already appended via _buffer flow

        # Check the combined json buffer for closing fence
        # The token was appended to self._buffer in feed(), but we need to
        # move it to json_buffer
        self._json_buffer = self._json_buffer  # already set in _process_text transition

        # Actually, after transition, new tokens go to _buffer but state is BUFFERING_JSON
        # So we need to move _buffer content to _json_buffer
        self._json_buffer += self._buffer
        self._buffer = ""

        fence_end = self._json_buffer.find("```")
        if fence_end != -1:
            json_str = self._json_buffer[:fence_end].strip()
            remaining = self._json_buffer[fence_end + 3:]
            self._json_buffer = ""
            self._buffer = remaining
            self._state = "STREAMING_TEXT"

            parsed = self._try_parse_json(json_str)
            if parsed:
                events.append({"type": "delta", "data": parsed})

            # Process any remaining text
            if self._buffer:
                events.extend(self._process_text())

        return events

    def _try_parse_json(self, json_str: str) -> dict | None:
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as e:
            self._errors.append(f"Invalid JSON in fence: {e}")
            return None

        if not isinstance(parsed, dict) or "deltas" not in parsed:
            self._errors.append("JSON block missing 'deltas' array")
            return None

        valid_deltas = []
        for i, delta in enumerate(parsed["deltas"]):
            err = _validate_delta(delta)
            if err:
                self._errors.append(f"Delta {i + 1}: {err}")
            else:
                valid_deltas.append(delta)

        if valid_deltas:
            self._deltas.extend(valid_deltas)
            return {"deltas": valid_deltas}
        return None


def apply_deltas(itinerary: dict, deltas: list[dict]) -> dict:
    """
    Apply itinerary change deltas to state. Mutates and returns itinerary.

    itinerary shape: {"days": {"1": {"slots": [...]}, ...}}
    """
    if "days" not in itinerary:
        itinerary["days"] = {}

    for delta in deltas:
        action = delta.get("action")
        day_key = str(delta.get("day", ""))

        if action == "add":
            if day_key not in itinerary["days"]:
                itinerary["days"][day_key] = {"slots": []}
            itinerary["days"][day_key]["slots"].append(delta["slot"])
            itinerary["days"][day_key]["slots"].sort(
                key=lambda s: s.get("start_time", "99:99")
            )

        elif action == "remove":
            if day_key in itinerary["days"]:
                itinerary["days"][day_key]["slots"] = [
                    s for s in itinerary["days"][day_key]["slots"]
                    if s.get("activity_id") != delta.get("activity_id")
                ]

        elif action == "move":
            from_day = str(delta.get("from_day", ""))
            to_day = str(delta.get("to_day", ""))
            activity_id = delta.get("activity_id")

            slot = None
            if from_day in itinerary["days"]:
                for s in itinerary["days"][from_day]["slots"]:
                    if s.get("activity_id") == activity_id:
                        slot = s
                        break
                if slot:
                    itinerary["days"][from_day]["slots"].remove(slot)
                    slot["start_time"] = delta.get("start_time", slot.get("start_time"))
                    if to_day not in itinerary["days"]:
                        itinerary["days"][to_day] = {"slots": []}
                    itinerary["days"][to_day]["slots"].append(slot)
                    itinerary["days"][to_day]["slots"].sort(
                        key=lambda s: s.get("start_time", "99:99")
                    )

        elif action == "clear_day":
            if day_key in itinerary["days"]:
                itinerary["days"][day_key]["slots"] = []

    return itinerary


def format_itinerary(itinerary: dict) -> str:
    """Format itinerary state as readable text for system prompt injection."""
    days = itinerary.get("days", {})
    if not days:
        return "空白 — 未有任何活動。"

    lines = []
    for day_num in sorted(days.keys(), key=lambda k: int(k)):
        day = days[day_num]
        lines.append(f"\nDay {day_num}:")
        for slot in day.get("slots", []):
            time = slot.get("start_time", "??:??")
            end = slot.get("end_time", "")
            aid = slot.get("activity_id", "?")
            notes = slot.get("notes", "")
            time_str = f"{time}-{end}" if end else time
            line = f"  {time_str}  {aid}"
            if notes:
                line += f" — {notes}"
            lines.append(line)
    return "\n".join(lines)

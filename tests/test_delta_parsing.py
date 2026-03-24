"""Tests for delta fence parsing — parse_deltas() and StreamDeltaParser."""

import pytest
from sidantrip.planner.parser import parse_deltas, StreamDeltaParser


# --- parse_deltas() tests (non-streaming) ---


class TestParseDeltasHappyPath:
    def test_single_add_delta(self):
        raw = """好啦，Day 1咁行：

```json
{"deltas": [{"action": "add", "day": 1, "slot": {"activity_id": "gyeongbokgung-palace", "start_time": "10:00", "end_time": "12:00", "notes": "朝早去人少啲"}}]}
```

記住帶護照！"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert len(deltas) == 1
        assert deltas[0]["action"] == "add"
        assert deltas[0]["slot"]["activity_id"] == "gyeongbokgung-palace"
        assert "記住帶護照" in text
        assert "```json" not in text

    def test_multiple_deltas_in_one_block(self):
        raw = """搞掂Day 1同Day 2：

```json
{"deltas": [
    {"action": "add", "day": 1, "slot": {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00"}},
    {"action": "add", "day": 1, "slot": {"activity_id": "a2", "start_time": "12:00", "end_time": "14:00"}},
    {"action": "add", "day": 2, "slot": {"activity_id": "a3", "start_time": "10:00", "end_time": "12:00"}}
]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert len(deltas) == 3
        assert "搞掂" in text

    def test_remove_delta(self):
        raw = """OK刪咗啦。

```json
{"deltas": [{"action": "remove", "day": 1, "activity_id": "a1"}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert deltas[0]["action"] == "remove"

    def test_move_delta(self):
        raw = """搬咗去Day 2。

```json
{"deltas": [{"action": "move", "activity_id": "a1", "from_day": 1, "to_day": 2, "start_time": "14:00"}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert deltas[0]["action"] == "move"
        assert deltas[0]["to_day"] == 2

    def test_clear_day_delta(self):
        raw = """Clear咗Day 3。

```json
{"deltas": [{"action": "clear_day", "day": 3}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert deltas[0]["action"] == "clear_day"
        assert deltas[0]["day"] == 3


class TestParseDeltasMultiBlock:
    def test_two_json_blocks(self):
        raw = """First batch:

```json
{"deltas": [{"action": "add", "day": 1, "slot": {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00"}}]}
```

Second batch:

```json
{"deltas": [{"action": "add", "day": 2, "slot": {"activity_id": "a2", "start_time": "10:00", "end_time": "12:00"}}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert error is None
        assert len(deltas) == 2
        assert deltas[0]["slot"]["activity_id"] == "a1"
        assert deltas[1]["slot"]["activity_id"] == "a2"


class TestParseDeltasMalformed:
    def test_invalid_json(self):
        raw = """Here you go:

```json
{not valid json at all}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "invalid JSON" in error

    def test_missing_deltas_key(self):
        raw = """Done:

```json
{"changes": [{"action": "add", "day": 1}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "missing" in error.lower()

    def test_invalid_action(self):
        raw = """Here:

```json
{"deltas": [{"action": "delete_all", "day": 1}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "invalid action" in error

    def test_add_missing_slot(self):
        raw = """Plan:

```json
{"deltas": [{"action": "add", "day": 1}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "slot" in error

    def test_add_missing_time(self):
        raw = """Plan:

```json
{"deltas": [{"action": "add", "day": 1, "slot": {"activity_id": "a1"}}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "start_time" in error

    def test_move_missing_fields(self):
        raw = """Move:

```json
{"deltas": [{"action": "move", "activity_id": "a1"}]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 0
        assert error is not None
        assert "from_day" in error


class TestParseDeltasNoJson:
    def test_text_only_response(self):
        text, deltas, error = parse_deltas("冇問題！你仲想去邊？")
        assert text == "冇問題！你仲想去邊？"
        assert deltas == []
        assert error is None

    def test_empty_response(self):
        text, deltas, error = parse_deltas("")
        assert text == ""
        assert deltas == []
        assert error is None


class TestParseDeltasPartialValid:
    def test_one_valid_one_invalid_delta(self):
        raw = """Mixed:

```json
{"deltas": [
    {"action": "add", "day": 1, "slot": {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00"}},
    {"action": "add", "day": 2}
]}
```"""
        text, deltas, error = parse_deltas(raw)
        assert len(deltas) == 1
        assert deltas[0]["slot"]["activity_id"] == "a1"
        assert error is not None  # reports the invalid one


# --- StreamDeltaParser tests ---


class TestStreamDeltaParser:
    def test_text_only_stream(self):
        parser = StreamDeltaParser()
        events = []
        for token in ["Hello ", "world!"]:
            events.extend(parser.feed(token))
        events.extend(parser.finish())
        token_events = [e for e in events if e["type"] == "token"]
        full_text = "".join(e["content"] for e in token_events)
        assert "Hello world!" in full_text

    def test_stream_with_json_block(self):
        parser = StreamDeltaParser()
        tokens = [
            "OK plan ",
            "好啦\n\n",
            "```json\n",
            '{"deltas": [{"action": "add", "day": 1, ',
            '"slot": {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00"}}]}\n',
            "```\n",
            "記住！",
        ]
        all_events = []
        for t in tokens:
            all_events.extend(parser.feed(t))
        all_events.extend(parser.finish())

        delta_events = [e for e in all_events if e["type"] == "delta"]
        assert len(delta_events) == 1
        assert delta_events[0]["data"]["deltas"][0]["action"] == "add"
        assert parser.text  # has text content
        assert "```json" not in parser.text

    def test_stream_cutoff_mid_json(self):
        parser = StreamDeltaParser()
        tokens = ["Text\n\n", "```json\n", '{"deltas": [{"action": "add"']
        for t in tokens:
            parser.feed(t)
        events = parser.finish()
        assert parser.error is not None
        assert "truncated" in parser.error.lower() or "ended" in parser.error.lower()

"""Tests for apply_deltas() — itinerary state mutations."""

import copy
import pytest
from sidantrip.planner.parser import apply_deltas


def _empty_itinerary():
    return {"days": {}}


def _itinerary_with_day1():
    return {
        "days": {
            "1": {
                "slots": [
                    {"activity_id": "a1", "start_time": "09:00", "end_time": "11:00", "notes": "morning"},
                    {"activity_id": "a2", "start_time": "12:00", "end_time": "14:00", "notes": "lunch"},
                ]
            }
        }
    }


class TestAddAction:
    def test_add_to_empty_itinerary(self):
        it = _empty_itinerary()
        deltas = [{"action": "add", "day": 1, "slot": {
            "activity_id": "a1", "start_time": "10:00", "end_time": "12:00"
        }}]
        result = apply_deltas(it, deltas)
        assert "1" in result["days"]
        assert len(result["days"]["1"]["slots"]) == 1
        assert result["days"]["1"]["slots"][0]["activity_id"] == "a1"

    def test_add_multiple_slots_sorted_by_time(self):
        it = _empty_itinerary()
        deltas = [
            {"action": "add", "day": 1, "slot": {
                "activity_id": "a2", "start_time": "14:00", "end_time": "16:00"
            }},
            {"action": "add", "day": 1, "slot": {
                "activity_id": "a1", "start_time": "09:00", "end_time": "11:00"
            }},
        ]
        result = apply_deltas(it, deltas)
        slots = result["days"]["1"]["slots"]
        assert slots[0]["activity_id"] == "a1"
        assert slots[1]["activity_id"] == "a2"

    def test_add_to_existing_day(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "add", "day": 1, "slot": {
            "activity_id": "a3", "start_time": "16:00", "end_time": "18:00"
        }}]
        result = apply_deltas(it, deltas)
        assert len(result["days"]["1"]["slots"]) == 3

    def test_add_to_new_day(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "add", "day": 2, "slot": {
            "activity_id": "b1", "start_time": "10:00", "end_time": "12:00"
        }}]
        result = apply_deltas(it, deltas)
        assert "2" in result["days"]
        assert len(result["days"]["2"]["slots"]) == 1


class TestRemoveAction:
    def test_remove_existing_activity(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "remove", "day": 1, "activity_id": "a1"}]
        result = apply_deltas(it, deltas)
        slots = result["days"]["1"]["slots"]
        assert len(slots) == 1
        assert slots[0]["activity_id"] == "a2"

    def test_remove_nonexistent_activity(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "remove", "day": 1, "activity_id": "does-not-exist"}]
        result = apply_deltas(it, deltas)
        assert len(result["days"]["1"]["slots"]) == 2  # unchanged

    def test_remove_from_nonexistent_day(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "remove", "day": 99, "activity_id": "a1"}]
        result = apply_deltas(it, deltas)
        assert len(result["days"]["1"]["slots"]) == 2  # unchanged


class TestMoveAction:
    def test_move_activity_between_days(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "move", "activity_id": "a1", "from_day": 1, "to_day": 2, "start_time": "15:00"}]
        result = apply_deltas(it, deltas)
        # Removed from day 1
        day1_ids = [s["activity_id"] for s in result["days"]["1"]["slots"]]
        assert "a1" not in day1_ids
        # Added to day 2 with new time
        assert "2" in result["days"]
        day2_slots = result["days"]["2"]["slots"]
        assert len(day2_slots) == 1
        assert day2_slots[0]["activity_id"] == "a1"
        assert day2_slots[0]["start_time"] == "15:00"

    def test_move_nonexistent_activity(self):
        it = _itinerary_with_day1()
        original = copy.deepcopy(it)
        deltas = [{"action": "move", "activity_id": "nope", "from_day": 1, "to_day": 2, "start_time": "10:00"}]
        result = apply_deltas(it, deltas)
        assert result["days"]["1"]["slots"] == original["days"]["1"]["slots"]


class TestClearDayAction:
    def test_clear_existing_day(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "clear_day", "day": 1}]
        result = apply_deltas(it, deltas)
        assert result["days"]["1"]["slots"] == []

    def test_clear_nonexistent_day(self):
        it = _itinerary_with_day1()
        deltas = [{"action": "clear_day", "day": 99}]
        result = apply_deltas(it, deltas)
        assert len(result["days"]["1"]["slots"]) == 2  # unchanged


class TestEdgeCases:
    def test_empty_deltas_list(self):
        it = _itinerary_with_day1()
        original = copy.deepcopy(it)
        result = apply_deltas(it, [])
        assert result == original

    def test_missing_days_key(self):
        it = {}
        deltas = [{"action": "add", "day": 1, "slot": {
            "activity_id": "a1", "start_time": "10:00", "end_time": "12:00"
        }}]
        result = apply_deltas(it, deltas)
        assert "days" in result
        assert len(result["days"]["1"]["slots"]) == 1

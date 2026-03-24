"""Tests for db_tools.py with real Seoul YAML fixtures."""

import os
import pytest
import tempfile
import yaml
from pathlib import Path

from sidantrip.tools.db_tools import (
    load_city_index,
    load_clusters,
    load_activity_detail,
    load_city_meta,
    search_activities,
    load_schema_template,
    DB_PATH,
)

# Fixtures: create a temp DB structure mimicking destinations/south-korea/seoul/


@pytest.fixture
def seoul_db(tmp_path, monkeypatch):
    """Create a minimal Seoul activity DB for testing."""
    import sidantrip.tools.db_tools as db_mod

    db_root = tmp_path / "destinations"
    seoul_dir = db_root / "south-korea" / "seoul"
    sightseeing_dir = seoul_dir / "sightseeing"
    food_dir = seoul_dir / "food"
    schema_dir = tmp_path / "schema"

    for d in [sightseeing_dir, food_dir, schema_dir]:
        d.mkdir(parents=True)

    # _index.yaml
    index = {
        "total_activities": 3,
        "categories": {
            "sightseeing": [
                {"id": "gyeongbokgung-palace", "name": "Gyeongbokgung Palace", "area": "Jongno", "tags": ["palace", "history"], "duration": 120, "cost": "₩3,000"},
                {"id": "bukchon-hanok-village", "name": "Bukchon Hanok Village", "area": "Jongno", "tags": ["traditional", "village"], "duration": 90, "cost": "Free"},
            ],
            "food": [
                {"id": "gwangjang-market", "name": "Gwangjang Market", "area": "Jongno", "tags": ["market", "street-food"], "duration": 60, "cost": "₩10,000-20,000"},
            ],
        },
    }
    (seoul_dir / "_index.yaml").write_text(yaml.dump(index))

    # _clusters.yaml
    clusters = {
        "neighborhoods": [
            {
                "name": "Jongno",
                "activity_count": 3,
                "center": {"lat": 37.5796, "lng": 126.9770},
                "activities": [
                    {"id": "gyeongbokgung-palace", "name": "Gyeongbokgung Palace", "category": "sightseeing"},
                    {"id": "bukchon-hanok-village", "name": "Bukchon Hanok Village", "category": "sightseeing"},
                    {"id": "gwangjang-market", "name": "Gwangjang Market", "category": "food"},
                ],
            }
        ]
    }
    (seoul_dir / "_clusters.yaml").write_text(yaml.dump(clusters))

    # _meta.yaml
    meta = {
        "city": "Seoul",
        "country": "South Korea",
        "timezone": "Asia/Seoul",
        "currency": "KRW",
        "language": "Korean",
    }
    (seoul_dir / "_meta.yaml").write_text(yaml.dump(meta))

    # Activity files
    palace = {
        "id": "gyeongbokgung-palace",
        "name": "Gyeongbokgung Palace",
        "category": "sightseeing",
        "area": "Jongno",
        "description": "The main royal palace of the Joseon dynasty.",
        "opening_hours": "09:00-18:00",
        "cost": "₩3,000",
    }
    (sightseeing_dir / "gyeongbokgung-palace.yaml").write_text(yaml.dump(palace))

    hanok = {
        "id": "bukchon-hanok-village",
        "name": "Bukchon Hanok Village",
        "category": "sightseeing",
        "area": "Jongno",
    }
    (sightseeing_dir / "bukchon-hanok-village.yaml").write_text(yaml.dump(hanok))

    market = {
        "id": "gwangjang-market",
        "name": "Gwangjang Market",
        "category": "food",
        "area": "Jongno",
    }
    (food_dir / "gwangjang-market.yaml").write_text(yaml.dump(market))

    # Schema template
    (schema_dir / "sightseeing.template.yaml").write_text("# Sightseeing template\nid: \nname: \n")

    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path))
    return tmp_path


class TestLoadCityIndex:
    def test_loads_seoul_index(self, seoul_db):
        result = load_city_index("seoul")
        assert "Gyeongbokgung Palace" in result
        assert "Gwangjang Market" in result
        assert "3 activities" in result

    def test_missing_destination(self, seoul_db):
        result = load_city_index("atlantis")
        assert "No index" in result or "not found" in result.lower()


class TestLoadClusters:
    def test_loads_seoul_clusters(self, seoul_db):
        result = load_clusters("seoul")
        assert "Jongno" in result
        assert "gyeongbokgung-palace" in result

    def test_missing_destination(self, seoul_db):
        result = load_clusters("atlantis")
        assert "No clusters" in result


class TestLoadActivityDetail:
    def test_loads_existing_activity(self, seoul_db):
        result = load_activity_detail("seoul", "gyeongbokgung-palace")
        assert "Gyeongbokgung" in result
        assert "Joseon" in result

    def test_loads_activity_in_subfolder(self, seoul_db):
        result = load_activity_detail("seoul", "gwangjang-market")
        assert "Gwangjang" in result

    def test_missing_activity(self, seoul_db):
        result = load_activity_detail("seoul", "does-not-exist")
        assert "not found" in result.lower()


class TestLoadCityMeta:
    def test_loads_seoul_meta(self, seoul_db):
        result = load_city_meta("seoul")
        assert "Seoul" in result
        assert "KRW" in result

    def test_missing_destination(self, seoul_db):
        result = load_city_meta("atlantis")
        assert "No metadata" in result


class TestSearchActivities:
    def test_search_by_name(self, seoul_db):
        result = search_activities("seoul", "palace")
        assert "gyeongbokgung-palace" in result
        assert "1 matches" in result

    def test_search_by_tag(self, seoul_db):
        result = search_activities("seoul", "street-food")
        assert "gwangjang-market" in result

    def test_search_with_category_filter(self, seoul_db):
        result = search_activities("seoul", "palace", category="food")
        assert "No activities" in result

    def test_search_no_results(self, seoul_db):
        result = search_activities("seoul", "unicorn")
        assert "No activities" in result


class TestLoadSchemaTemplate:
    def test_loads_template(self, seoul_db):
        result = load_schema_template("sightseeing")
        assert "Sightseeing template" in result

    def test_missing_template(self, seoul_db):
        result = load_schema_template("nonexistent")
        assert "No schema template" in result

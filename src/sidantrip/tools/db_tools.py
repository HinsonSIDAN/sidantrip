"""
Tools for reading the SidanTrip activity database.
Three-layer index: _index.yaml (manifest) → _clusters.yaml (neighborhoods) → full YAML (on-demand).
"""

import os
import yaml
from typing import Optional


DB_PATH = os.environ.get("SIDANTRIP_DB_PATH", "../sidantrip-db")


def _dest_path(destination: str) -> str:
    """Find destination directory under destinations/{country}/{city}/.
    Accepts just the city name (e.g., 'seoul') and searches across countries."""
    from pathlib import Path
    dest_root = Path(DB_PATH) / "destinations"
    # Search for city across all country directories
    for country_dir in dest_root.iterdir():
        if not country_dir.is_dir():
            continue
        city_dir = country_dir / destination
        if city_dir.exists():
            return str(city_dir)
    # Fallback: try direct path (backwards compat with flat structure)
    return os.path.join(DB_PATH, "destinations", destination)


def load_city_index(destination: str) -> str:
    """Load the compact activity manifest for a destination. Returns all activities with
    id, name, category, area, vibe tags, duration, and cost — enough for the planner to
    suggest activities without loading full details."""
    path = os.path.join(_dest_path(destination), "_index.yaml")
    try:
        with open(path, "r") as f:
            index = yaml.safe_load(f)
        # Format as a readable string for the LLM
        lines = [f"# {destination.title()} Activity Index ({index.get('total_activities', '?')} activities)\n"]
        for cat_name, activities in index.get("categories", {}).items():
            lines.append(f"\n## {cat_name.title()} ({len(activities)})")
            for act in activities:
                tags = ", ".join(act.get("tags", []))
                lines.append(
                    f"- [{act['id']}] {act['name']} | {act.get('area', '?')} | "
                    f"{act.get('duration', '?')}min | {act.get('cost', '?')} | {tags}"
                )
        return "\n".join(lines)
    except FileNotFoundError:
        return f"No index found for {destination}. Available destinations may need compiling."


def load_clusters(destination: str) -> str:
    """Load neighborhood clusters for a destination. Returns activities grouped by geographic
    area — useful for planning efficient day itineraries that minimize travel time."""
    path = os.path.join(_dest_path(destination), "_clusters.yaml")
    try:
        with open(path, "r") as f:
            clusters = yaml.safe_load(f)
        lines = [f"# {destination.title()} Neighborhoods\n"]
        for nb in clusters.get("neighborhoods", []):
            name = nb["name"]
            count = nb.get("activity_count", len(nb.get("activities", [])))
            lines.append(f"\n## {name} ({count} activities)")
            if nb.get("center"):
                lines.append(f"Center: {nb['center']['lat']}, {nb['center']['lng']}")
            for act in nb.get("activities", []):
                lines.append(f"  - [{act['id']}] {act['name']} ({act['category']})")
        return "\n".join(lines)
    except FileNotFoundError:
        return f"No clusters found for {destination}."


def load_activity_detail(destination: str, activity_id: str) -> str:
    """Load full YAML details for a specific activity. Use this when the traveler asks
    for more info about a specific place, or when you need opening hours, prices, etc."""
    dest_dir = _dest_path(destination)
    # Search all YAML files recursively — works with both flat and subfolder layouts
    from pathlib import Path
    for filepath in Path(dest_dir).rglob("*.yaml"):
        if filepath.name.startswith("_"):
            continue
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            data = yaml.safe_load(content)
            if data and data.get("id") == activity_id:
                return content
        except Exception:
            continue
    return f"Activity {activity_id} not found in {destination}."


def load_city_meta(destination: str) -> str:
    """Load city metadata — timezone, currency, language, practical tips, emergency numbers.
    Useful for providing traveler with general info about the destination."""
    path = os.path.join(_dest_path(destination), "_meta.yaml")
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"No metadata found for {destination}."


def search_activities(destination: str, query: str, category: Optional[str] = None) -> str:
    """Search activities by keyword in name, description, or tags. Optionally filter by
    category (sightseeing, experience, food). Returns matching activity summaries."""
    path = os.path.join(_dest_path(destination), "_index.yaml")
    try:
        with open(path, "r") as f:
            index = yaml.safe_load(f)
    except FileNotFoundError:
        return f"No index for {destination}."

    query_lower = query.lower()
    matches = []

    for cat_name, activities in index.get("categories", {}).items():
        if category and cat_name != category:
            continue
        for act in activities:
            searchable = f"{act.get('name', '')} {' '.join(act.get('tags', []))}".lower()
            if query_lower in searchable:
                matches.append(
                    f"[{act['id']}] {act['name']} | {cat_name} | {act.get('area', '?')} | "
                    f"{act.get('duration', '?')}min | {act.get('cost', '?')}"
                )

    if not matches:
        return f"No activities matching '{query}' in {destination}."
    return f"Found {len(matches)} matches:\n" + "\n".join(matches)


def load_schema_template(category: str) -> str:
    """Load the YAML schema template for a given activity category (sightseeing, experience, food).
    Used by the researcher to produce correctly formatted entries."""
    path = os.path.join(DB_PATH, "schema", f"{category}.template.yaml")
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"No schema template found for category '{category}'."

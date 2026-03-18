"""
SmartTrip AI - Spain POI Dataset Builder
One-time script to populate phase1_data.xlsx with 500+ attractions
across 10+ Spanish cities using Overpass API and OpenTripMap.

Usage:
    cd Trip_optimizer
    python data/build_dataset.py

Prerequisites:
    pip install requests openpyxl pandas python-dotenv
    Set OPENTRIPMAP_API_KEY in .env (optional but improves quality)

The script will:
    1. Query Overpass API for tourism/historic/leisure POIs per city
    2. Query OpenTripMap for popularity ratings (if API key available)
    3. Merge and deduplicate results
    4. Write new rows to the Attractions sheet in phase1_data.xlsx
    5. Preserve existing manually-curated rows for Madrid/Barcelona/Seville
"""

import os
import sys
import time
import uuid
import json
import math
import logging
import pathlib
import requests
import pandas as pd
from typing import Optional

# Allow running from any directory
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

EXCEL_PATH = ROOT / "engine" / "phase1_data.xlsx"
OTM_KEY = os.getenv("OPENTRIPMAP_API_KEY", "").strip()

# ── City Definitions ─────────────────────────────────────────────────────────

CITIES = {
    "Valencia": {
        "lat": 39.4699, "lon": -0.3763,
        "bbox": (39.39, -0.43, 39.55, -0.30),
        "climate": "coastal_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Bilbao": {
        "lat": 43.2630, "lon": -2.9350,
        "bbox": (43.22, -3.02, 43.31, -2.87),
        "climate": "atlantic",
        "timezone": "Europe/Madrid",
    },
    "Granada": {
        "lat": 37.1773, "lon": -3.5986,
        "bbox": (37.13, -3.65, 37.23, -3.55),
        "climate": "inland_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Malaga": {
        "lat": 36.7213, "lon": -4.4213,
        "bbox": (36.68, -4.50, 36.76, -4.36),
        "climate": "hot_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Toledo": {
        "lat": 39.8567, "lon": -4.0244,
        "bbox": (39.83, -4.06, 39.88, -3.98),
        "climate": "continental_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Salamanca": {
        "lat": 40.9701, "lon": -5.6635,
        "bbox": (40.93, -5.72, 41.01, -5.61),
        "climate": "continental",
        "timezone": "Europe/Madrid",
    },
    "San Sebastian": {
        "lat": 43.3183, "lon": -1.9812,
        "bbox": (43.29, -2.03, 43.35, -1.93),
        "climate": "atlantic",
        "timezone": "Europe/Madrid",
    },
    "Cordoba": {
        "lat": 37.8882, "lon": -4.7794,
        "bbox": (37.85, -4.83, 37.93, -4.73),
        "climate": "hot_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Palma": {
        "lat": 39.5696, "lon": 2.6502,
        "bbox": (39.52, 2.60, 39.62, 2.72),
        "climate": "coastal_mediterranean",
        "timezone": "Europe/Madrid",
    },
    "Zaragoza": {
        "lat": 41.6488, "lon": -0.8891,
        "bbox": (41.61, -0.96, 41.70, -0.82),
        "climate": "continental_mediterranean",
        "timezone": "Europe/Madrid",
    },
}

# ── OSM Tag → Category Mapping ───────────────────────────────────────────────

def assign_category(osm_tags: dict) -> str:
    tourism = osm_tags.get("tourism", "")
    historic = osm_tags.get("historic", "")
    leisure = osm_tags.get("leisure", "")
    amenity = osm_tags.get("amenity", "")

    if tourism in ("museum", "gallery"):
        return "museum"
    if tourism in ("attraction", "viewpoint", "artwork"):
        return "landmark"
    if historic in ("monument", "memorial", "castle", "ruins", "archaeological_site", "building"):
        return "landmark"
    if tourism == "theme_park":
        return "outdoor"
    if tourism in ("zoo", "aquarium"):
        return "indoor"
    if leisure in ("park", "garden", "nature_reserve"):
        return "outdoor"
    if leisure in ("beach_resort",) or osm_tags.get("natural") == "beach":
        return "beach"
    if amenity in ("theatre", "cinema", "arts_centre"):
        return "indoor"
    if amenity in ("place_of_worship",) or historic in ("church", "cathedral", "mosque", "synagogue"):
        return "religious"
    if amenity in ("restaurant", "cafe", "market", "marketplace", "food_court"):
        return "food"
    if tourism == "hotel":
        return None  # Skip hotels
    return "landmark"  # Default


# ── Visit Duration Defaults (minutes) ────────────────────────────────────────

VISIT_DURATION = {
    "museum":    90,
    "landmark":  45,
    "outdoor":   75,
    "beach":    120,
    "religious": 40,
    "food":      60,
    "indoor":    80,
}

IDEAL_TIMES = {
    "museum":    (10, 18),
    "landmark":  (9, 19),
    "outdoor":   (8, 12),   # mornings to avoid heat
    "beach":     (9, 18),
    "religious": (9, 18),
    "food":      (12, 22),
    "indoor":    (10, 20),
}

HEAT_SENSITIVE = {"outdoor", "beach"}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Overpass POI Fetcher ──────────────────────────────────────────────────────

def fetch_overpass_pois(city_name: str, bbox: tuple) -> list:
    """Fetch POIs from Overpass API for a city bounding box."""
    south, west, north, east = bbox
    tags = ["tourism", "historic", "leisure", "amenity"]
    tag_filters = "\n".join(
        f'  node["{t}"]({south},{west},{north},{east});' for t in tags
    )
    query = f"""
[out:json][timeout:60];
(
{tag_filters}
);
out body;
"""
    try:
        logger.info(f"  Querying Overpass for {city_name}...")
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data=query,
            timeout=70,
            headers={"Content-Type": "text/plain"},
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        logger.info(f"  → {len(elements)} raw elements from Overpass")

        results = []
        for el in elements:
            tags_data = el.get("tags", {})
            name = tags_data.get("name") or tags_data.get("name:en", "")
            if not name or len(name) < 3:
                continue
            cat = assign_category(tags_data)
            if cat is None:
                continue
            results.append({
                "name": name,
                "name_es": tags_data.get("name:es", name),
                "lat": float(el.get("lat", 0)),
                "lon": float(el.get("lon", 0)),
                "category": cat,
                "osm_tags": tags_data,
                "source": "overpass",
            })
        return results

    except Exception as e:
        logger.warning(f"  Overpass error for {city_name}: {e}")
        return []


# ── OpenTripMap POI Fetcher ───────────────────────────────────────────────────

def fetch_opentripmap_pois(lat: float, lon: float, api_key: str) -> list:
    """Fetch top-rated POIs from OpenTripMap."""
    if not api_key:
        return []
    try:
        params = {
            "radius": 10000,
            "lon": lon,
            "lat": lat,
            "kinds": "interesting_places",
            "rate": 2,
            "format": "json",
            "limit": 150,
            "apikey": api_key,
        }
        resp = requests.get(
            "https://api.opentripmap.com/0.1/en/places/radius",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        features = resp.json()

        results = []
        for f in features:
            props = f.get("properties", {})
            name = props.get("name") or props.get("name_en", "")
            if not name or len(name) < 3:
                continue
            geo = f.get("geometry", {}).get("coordinates", [lon, lat])
            kinds = props.get("kinds", "")
            cat = _otm_kinds_to_category(kinds)
            if cat is None:
                continue
            results.append({
                "name": name,
                "name_es": name,
                "lat": float(geo[1]),
                "lon": float(geo[0]),
                "category": cat,
                "priority_score": float(props.get("rate", 0)) * 3.3,  # 0-3 → 0-10
                "wikidata": props.get("wikidata", ""),
                "source": "opentripmap",
            })
        return results

    except Exception as e:
        logger.warning(f"  OpenTripMap error: {e}")
        return []


def _otm_kinds_to_category(kinds: str) -> Optional[str]:
    kinds_lower = kinds.lower()
    if "museum" in kinds_lower or "gallery" in kinds_lower:
        return "museum"
    if "church" in kinds_lower or "cathedral" in kinds_lower or "mosque" in kinds_lower:
        return "religious"
    if "natural" in kinds_lower or "parks" in kinds_lower or "garden" in kinds_lower:
        return "outdoor"
    if "beach" in kinds_lower:
        return "beach"
    if "food" in kinds_lower or "restaurants" in kinds_lower:
        return "food"
    if "interesting_places" in kinds_lower or "architecture" in kinds_lower:
        return "landmark"
    if "theatres" in kinds_lower or "cinema" in kinds_lower:
        return "indoor"
    return "landmark"


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(pois: list, radius_km: float = 0.05) -> list:
    """Remove duplicates within radius_km of each other (keep highest priority)."""
    kept = []
    for poi in pois:
        duplicate = False
        for k in kept:
            dist = haversine_km(poi["lat"], poi["lon"], k["lat"], k["lon"])
            if dist < radius_km and poi["name"].lower() == k["name"].lower():
                duplicate = True
                # Merge: take higher priority score
                if poi.get("priority_score", 5) > k.get("priority_score", 5):
                    k["priority_score"] = poi["priority_score"]
                break
        if not duplicate:
            kept.append(poi)
    return kept


# ── Row Builder ───────────────────────────────────────────────────────────────

def build_row(poi: dict, city_name: str) -> dict:
    """Convert a raw POI dict to an Excel-compatible row."""
    cat = poi.get("category", "landmark")
    visit_dur = VISIT_DURATION.get(cat, 60)
    ideal = IDEAL_TIMES.get(cat, (9, 18))
    heat_sens = cat in HEAT_SENSITIVE

    # Derive zone from rough coordinates
    # Simple rule: if within 1km of city center → Central, else Tourist Cluster
    city_cfg = CITIES.get(city_name, {})
    city_lat = city_cfg.get("lat", poi["lat"])
    city_lon = city_cfg.get("lon", poi["lon"])
    dist_to_center = haversine_km(poi["lat"], poi["lon"], city_lat, city_lon)

    if dist_to_center <= 1.5:
        zone = "Central"
    elif dist_to_center <= 3.0:
        zone = "Tourist Cluster"
    elif dist_to_center <= 6.0:
        zone = "Residential"
    else:
        zone = "Peripheral"

    # Priority: use OpenTripMap rate if available, else estimate from category
    default_priorities = {"museum": 7.5, "landmark": 6.5, "outdoor": 6.0,
                          "beach": 7.0, "religious": 6.0, "food": 5.5, "indoor": 6.0}
    priority = poi.get("priority_score") or default_priorities.get(cat, 6.0)
    priority = round(min(max(priority, 1.0), 10.0), 1)

    # Default peak hours by category
    peak_map = {
        "museum": [11, 12, 13, 15, 16],
        "landmark": [10, 11, 12, 16, 17],
        "outdoor": [10, 11, 17, 18],
        "beach": [11, 12, 13, 14, 15],
        "religious": [10, 11, 12],
        "food": [13, 14, 20, 21],
        "indoor": [11, 12, 15, 16, 17],
    }
    peak_hours = peak_map.get(cat, [11, 12, 15, 16])

    osm_tags = poi.get("osm_tags", {})

    return {
        "id": str(uuid.uuid4()),
        "name": poi["name"],
        "name_es": poi.get("name_es", poi["name"]),
        "city": city_name,
        "latitude": round(poi["lat"], 6),
        "longitude": round(poi["lon"], 6),
        "category": cat,
        "zone": zone,
        "average_visit_duration": visit_dur,
        "ideal_time_start": ideal[0],
        "ideal_time_end": ideal[1],
        "peak_hours_json": json.dumps(peak_hours),
        "heat_sensitive": heat_sens,
        "sunset_sensitive": cat in ("outdoor", "landmark"),
        "priority_score": priority,
        "description": osm_tags.get("description", ""),
        "opening_hours": osm_tags.get("opening_hours", ""),
        "fee": osm_tags.get("fee", ""),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def build_city_dataset(city_name: str, city_cfg: dict) -> pd.DataFrame:
    """Build POI dataset for a single city."""
    logger.info(f"\nBuilding dataset for {city_name}...")
    lat, lon = city_cfg["lat"], city_cfg["lon"]
    bbox = city_cfg["bbox"]

    # Fetch from Overpass (free, always available)
    overpass_pois = []
    for attempt in range(3):
        overpass_pois = fetch_overpass_pois(city_name, bbox)
        if overpass_pois:
            break
        wait = (attempt + 1) * 15  # 15s, 30s, 45s backoff
        logger.warning(f"  Retrying in {wait}s (attempt {attempt+1}/3)...")
        time.sleep(wait)
    time.sleep(5)  # be polite to Overpass

    # Fetch from OpenTripMap (optional, adds popularity scores)
    otm_pois = fetch_opentripmap_pois(lat, lon, OTM_KEY)
    if otm_pois:
        logger.info(f"  → {len(otm_pois)} POIs from OpenTripMap")
        time.sleep(1)

    # Merge all sources
    all_pois = overpass_pois + otm_pois

    # Enrich OpenTripMap scores into Overpass results
    for op_poi in overpass_pois:
        for otm_poi in otm_pois:
            dist = haversine_km(op_poi["lat"], op_poi["lon"], otm_poi["lat"], otm_poi["lon"])
            if dist < 0.1:
                op_poi["priority_score"] = otm_poi.get("priority_score", 5.0)
                break

    # Deduplicate
    unique_pois = deduplicate(all_pois, radius_km=0.05)
    logger.info(f"  → {len(unique_pois)} unique POIs after deduplication")

    # Filter out very low quality entries
    # Keep only POIs with a real name
    unique_pois = [p for p in unique_pois if len(p.get("name", "")) >= 4]

    # Build rows
    rows = [build_row(poi, city_name) for poi in unique_pois]
    df = pd.DataFrame(rows)
    logger.info(f"  → {len(df)} rows ready for {city_name}")
    return df


def main():
    logger.info("SmartTrip AI - Spain Dataset Builder")
    logger.info(f"Excel path: {EXCEL_PATH}")
    logger.info(f"OTM API key: {'set' if OTM_KEY else 'not set (using Overpass only)'}")

    if not EXCEL_PATH.exists():
        logger.error(f"phase1_data.xlsx not found at {EXCEL_PATH}")
        sys.exit(1)

    # Load existing data to preserve it
    try:
        existing_df = pd.read_excel(EXCEL_PATH, sheet_name="Attractions", engine="openpyxl")
        logger.info(f"Existing attractions: {len(existing_df)} rows")
    except Exception as e:
        logger.error(f"Failed to read existing data: {e}")
        sys.exit(1)

    # Collect all city names already in the sheet (don't overwrite them)
    existing_cities = set(str(c).lower() for c in existing_df["city"].unique() if pd.notna(c))
    logger.info(f"Existing cities: {existing_cities}")

    # Build datasets only for NEW cities
    new_dfs = []
    for city_name, city_cfg in CITIES.items():
        if city_name.lower() in existing_cities:
            logger.info(f"Skipping {city_name} (already in dataset)")
            continue
        try:
            df = build_city_dataset(city_name, city_cfg)
            if not df.empty:
                new_dfs.append(df)
        except Exception as e:
            logger.warning(f"Failed to build dataset for {city_name}: {e}")
            continue

    if not new_dfs:
        logger.info("No new cities to add. Dataset is up to date.")
        return

    # Merge with existing
    combined = pd.concat([existing_df] + new_dfs, ignore_index=True)
    logger.info(f"\nTotal attractions after merge: {len(combined)}")

    # Write back to Excel preserving all other sheets
    with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        combined.to_excel(writer, sheet_name="Attractions", index=False)

    logger.info(f"\nDone! Wrote {len(combined)} rows to {EXCEL_PATH}")
    logger.info("Breakdown by city:")
    for city, count in combined.groupby("city").size().items():
        logger.info(f"  {city}: {count} attractions")


if __name__ == "__main__":
    main()

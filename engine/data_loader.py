"""
SmartTrip AI - Data Loader
Loads all Phase 1 datasets and provides fast lookup interfaces.
"""

import pandas as pd
import json
import math
from typing import Dict, List, Optional, Tuple

DATA_DIR = r"C:\Users\smvk2\OneDrive\Desktop\Trip_optimizer\engine\phase1_data.xlsx"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in km between two coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class DataStore:
    """Central data store for all SmartTrip datasets."""

    def __init__(self):
        self._load_all()

    def _load_all(self):
        # Attractions
        self.attractions_df = pd.read_csv(
            f"{DATA_DIR}/attractions/attractions_database.csv")
        self.attractions_by_id = {}
        for _, row in self.attractions_df.iterrows():
            rec = row.to_dict()
            # Parse peak_hours JSON
            ph = rec.get("peak_hours_json", "[]")
            rec["peak_hours"] = json.loads(ph) if isinstance(ph, str) else []
            # Normalize booleans
            for bf in ("heat_sensitive", "sunset_sensitive"):
                val = rec.get(bf, False)
                rec[bf] = val if isinstance(val, bool) else str(
                    val).strip().lower() in ("true", "1", "yes")
            self.attractions_by_id[rec["id"]] = rec

        # Traffic baseline
        self.traffic_df = pd.read_csv(
            f"{DATA_DIR}/traffic/traffic_baseline.csv")
        # Build lookup: (city_lower, zone, day_type, hour) -> index
        self._traffic_lookup: Dict[Tuple, float] = {}
        for _, row in self.traffic_df.iterrows():
            key = (row["city"].lower(), row["zone"],
                   row["day_type"], int(row["hour"]))
            self._traffic_lookup[key] = float(row["avg_traffic_index"])

        # Weather baseline
        self.weather_df = pd.read_csv(
            f"{DATA_DIR}/weather/weather_baseline.csv")
        self._weather_lookup: Dict[Tuple, dict] = {}
        for _, row in self.weather_df.iterrows():
            key = (row["city"].lower(), int(row["month"]), int(row["hour"]))
            self._weather_lookup[key] = {
                "temperature": float(row["avg_temperature_c"]),
                "heat_discomfort": float(row["heat_discomfort_index"]),
            }

        # Zone definitions
        self.zones_df = pd.read_csv(f"{DATA_DIR}/traffic/zone_definitions.csv")
        self._zones: Dict[str, List[dict]] = {}
        for _, row in self.zones_df.iterrows():
            city = row["city"].lower()
            if city not in self._zones:
                self._zones[city] = []
            self._zones[city].append({
                "zone": row["zone"],
                "lat": float(row["center_latitude"]),
                "lon": float(row["center_longitude"]),
                "radius_km": float(row["radius_km"]),
            })

        # Event venues
        self.venues_df = pd.read_csv(f"{DATA_DIR}/events/major_venues.csv")
        self.venues = []
        for _, row in self.venues_df.iterrows():
            v = row.to_dict()
            v["event_types"] = json.loads(v["event_types"]) if isinstance(
                v["event_types"], str) else []
            v["typical_event_days"] = json.loads(v["typical_event_days"]) if isinstance(
                v["typical_event_days"], str) else []
            self.venues.append(v)

        # Seasonal adjustments
        try:
            self.seasonal_df = pd.read_csv(
                f"{DATA_DIR}/traffic/seasonal_adjustments.csv")
            self._seasonal = {int(r["month"]): float(
                r["seasonal_multiplier"]) for _, r in self.seasonal_df.iterrows()}
        except FileNotFoundError:
            self._seasonal = {m: 1.0 for m in range(1, 13)}

    # ── Lookup Methods ──────────────────────────────────────

    def get_attraction(self, attraction_id: str) -> Optional[dict]:
        return self.attractions_by_id.get(attraction_id)

    def get_attractions_by_city(self, city: str) -> List[dict]:
        city_lower = city.lower()
        return [a for a in self.attractions_by_id.values() if a["city"].lower() == city_lower]

    def get_traffic_index(self, city: str, zone: str, day_type: str, hour: int) -> float:
        """Get traffic congestion index (0-1) for given conditions."""
        key = (city.lower(), zone, day_type, hour % 24)
        val = self._traffic_lookup.get(key)
        if val is not None:
            return val
        # Fallback: try without exact zone match → use Central
        fallback_key = (city.lower(), "Central", day_type, hour % 24)
        return self._traffic_lookup.get(fallback_key, 0.3)

    def get_weather(self, city: str, month: int, hour: int) -> dict:
        """Get temperature and heat discomfort for given conditions."""
        key = (city.lower(), month, hour % 24)
        return self._weather_lookup.get(key, {"temperature": 20.0, "heat_discomfort": 0.0})

    def get_seasonal_multiplier(self, month: int) -> float:
        return self._seasonal.get(month, 1.0)

    def get_zone_for_coords(self, city: str, lat: float, lon: float) -> str:
        """Determine which traffic zone a coordinate falls in."""
        city_lower = city.lower()
        zones = self._zones.get(city_lower, [])
        best_zone = "Central"
        best_dist = float("inf")
        for z in zones:
            dist = haversine_km(lat, lon, z["lat"], z["lon"])
            if dist < z["radius_km"] and dist < best_dist:
                best_dist = dist
                best_zone = z["zone"]
        return best_zone

    def get_event_congestion_multiplier(self, city: str, zone: str, day_name: str) -> float:
        """Check if any venue event might affect this zone on this day."""
        city_lower = city.lower()
        max_mult = 1.0
        for v in self.venues:
            if v["city"].lower() == city_lower and v["affected_zone"] == zone:
                if day_name in v["typical_event_days"]:
                    max_mult = max(max_mult, float(v["congestion_multiplier"]))
        return max_mult


# Singleton instance
_store: Optional[DataStore] = None


def get_data_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store

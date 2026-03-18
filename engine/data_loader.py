"""
SmartTrip AI - Data Loader
Loads all datasets from the Excel workbook and provides fast lookup interfaces.
Integrates live API clients for weather/routing with Excel fallback.

Sheet layout in phase1_data.xlsx:
    Attractions        — attraction metadata for all Spain cities
    Traffic Baseline   — hourly congestion index per city/zone, weekday + weekend_multiplier
    Weather Baseline   — hourly temperature and heat discomfort per city/month
    Zone Definitions   — geographic traffic zone boundaries
    Event Venues       — major venues and their congestion impact
    Seasonal Adjustments — monthly traffic multiplier
"""

import os
import pathlib
import pandas as pd
import json
import math
from typing import Dict, List, Optional, Tuple

# Load .env early so API keys are available when DataStore initialises
try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv not installed yet — keys may still come from environment

# Path to the Excel workbook — relative to this file for portability
EXCEL_PATH = pathlib.Path(__file__).parent / "phase1_data.xlsx"

# Sheet names in the workbook
SHEET_ATTRACTIONS    = "Attractions"
SHEET_TRAFFIC        = "Traffic Baseline"
SHEET_WEATHER        = "Weather Baseline"
SHEET_ZONES          = "Zone Definitions"
SHEET_VENUES         = "Event Venues"
SHEET_SEASONAL       = "Seasonal Adjustments"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate straight-line distance in km between two coordinates."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_sheet_names() -> List[str]:
    """Return all sheet names in the Excel workbook (useful for debugging)."""
    xl = pd.ExcelFile(EXCEL_PATH, engine="openpyxl")
    return xl.sheet_names


class DataStore:
    """Central data store for all SmartTrip datasets. Reads from Excel on init."""

    def __init__(self):
        self._load_all()
        self._api_clients = self._init_api_clients()

    def _load_all(self):
        # ── Attractions ──────────────────────────────────────────────────────
        self.attractions_df = pd.read_excel(
            EXCEL_PATH, sheet_name=SHEET_ATTRACTIONS, engine="openpyxl"
        )
        self.attractions_by_id: Dict[str, dict] = {}
        for _, row in self.attractions_df.iterrows():
            rec = row.to_dict()
            # Parse peak_hours JSON string → list
            ph = rec.get("peak_hours_json", "[]")
            try:
                rec["peak_hours"] = json.loads(ph) if isinstance(ph, str) else []
            except (json.JSONDecodeError, TypeError):
                rec["peak_hours"] = []
            # Normalise boolean columns
            for bf in ("heat_sensitive", "sunset_sensitive"):
                val = rec.get(bf, False)
                if isinstance(val, bool):
                    rec[bf] = val
                else:
                    rec[bf] = str(val).strip().lower() in ("true", "1", "yes")
            self.attractions_by_id[str(rec["id"])] = rec

        # ── Traffic Baseline ─────────────────────────────────────────────────
        self.traffic_df = pd.read_excel(
            EXCEL_PATH, sheet_name=SHEET_TRAFFIC, engine="openpyxl"
        )
        self._traffic_lookup: Dict[Tuple, Dict[str, float]] = {}
        for _, row in self.traffic_df.iterrows():
            weekday_val = float(row["avg_traffic_index"])
            mult = float(row.get("weekend_multiplier", 1.0))
            weekend_val = round(min(weekday_val * mult, 1.0), 3)
            key = (str(row["city"]).lower(), str(row["zone"]), int(row["hour"]))
            self._traffic_lookup[key] = {
                "weekday": round(weekday_val, 3),
                "weekend": weekend_val,
            }

        # ── Weather Baseline ─────────────────────────────────────────────────
        self.weather_df = pd.read_excel(
            EXCEL_PATH, sheet_name=SHEET_WEATHER, engine="openpyxl"
        )
        self._weather_lookup: Dict[Tuple, dict] = {}
        for _, row in self.weather_df.iterrows():
            key = (str(row["city"]).lower(), int(row["month"]), int(row["hour"]))
            self._weather_lookup[key] = {
                "temperature": float(row["avg_temperature_c"]),
                "heat_discomfort": float(row["heat_discomfort_index"]),
            }

        # ── Zone Definitions ─────────────────────────────────────────────────
        self.zones_df = pd.read_excel(
            EXCEL_PATH, sheet_name=SHEET_ZONES, engine="openpyxl"
        )
        self._zones: Dict[str, List[dict]] = {}
        for _, row in self.zones_df.iterrows():
            city = str(row["city"]).lower()
            if city not in self._zones:
                self._zones[city] = []
            self._zones[city].append({
                "zone": row["zone"],
                "lat": float(row["center_latitude"]),
                "lon": float(row["center_longitude"]),
                "radius_km": float(row["radius_km"]),
            })

        # ── Event Venues ─────────────────────────────────────────────────────
        self.venues_df = pd.read_excel(
            EXCEL_PATH, sheet_name=SHEET_VENUES, engine="openpyxl"
        )
        if "venue_name" in self.venues_df.columns:
            self.venues_df = self.venues_df.rename(columns={"venue_name": "name"})

        self.venues: List[dict] = []
        for _, row in self.venues_df.iterrows():
            v = row.to_dict()
            for json_col in ("event_types", "typical_event_days"):
                raw = v.get(json_col, "[]")
                try:
                    v[json_col] = json.loads(raw) if isinstance(raw, str) else []
                except (json.JSONDecodeError, TypeError):
                    v[json_col] = []
            self.venues.append(v)

        # ── Seasonal Adjustments ─────────────────────────────────────────────
        try:
            self.seasonal_df = pd.read_excel(
                EXCEL_PATH, sheet_name=SHEET_SEASONAL, engine="openpyxl"
            )
            self._seasonal = {
                int(r["month"]): float(r["seasonal_multiplier"])
                for _, r in self.seasonal_df.iterrows()
            }
        except Exception:
            self._seasonal = {m: 1.0 for m in range(1, 13)}

    def _init_api_clients(self) -> dict:
        """Initialise external API clients from environment variables."""
        try:
            from engine.api_clients import init_api_clients
            return init_api_clients()
        except Exception:
            return {}

    # ── Lookup Methods ───────────────────────────────────────────────────────

    def get_attraction(self, attraction_id: str) -> Optional[dict]:
        return self.attractions_by_id.get(str(attraction_id))

    def get_attractions_by_city(self, city: str) -> List[dict]:
        city_lower = city.lower()
        return [a for a in self.attractions_by_id.values()
                if str(a.get("city", "")).lower() == city_lower]

    def get_supported_cities(self) -> List[str]:
        """Return all cities that have attractions in the dataset."""
        cities = set()
        for a in self.attractions_by_id.values():
            city = str(a.get("city", "")).strip()
            if city:
                cities.add(city)
        return sorted(cities)

    def get_traffic_index(self, city: str, zone: str, day_type: str, hour: int) -> float:
        """Return traffic congestion index (0–1) for given conditions."""
        key = (city.lower(), zone, hour % 24)
        entry = self._traffic_lookup.get(key)
        if entry is not None:
            return entry.get(day_type, entry["weekday"])
        # Fallback: try Central zone for the same city/hour
        fallback_key = (city.lower(), "Central", hour % 24)
        entry = self._traffic_lookup.get(fallback_key)
        if entry:
            return entry.get(day_type, entry["weekday"])
        return 0.3  # default moderate congestion

    def get_weather(
        self,
        city: str,
        month: int,
        hour: int,
        date_str: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> dict:
        """
        Return temperature and heat discomfort for given conditions.

        If date_str + lat + lon are provided, tries Open-Meteo live weather first.
        Falls back to Excel baseline.
        """
        # Try live weather via Open-Meteo
        if date_str and lat is not None and lon is not None:
            open_meteo = self._api_clients.get("open_meteo")
            if open_meteo:
                hourly = open_meteo.get_hourly_weather(lat, lon, date_str)
                if hourly and hour in hourly:
                    from engine.api_clients import compute_live_heat_discomfort
                    h_data = hourly[hour]
                    return {
                        "temperature": h_data["temp_c"],
                        "heat_discomfort": compute_live_heat_discomfort(
                            h_data["temp_c"], h_data.get("uv_index", 3.0)
                        ),
                        "source": "open_meteo_live",
                        "humidity": h_data.get("humidity"),
                        "uv_index": h_data.get("uv_index"),
                        "precip_prob": h_data.get("precip_prob"),
                    }

        # Static Excel fallback
        key = (city.lower(), month, hour % 24)
        data = self._weather_lookup.get(
            key, {"temperature": 20.0, "heat_discomfort": 0.0}
        )
        return {**data, "source": "static"}

    def get_weather_full_day(
        self,
        city: str,
        month: int,
        date_str: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> List[dict]:
        """Return 24-hour weather profile."""
        return [
            {"hour": h, **self.get_weather(city, month, h, date_str, lat, lon)}
            for h in range(24)
        ]

    def get_seasonal_multiplier(self, month: int) -> float:
        return self._seasonal.get(month, 1.0)

    def get_zone_for_coords(self, city: str, lat: float, lon: float) -> str:
        """Map a coordinate pair to the nearest traffic zone for that city."""
        zones = self._zones.get(city.lower(), [])
        best_zone = "Central"
        best_dist = float("inf")
        for z in zones:
            dist = haversine_km(lat, lon, z["lat"], z["lon"])
            if dist < z["radius_km"] and dist < best_dist:
                best_dist = dist
                best_zone = z["zone"]
        return best_zone

    def get_event_congestion_multiplier(
        self, city: str, zone: str, day_name: str
    ) -> float:
        """Return the maximum congestion multiplier from any venue event active today."""
        city_lower = city.lower()
        max_mult = 1.0
        for v in self.venues:
            if (str(v.get("city", "")).lower() == city_lower
                    and v.get("affected_zone") == zone
                    and day_name in v.get("typical_event_days", [])):
                max_mult = max(max_mult, float(v.get("congestion_multiplier", 1.0)))
        return max_mult


# ── Singleton ────────────────────────────────────────────────────────────────

_store: Optional[DataStore] = None


def get_data_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store


def reset_data_store() -> None:
    """Force a fresh load — useful in tests."""
    global _store
    _store = None

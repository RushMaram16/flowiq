"""
SmartTrip AI - External API Clients
Wrappers for all third-party APIs with automatic fallback on failure.

APIs integrated:
    Open-Meteo        — live weather, no key required
    OpenRouteService  — real road routing (ORS_API_KEY)
    TomTom Traffic    — real-time traffic flow (TOMTOM_API_KEY)
    Overpass          — OpenStreetMap POI queries, no key required
    Nominatim         — geocoding, no key required

All methods return None on failure — callers fall back to static Excel data.
"""

import os
import time
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default request timeout (seconds)
_TIMEOUT = 8


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class APIHealthTracker:
    """
    Simple circuit breaker. After 3 failures within 5 minutes, skips the API
    entirely until the backoff window expires — prevents cascade timeouts during
    the optimizer's permutation loop.
    """
    FAILURE_THRESHOLD = 3
    BACKOFF_SECONDS = 300  # 5 minutes

    def __init__(self):
        self._failures: dict = {}
        self._last_failure: dict = {}

    def record_failure(self, api_name: str):
        self._failures[api_name] = self._failures.get(api_name, 0) + 1
        self._last_failure[api_name] = time.time()

    def record_success(self, api_name: str):
        self._failures[api_name] = 0

    def is_available(self, api_name: str) -> bool:
        failures = self._failures.get(api_name, 0)
        if failures < self.FAILURE_THRESHOLD:
            return True
        last = self._last_failure.get(api_name, 0)
        if time.time() - last > self.BACKOFF_SECONDS:
            self._failures[api_name] = 0  # reset after backoff
            return True
        return False


# Global health tracker shared across all clients
_health = APIHealthTracker()


# ── Open-Meteo Weather Client ─────────────────────────────────────────────────

class OpenMeteoClient:
    """
    Free weather API — no API key required.
    Returns hourly temperature, humidity, UV index, precipitation probability.
    API: https://open-meteo.com
    """
    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    API_NAME = "open_meteo"
    _CACHE_TTL = 6 * 3600  # 6 hours in seconds

    def __init__(self):
        self._cache: dict = {}  # key → (result, timestamp)

    def get_hourly_weather(
        self, lat: float, lon: float, date_str: str
    ) -> Optional[dict]:
        """
        Fetch hourly weather for a specific date.

        Args:
            lat, lon: Coordinates
            date_str: ISO date string "YYYY-MM-DD"

        Returns:
            Dict {hour: {temp_c, humidity, uv_index, precip_prob}} or None
        """
        if not _health.is_available(self.API_NAME):
            return None

        # Open-Meteo only supports 16-day forecast — skip for historical/far-future dates
        try:
            import datetime as _dt
            req_date = _dt.date.fromisoformat(date_str)
            today = _dt.date.today()
            days_diff = (req_date - today).days
            if days_diff < -1 or days_diff > 16:
                return None  # Outside forecast window — use static fallback
        except (ValueError, TypeError):
            return None

        # In-memory cache: same (lat, lon, date) reused within 6 hours
        cache_key = (round(lat, 3), round(lon, 3), date_str)
        cached_entry = self._cache.get(cache_key)
        if cached_entry:
            result, ts = cached_entry
            if time.time() - ts < self._CACHE_TTL:
                return result

        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,relativehumidity_2m,uv_index,precipitation_probability",
                "start_date": date_str,
                "end_date": date_str,
                "timezone": "Europe/Madrid",
            }
            resp = requests.get(self.BASE_URL, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            hourly = data.get("hourly", {})
            times = hourly.get("time", [])
            temps = hourly.get("temperature_2m", [])
            humidity = hourly.get("relativehumidity_2m", [])
            uv = hourly.get("uv_index", [])
            precip = hourly.get("precipitation_probability", [])

            result = {}
            for i, t in enumerate(times):
                hour = int(t.split("T")[1].split(":")[0])
                result[hour] = {
                    "temp_c": temps[i] if i < len(temps) else 20.0,
                    "humidity": humidity[i] if i < len(humidity) else 50,
                    "uv_index": uv[i] if i < len(uv) else 3.0,
                    "precip_prob": precip[i] if i < len(precip) else 0,
                }

            _health.record_success(self.API_NAME)
            self._cache[cache_key] = (result, time.time())
            return result

        except Exception as e:
            logger.warning(f"Open-Meteo error: {e}")
            _health.record_failure(self.API_NAME)
            return None


# ── OpenRouteService Client ───────────────────────────────────────────────────

class OpenRouteServiceClient:
    """
    Real road routing via OpenRouteService.
    Free tier: 500 requests/day.
    API: https://openrouteservice.org
    """
    BASE_URL = "https://api.openrouteservice.org/v2"
    API_NAME = "ors"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }

    def get_driving_time(
        self, origin: tuple, dest: tuple
    ) -> Optional[dict]:
        """
        Get driving time and distance between two points.

        Args:
            origin: (lat, lon) tuple
            dest: (lat, lon) tuple

        Returns:
            {duration_minutes, distance_km} or None
        """
        return self._get_route(origin, dest, profile="driving-car")

    def get_walking_time(
        self, origin: tuple, dest: tuple
    ) -> Optional[dict]:
        """Get walking time between two points."""
        return self._get_route(origin, dest, profile="foot-walking")

    def _get_route(
        self, origin: tuple, dest: tuple, profile: str
    ) -> Optional[dict]:
        if not _health.is_available(self.API_NAME):
            return None
        try:
            # ORS uses [lon, lat] order
            body = {
                "coordinates": [
                    [origin[1], origin[0]],
                    [dest[1], dest[0]],
                ],
                "units": "km",
            }
            url = f"{self.BASE_URL}/directions/{profile}"
            resp = requests.post(url, json=body, headers=self._headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            summary = data["routes"][0]["summary"]
            _health.record_success(self.API_NAME)
            return {
                "duration_minutes": round(summary["duration"] / 60, 1),
                "distance_km": round(summary["distance"], 2),
            }
        except Exception as e:
            logger.warning(f"ORS routing error: {e}")
            _health.record_failure(self.API_NAME)
            return None

    def get_matrix(
        self, locations: list, profile: str = "driving-car"
    ) -> Optional[list]:
        """
        Compute NxN travel time matrix in a single API call.

        Args:
            locations: list of dicts with 'latitude' and 'longitude'
            profile: "driving-car" or "foot-walking"

        Returns:
            2D list of durations in minutes, or None on failure
        """
        if not _health.is_available(self.API_NAME):
            return None
        try:
            # ORS uses [lon, lat] order
            coords = [[loc["longitude"], loc["latitude"]] for loc in locations]
            body = {
                "locations": coords,
                "metrics": ["duration", "distance"],
                "units": "km",
            }
            url = f"{self.BASE_URL}/matrix/{profile}"
            resp = requests.post(url, json=body, headers=self._headers, timeout=_TIMEOUT * 2)
            resp.raise_for_status()
            data = resp.json()

            raw_durations = data.get("durations", [])
            raw_distances = data.get("distances", [])

            # Convert seconds → minutes, meters → km
            n = len(raw_durations)
            matrix_time = [[0.0] * n for _ in range(n)]
            matrix_dist = [[0.0] * n for _ in range(n)]

            for i in range(n):
                for j in range(n):
                    matrix_time[i][j] = round(raw_durations[i][j] / 60, 1)
                    if raw_distances:
                        matrix_dist[i][j] = round(raw_distances[i][j], 2)

            _health.record_success(self.API_NAME)
            return {"time": matrix_time, "distance": matrix_dist}

        except Exception as e:
            logger.warning(f"ORS matrix error: {e}")
            _health.record_failure(self.API_NAME)
            return None


# ── TomTom Traffic Client ─────────────────────────────────────────────────────

class TomTomTrafficClient:
    """
    Real-time traffic flow data from TomTom.
    Free tier: 2500 requests/day.
    API: https://developer.tomtom.com/traffic-api
    """
    BASE_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData"
    API_NAME = "tomtom"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_flow_segment(self, lat: float, lon: float) -> Optional[dict]:
        """
        Get current traffic flow at a point.

        Returns:
            {current_speed_kmh, free_flow_speed_kmh, confidence, traffic_index}
            traffic_index = 1 - (current / free_flow), clipped to [0, 1]
        """
        if not _health.is_available(self.API_NAME):
            return None
        try:
            url = f"{self.BASE_URL}/absolute/10/json"
            params = {
                "point": f"{lat},{lon}",
                "key": self.api_key,
            }
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            flow = data.get("flowSegmentData", {})
            current = float(flow.get("currentSpeed", 0))
            free_flow = float(flow.get("freeFlowSpeed", 1))
            confidence = float(flow.get("confidence", 0))

            # traffic_index: 0 = free flow, 1 = fully congested
            if free_flow > 0:
                traffic_index = round(max(0, min(1 - current / free_flow, 1.0)), 3)
            else:
                traffic_index = 0.3  # default moderate

            _health.record_success(self.API_NAME)
            return {
                "current_speed_kmh": round(current, 1),
                "free_flow_speed_kmh": round(free_flow, 1),
                "confidence": round(confidence, 2),
                "traffic_index": traffic_index,
            }
        except Exception as e:
            logger.warning(f"TomTom traffic error: {e}")
            _health.record_failure(self.API_NAME)
            return None


# ── Overpass (OpenStreetMap) Client ───────────────────────────────────────────

class OverpassClient:
    """
    Free OpenStreetMap data via Overpass API.
    No API key required. Used for live POI enrichment and dataset building.
    """
    BASE_URL = "https://overpass-api.de/api/interpreter"
    API_NAME = "overpass"

    def get_pois_near(
        self,
        lat: float,
        lon: float,
        radius_m: int = 5000,
        tags: Optional[list] = None,
    ) -> Optional[list]:
        """
        Query POIs near a point.

        Args:
            lat, lon: Center coordinates
            radius_m: Search radius in metres
            tags: List of OSM tag filters, e.g. ['tourism', 'historic', 'leisure']

        Returns:
            List of {name, lat, lon, tags} dicts or None
        """
        if not _health.is_available(self.API_NAME):
            return None

        if tags is None:
            tags = ["tourism", "historic", "leisure"]

        # Build Overpass QL query
        tag_filters = "\n".join(
            f'  node["{t}"](around:{radius_m},{lat},{lon});' for t in tags
        )
        query = f"""
[out:json][timeout:30];
(
{tag_filters}
);
out body;
"""
        try:
            resp = requests.post(
                self.BASE_URL,
                data=query,
                timeout=35,
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])

            results = []
            for el in elements:
                name = el.get("tags", {}).get("name") or el.get("tags", {}).get("name:en")
                if not name:
                    continue
                results.append({
                    "name": name,
                    "lat": el.get("lat", lat),
                    "lon": el.get("lon", lon),
                    "osm_tags": el.get("tags", {}),
                    "osm_id": el.get("id"),
                })

            _health.record_success(self.API_NAME)
            return results

        except Exception as e:
            logger.warning(f"Overpass API error: {e}")
            _health.record_failure(self.API_NAME)
            return None

    def get_pois_in_bbox(
        self,
        south: float,
        west: float,
        north: float,
        east: float,
        tags: Optional[list] = None,
    ) -> Optional[list]:
        """Query POIs within a bounding box — used by build_dataset.py."""
        if not _health.is_available(self.API_NAME):
            return None

        if tags is None:
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
            resp = requests.post(
                self.BASE_URL,
                data=query,
                timeout=65,
                headers={"Content-Type": "text/plain"},
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])

            results = []
            for el in elements:
                name = el.get("tags", {}).get("name") or el.get("tags", {}).get("name:en")
                if not name:
                    continue
                results.append({
                    "name": name,
                    "lat": float(el.get("lat", 0)),
                    "lon": float(el.get("lon", 0)),
                    "osm_tags": el.get("tags", {}),
                    "osm_id": el.get("id"),
                })

            _health.record_success(self.API_NAME)
            return results

        except Exception as e:
            logger.warning(f"Overpass bbox error: {e}")
            _health.record_failure(self.API_NAME)
            return None


# ── Nominatim Geocoding Client ────────────────────────────────────────────────

class NominatimClient:
    """
    Free geocoding via Nominatim (OpenStreetMap).
    No API key required. Enforce 1 req/sec per OSM policy.
    """
    BASE_URL = "https://nominatim.openstreetmap.org"
    API_NAME = "nominatim"
    _last_call: float = 0.0

    def geocode(self, city_name: str, country: str = "Spain") -> Optional[dict]:
        """
        Geocode a city name to coordinates.

        Returns:
            {lat, lon, display_name} or None
        """
        if not _health.is_available(self.API_NAME):
            return None

        # Enforce 1 request/second per Nominatim ToS
        elapsed = time.time() - self._last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        try:
            params = {
                "q": f"{city_name}, {country}",
                "format": "json",
                "limit": 1,
            }
            headers = {"User-Agent": "SmartTrip-AI/1.0 (trip-optimizer)"}
            resp = requests.get(
                f"{self.BASE_URL}/search",
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            self._last_call = time.time()

            results = resp.json()
            if results:
                _health.record_success(self.API_NAME)
                return {
                    "lat": float(results[0]["lat"]),
                    "lon": float(results[0]["lon"]),
                    "display_name": results[0].get("display_name", city_name),
                }
        except Exception as e:
            logger.warning(f"Nominatim error for '{city_name}': {e}")
            _health.record_failure(self.API_NAME)

        return None


# ── OpenTripMap Client ────────────────────────────────────────────────────────

class OpenTripMapClient:
    """
    POI database for dataset building (build_dataset.py).
    Free tier with API key.
    API: https://opentripmap.io/product
    """
    BASE_URL = "https://api.opentripmap.com/0.1/en"
    API_NAME = "opentripmap"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_pois_radius(
        self,
        lat: float,
        lon: float,
        radius_m: int = 10000,
        kinds: str = "interesting_places",
        rate: int = 2,
        limit: int = 100,
    ) -> Optional[list]:
        """
        Get top-rated POIs near coordinates.

        Args:
            rate: Minimum rating (0-3). 2 = well-known, 3 = world-famous

        Returns:
            List of {name, lat, lon, kinds, rate, wikidata} dicts
        """
        if not _health.is_available(self.API_NAME):
            return None
        try:
            params = {
                "radius": radius_m,
                "lon": lon,
                "lat": lat,
                "kinds": kinds,
                "rate": rate,
                "format": "json",
                "limit": limit,
                "apikey": self.api_key,
            }
            resp = requests.get(
                f"{self.BASE_URL}/places/radius",
                params=params,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            features = resp.json()

            results = []
            for f in features:
                props = f.get("properties", {})
                name = props.get("name") or props.get("name_en", "")
                if not name:
                    continue
                geo = f.get("geometry", {}).get("coordinates", [lon, lat])
                results.append({
                    "name": name,
                    "lat": geo[1],
                    "lon": geo[0],
                    "kinds": props.get("kinds", ""),
                    "rate": props.get("rate", 0),
                    "wikidata": props.get("wikidata", ""),
                    "xid": props.get("xid", ""),
                })

            _health.record_success(self.API_NAME)
            return results

        except Exception as e:
            logger.warning(f"OpenTripMap error: {e}")
            _health.record_failure(self.API_NAME)
            return None


# ── Utility: heat discomfort from live weather ────────────────────────────────

def compute_live_heat_discomfort(temp_c: float, uv_index: float) -> float:
    """
    Compute a heat discomfort index (0-1) from real temperature and UV index.
    Replicates the logic used when building the Excel Weather Baseline sheet.
    """
    # Temperature component: starts at 25°C, maxes out at 45°C+
    if temp_c <= 18:
        temp_component = 0.0
    elif temp_c <= 25:
        temp_component = (temp_c - 18) / 35.0
    elif temp_c <= 35:
        temp_component = 0.2 + (temp_c - 25) / 25.0
    else:
        temp_component = 0.6 + min((temp_c - 35) / 25.0, 0.4)

    # UV component: 0 = no sun, 11+ = extreme
    uv_component = min(uv_index / 11.0, 1.0) * 0.3

    return round(min(temp_component + uv_component, 1.0), 3)


# ── Factory: initialise clients from environment ──────────────────────────────

def init_api_clients() -> dict:
    """
    Read environment variables and return a dict of available API clients.
    Always includes open_meteo (no key needed).
    Others only created if their key is set.
    """
    clients = {
        "open_meteo": OpenMeteoClient(),
        "overpass": OverpassClient(),
        "nominatim": NominatimClient(),
    }

    ors_key = os.getenv("ORS_API_KEY", "").strip()
    if ors_key and ors_key != "your_ors_key_here":
        clients["ors"] = OpenRouteServiceClient(ors_key)
        logger.info("OpenRouteService client initialised")

    tomtom_key = os.getenv("TOMTOM_API_KEY", "").strip()
    if tomtom_key and tomtom_key != "your_tomtom_key_here":
        clients["tomtom"] = TomTomTrafficClient(tomtom_key)
        logger.info("TomTom Traffic client initialised")

    otm_key = os.getenv("OPENTRIPMAP_API_KEY", "").strip()
    if otm_key and otm_key != "your_opentripmap_key_here":
        clients["opentripmap"] = OpenTripMapClient(otm_key)
        logger.info("OpenTripMap client initialised")

    return clients

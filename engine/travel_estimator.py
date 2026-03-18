"""
SmartTrip AI - Travel Time Estimator
Estimates travel time between coordinates.

Priority order:
    1. OpenRouteService (real road routing, if ORS_API_KEY set)
       + TomTom live traffic multiplier (if TOMTOM_API_KEY set)
    2. Haversine + city-specific speed model (static fallback, always available)

Both driving and walking modes are supported.
"""

import math
from typing import Tuple, Optional
from engine.data_loader import get_data_store, haversine_km


# ── Speed / Detour Constants ──────────────────────────────────────────────────

# Average driving speeds in km/h by city and congestion level
BASE_SPEEDS = {
    # Original 3 cities
    "madrid":        {"free_flow": 38, "congested": 12, "avg": 25},
    "barcelona":     {"free_flow": 35, "congested": 10, "avg": 22},
    "seville":       {"free_flow": 40, "congested": 15, "avg": 28},
    # New cities
    "valencia":      {"free_flow": 37, "congested": 13, "avg": 24},
    "bilbao":        {"free_flow": 36, "congested": 13, "avg": 24},
    "granada":       {"free_flow": 32, "congested": 10, "avg": 21},
    "malaga":        {"free_flow": 38, "congested": 13, "avg": 25},
    "toledo":        {"free_flow": 28, "congested": 8,  "avg": 18},  # compact medieval
    "salamanca":     {"free_flow": 32, "congested": 10, "avg": 21},
    "san sebastian": {"free_flow": 33, "congested": 11, "avg": 22},
    "cordoba":       {"free_flow": 30, "congested": 9,  "avg": 19},  # old town streets
    "palma":         {"free_flow": 36, "congested": 12, "avg": 23},
    "zaragoza":      {"free_flow": 38, "congested": 13, "avg": 25},
}

# Road distance multiplier over straight-line (detour factor)
DETOUR_FACTORS = {
    "madrid":        1.35,
    "barcelona":     1.40,   # grid + one-ways
    "seville":       1.45,   # narrow old-town streets
    "valencia":      1.38,
    "bilbao":        1.42,
    "granada":       1.48,   # steep hillside streets
    "malaga":        1.38,
    "toledo":        1.55,   # very compact medieval layout
    "salamanca":     1.40,
    "san sebastian": 1.38,
    "cordoba":       1.52,   # medina-style old town
    "palma":         1.38,
    "zaragoza":      1.35,
}

# Walking speed (km/h) — uniform across cities
WALKING_SPEED_KMH = 4.5


def estimate_travel_time(
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
    city: str,
    hour: int,
    day_type: str = "weekday",
    month: int = 6,
    travel_mode: str = "driving",
    precomputed: Optional[dict] = None,
) -> dict:
    """
    Estimate travel time between two points.

    Args:
        precomputed: If provided, use these pre-fetched values directly
                     (from ORS matrix call in optimizer).

    Returns dict with:
        distance_km, duration_minutes, traffic_index,
        speed_kmh, free_flow_minutes, origin_zone, dest_zone, source
    """
    # Use pre-fetched matrix values if available (optimizer passes these in)
    if precomputed is not None:
        return precomputed

    store = get_data_store()
    city_lower = city.lower()

    # ── Walking mode: simple Haversine, no traffic ────────────────────────────
    if travel_mode == "walking":
        return _walking_estimate(origin_lat, origin_lon, dest_lat, dest_lon, city_lower, store)

    # ── Driving: try live ORS + TomTom first, fallback to Haversine ──────────
    clients = getattr(store, "_api_clients", {})
    ors = clients.get("ors")

    if ors:
        live = ors.get_driving_time(
            (origin_lat, origin_lon), (dest_lat, dest_lon)
        )
        if live:
            # Apply real-time TomTom traffic multiplier if available
            tomtom = clients.get("tomtom")
            traffic_index = _static_traffic_index(store, city_lower, origin_lat, origin_lon,
                                                   dest_lat, dest_lon, day_type, hour, month)
            if tomtom:
                mid_lat = (origin_lat + dest_lat) / 2
                mid_lon = (origin_lon + dest_lon) / 2
                flow = tomtom.get_flow_segment(mid_lat, mid_lon)
                if flow:
                    traffic_index = flow["traffic_index"]

            # Apply traffic multiplier: at index=0 → 1.0x, at index=1 → 2.5x
            traffic_mult = 1.0 + traffic_index * 1.5
            duration = round(live["duration_minutes"] * traffic_mult, 1)

            origin_zone = store.get_zone_for_coords(city, origin_lat, origin_lon)
            dest_zone = store.get_zone_for_coords(city, dest_lat, dest_lon)

            return {
                "distance_km": live["distance_km"],
                "duration_minutes": duration,
                "traffic_index": round(traffic_index, 3),
                "speed_kmh": round(live["distance_km"] / max(duration / 60, 0.01), 1),
                "free_flow_minutes": live["duration_minutes"],
                "origin_zone": origin_zone,
                "dest_zone": dest_zone,
                "source": "ors_live",
            }

    # ── Static Haversine fallback ─────────────────────────────────────────────
    return _haversine_driving_estimate(
        origin_lat, origin_lon, dest_lat, dest_lon,
        city_lower, hour, day_type, month, store
    )


def _static_traffic_index(
    store, city_lower, origin_lat, origin_lon,
    dest_lat, dest_lon, day_type, hour, month
) -> float:
    """Compute traffic index from static Excel baseline."""
    origin_zone = store.get_zone_for_coords(city_lower, origin_lat, origin_lon)
    dest_zone = store.get_zone_for_coords(city_lower, dest_lat, dest_lon)
    t_origin = store.get_traffic_index(city_lower, origin_zone, day_type, hour)
    t_dest = store.get_traffic_index(city_lower, dest_zone, day_type, hour)
    traffic_index = (t_origin + t_dest) / 2
    seasonal_mult = store.get_seasonal_multiplier(month)
    return round(min(traffic_index * seasonal_mult, 1.0), 3)


def _haversine_driving_estimate(
    origin_lat, origin_lon, dest_lat, dest_lon,
    city_lower, hour, day_type, month, store
) -> dict:
    """Haversine + static traffic model (always available, no API key needed)."""
    straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    detour = DETOUR_FACTORS.get(city_lower, 1.38)
    road_km = max(straight_km * detour, 0.3)

    origin_zone = store.get_zone_for_coords(city_lower, origin_lat, origin_lon)
    dest_zone = store.get_zone_for_coords(city_lower, dest_lat, dest_lon)

    t_origin = store.get_traffic_index(city_lower, origin_zone, day_type, hour)
    t_dest = store.get_traffic_index(city_lower, dest_zone, day_type, hour)
    traffic_index = (t_origin + t_dest) / 2

    seasonal_mult = store.get_seasonal_multiplier(month)
    traffic_index = min(traffic_index * seasonal_mult, 1.0)

    speeds = BASE_SPEEDS.get(city_lower, BASE_SPEEDS["madrid"])
    free_flow = speeds["free_flow"]
    congested = speeds["congested"]

    effective_speed = free_flow - (free_flow - congested) * traffic_index
    effective_speed = max(effective_speed, congested * 0.7)

    duration_hours = road_km / effective_speed
    duration_minutes = round(duration_hours * 60, 1)
    free_flow_minutes = round((road_km / free_flow) * 60, 1)

    return {
        "distance_km": round(road_km, 2),
        "duration_minutes": duration_minutes,
        "traffic_index": round(traffic_index, 3),
        "speed_kmh": round(effective_speed, 1),
        "free_flow_minutes": free_flow_minutes,
        "origin_zone": origin_zone,
        "dest_zone": dest_zone,
        "source": "static",
    }


def _walking_estimate(
    origin_lat, origin_lon, dest_lat, dest_lon, city_lower, store
) -> dict:
    """Walking time estimate (no traffic)."""
    straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
    # Walking has a smaller detour factor (pedestrian shortcuts)
    road_km = max(straight_km * 1.20, 0.1)

    duration_minutes = round((road_km / WALKING_SPEED_KMH) * 60, 1)

    origin_zone = store.get_zone_for_coords(city_lower, origin_lat, origin_lon)
    dest_zone = store.get_zone_for_coords(city_lower, dest_lat, dest_lon)

    return {
        "distance_km": round(road_km, 2),
        "duration_minutes": duration_minutes,
        "traffic_index": 0.0,   # walking ignores traffic
        "speed_kmh": WALKING_SPEED_KMH,
        "free_flow_minutes": duration_minutes,
        "origin_zone": origin_zone,
        "dest_zone": dest_zone,
        "source": "walking_static",
    }


def estimate_travel_matrix(
    locations: list,
    city: str,
    hour: int,
    day_type: str = "weekday",
    month: int = 6,
    travel_mode: str = "driving",
) -> dict:
    """
    Compute travel time + distance matrix for a list of locations.
    Tries ORS /matrix (one API call for full NxN matrix) first,
    falls back to individual Haversine estimates.

    Args:
        locations: list of dicts with 'latitude' and 'longitude'

    Returns:
        {"time": [[minutes]], "distance": [[km]], "source": str}
    """
    store = get_data_store()
    n = len(locations)
    clients = getattr(store, "_api_clients", {})
    ors = clients.get("ors")

    # Try ORS matrix endpoint (1 API call for whole matrix)
    if ors and travel_mode == "driving":
        ors_profile = "driving-car"
        matrix = ors.get_matrix(locations, profile=ors_profile)
        if matrix:
            return {**matrix, "source": "ors_matrix"}

    if ors and travel_mode == "walking":
        matrix = ors.get_matrix(locations, profile="foot-walking")
        if matrix:
            return {**matrix, "source": "ors_matrix_walking"}

    # Fallback: compute individually
    time_matrix = [[0.0] * n for _ in range(n)]
    dist_matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            result = estimate_travel_time(
                locations[i]["latitude"], locations[i]["longitude"],
                locations[j]["latitude"], locations[j]["longitude"],
                city, hour, day_type, month, travel_mode,
            )
            time_matrix[i][j] = result["duration_minutes"]
            dist_matrix[i][j] = result["distance_km"]

    return {"time": time_matrix, "distance": dist_matrix, "source": "static_matrix"}

"""
SmartTrip AI - Travel Time Estimator
Estimates driving time between coordinates using distance + traffic congestion model.
In production, this would call OSRM or Google Routes API.
For MVP, uses haversine distance with city-specific speed adjustments.
"""

import math
from typing import Tuple
from engine.data_loader import get_data_store, haversine_km


# Average driving speeds in km/h by city and congestion level
# Based on TomTom Traffic Index data for Spanish cities
BASE_SPEEDS = {
    "madrid":    {"free_flow": 38, "congested": 12, "avg": 25},
    "barcelona": {"free_flow": 35, "congested": 10, "avg": 22},
    "seville":   {"free_flow": 40, "congested": 15, "avg": 28},
}

# City-center detour factor: straight-line vs actual road distance
# Typical urban detour ratios range from 1.2 to 1.6
DETOUR_FACTORS = {
    "madrid": 1.35,
    "barcelona": 1.40,  # Grid layout but one-way streets
    "seville": 1.45,    # Narrow old-town streets
}


def estimate_travel_time(
    origin_lat: float, origin_lon: float,
    dest_lat: float, dest_lon: float,
    city: str,
    hour: int,
    day_type: str = "weekday",
    month: int = 6,
) -> dict:
    """
    Estimate driving time between two points.

    Returns:
        dict with:
            - distance_km: estimated road distance
            - duration_minutes: estimated travel time
            - traffic_index: congestion level used (0-1)
            - speed_kmh: effective speed used
            - free_flow_minutes: time without traffic
    """
    store = get_data_store()
    city_lower = city.lower()

    # 1. Compute straight-line distance
    straight_km = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)

    # 2. Apply detour factor for road distance
    detour = DETOUR_FACTORS.get(city_lower, 1.35)
    road_km = straight_km * detour

    # Minimum distance (even adjacent attractions have some travel)
    road_km = max(road_km, 0.3)

    # 3. Get traffic congestion for this time
    origin_zone = store.get_zone_for_coords(city, origin_lat, origin_lon)
    dest_zone = store.get_zone_for_coords(city, dest_lat, dest_lon)

    # Use average of origin and destination zone congestion
    traffic_origin = store.get_traffic_index(city, origin_zone, day_type, hour)
    traffic_dest = store.get_traffic_index(city, dest_zone, day_type, hour)
    traffic_index = (traffic_origin + traffic_dest) / 2

    # 4. Apply seasonal adjustment
    seasonal_mult = store.get_seasonal_multiplier(month)
    traffic_index = min(traffic_index * seasonal_mult, 1.0)

    # 5. Compute effective speed
    speeds = BASE_SPEEDS.get(city_lower, BASE_SPEEDS["madrid"])
    free_flow = speeds["free_flow"]
    congested = speeds["congested"]

    # Linear interpolation between free-flow and congested speeds
    effective_speed = free_flow - (free_flow - congested) * traffic_index
    effective_speed = max(effective_speed, congested * 0.7)  # Floor

    # 6. Compute travel time
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
    }


def estimate_travel_matrix(
    locations: list,
    city: str,
    hour: int,
    day_type: str = "weekday",
    month: int = 6,
) -> list:
    """
    Compute travel time matrix for a list of locations.

    Args:
        locations: list of dicts with 'latitude' and 'longitude'

    Returns:
        2D list where matrix[i][j] = travel time in minutes from i to j
    """
    n = len(locations)
    matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            result = estimate_travel_time(
                locations[i]["latitude"], locations[i]["longitude"],
                locations[j]["latitude"], locations[j]["longitude"],
                city, hour, day_type, month
            )
            matrix[i][j] = result["duration_minutes"]

    return matrix

"""
SmartTrip AI - Optimization Engine
The core computational component that simulates all route permutations
and selects the itinerary with the lowest Travel Impact Score.

Architecture:
    Step 1: Pre-fetch ORS travel matrix (1 API call for full NxN)
    Step 2: Generate all permutations of N attractions (N! for 3-5 = 6-120)
    Step 3: For each permutation, simulate the full timeline using pre-fetched matrix
    Step 4: Compute Travel Impact Score for each
    Step 5: Select the route with the lowest total score
"""

import itertools
import time as time_module
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from engine.data_loader import get_data_store
from engine.travel_estimator import estimate_travel_time, estimate_travel_matrix
from engine.impact_score import (
    compute_crowd_factor,
    compute_traffic_volatility,
    compute_heat_impact,
    compute_leg_impact_score,
    compute_itinerary_score,
)

# City center coordinates used for a single weather fetch per optimisation run.
# Using the city center avoids one HTTP call per attraction; weather is uniform within a city.
CITY_WEATHER_COORDS = {
    "madrid":        (40.4168, -3.7038),
    "barcelona":     (41.3851,  2.1734),
    "seville":       (37.3891, -5.9845),
    "valencia":      (39.4699, -0.3763),
    "bilbao":        (43.2630, -2.9350),
    "granada":       (37.1773, -3.5986),
    "malaga":        (36.7213, -4.4213),
    "toledo":        (39.8567, -4.0244),
    "salamanca":     (40.9701, -5.6635),
    "san sebastian": (43.3183, -1.9812),
    "cordoba":       (37.8882, -4.7794),
    "palma":         (39.5696,  2.6502),
    "zaragoza":      (41.6488, -0.8891),
}


# ── Data Classes ─────────────────────────────────────────

class TimeSlot:
    """Represents a time window in the itinerary."""

    def __init__(self, start: datetime, end: datetime):
        self.start = start
        self.end = end

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def __repr__(self):
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


class ItineraryLeg:
    """Represents one segment: travel + visit at destination."""

    def __init__(self):
        self.attraction_id: str = ""
        self.attraction_name: str = ""
        self.travel_from: str = ""
        self.travel_start: datetime = None
        self.travel_end: datetime = None
        self.travel_duration_min: float = 0
        self.travel_distance_km: float = 0
        self.visit_start: datetime = None
        self.visit_end: datetime = None
        self.visit_duration_min: float = 0
        self.impact_score: dict = {}
        self.travel_details: dict = {}

    def to_dict(self) -> dict:
        return {
            "attraction_id": self.attraction_id,
            "attraction_name": self.attraction_name,
            "travel_from": self.travel_from,
            "departure_time": self.travel_start.strftime("%H:%M") if self.travel_start else None,
            "arrival_time": self.travel_end.strftime("%H:%M") if self.travel_end else None,
            "travel_duration_min": round(self.travel_duration_min, 1),
            "travel_distance_km": round(self.travel_distance_km, 2),
            "visit_start": self.visit_start.strftime("%H:%M") if self.visit_start else None,
            "visit_end": self.visit_end.strftime("%H:%M") if self.visit_end else None,
            "visit_duration_min": round(self.visit_duration_min),
            "impact_score": self.impact_score,
        }


class OptimizationResult:
    """Complete result from the optimizer."""

    def __init__(self):
        self.ordered_route: List[dict] = []
        self.timeline: List[dict] = []
        self.total_travel_time: float = 0
        self.total_visit_time: float = 0
        self.total_impact_score: float = 0
        self.impact_breakdown: dict = {}
        self.itinerary_start: str = ""
        self.itinerary_end: str = ""
        self.permutations_evaluated: int = 0
        self.computation_time_ms: float = 0
        self.explanation: str = ""
        self.data_sources: dict = {}
        self.all_scores: List[dict] = []

    def to_dict(self) -> dict:
        return {
            "ordered_route": self.ordered_route,
            "timeline": self.timeline,
            "total_travel_time_min": round(self.total_travel_time, 1),
            "total_visit_time_min": round(self.total_visit_time, 1),
            "total_duration_min": round(self.total_travel_time + self.total_visit_time, 1),
            "total_impact_score": round(self.total_impact_score, 4),
            "impact_breakdown": self.impact_breakdown,
            "itinerary_start": self.itinerary_start,
            "itinerary_end": self.itinerary_end,
            "permutations_evaluated": self.permutations_evaluated,
            "computation_time_ms": round(self.computation_time_ms, 1),
            "explanation": self.explanation,
            "data_sources": self.data_sources,
        }


# ── Timeline Simulator ───────────────────────────────────

def simulate_timeline(
    start_lat: float,
    start_lon: float,
    attraction_ids: List[str],
    city: str,
    date: datetime,
    start_hour: int = 9,
    preference_mode: str = "balanced",
    travel_mode: str = "driving",
    precomputed_matrix: Optional[dict] = None,
    location_index_map: Optional[dict] = None,
) -> Tuple[List[ItineraryLeg], dict]:
    """
    Simulate a complete itinerary timeline for a given attraction ordering.

    Args:
        precomputed_matrix: NxN travel time/distance matrix (from estimate_travel_matrix).
                            location_index_map maps (lat_rounded, lon_rounded) → index.
        location_index_map: Dict mapping attraction_id → index in precomputed_matrix.
    """
    store = get_data_store()
    month = date.month
    day_name = date.strftime("%A")
    day_type = "weekend" if date.weekday() >= 5 else "weekday"
    date_str = date.strftime("%Y-%m-%d")

    current_time = date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    current_lat = start_lat
    current_lon = start_lon
    current_name = "Start Location"
    current_id = "__start__"

    legs: List[ItineraryLeg] = []
    leg_scores: List[dict] = []

    for attr_id in attraction_ids:
        attr = store.get_attraction(attr_id)
        if not attr:
            continue

        leg = ItineraryLeg()
        leg.attraction_id = attr_id
        leg.attraction_name = attr["name"]
        leg.travel_from = current_name

        # ── Travel Phase ──
        current_hour = current_time.hour

        # Use pre-computed matrix values if available
        if (precomputed_matrix is not None and
                location_index_map is not None and
                current_id in location_index_map and
                attr_id in location_index_map):
            i = location_index_map[current_id]
            j = location_index_map[attr_id]
            travel_minutes = precomputed_matrix["time"][i][j]
            travel_distance = precomputed_matrix["distance"][i][j]
            traffic_index = 0.3  # approximate for scoring
            origin_zone = store.get_zone_for_coords(city, current_lat, current_lon)
            dest_zone = store.get_zone_for_coords(city, attr["latitude"], attr["longitude"])
            travel = {
                "duration_minutes": travel_minutes,
                "distance_km": travel_distance,
                "traffic_index": traffic_index,
                "origin_zone": origin_zone,
                "dest_zone": dest_zone,
                "source": precomputed_matrix.get("source", "matrix"),
            }
        else:
            # Live or static estimate
            travel = estimate_travel_time(
                current_lat, current_lon,
                attr["latitude"], attr["longitude"],
                city, current_hour, day_type, month, travel_mode,
            )

        leg.travel_start = current_time
        travel_minutes = travel["duration_minutes"]
        leg.travel_duration_min = travel_minutes
        leg.travel_distance_km = travel["distance_km"]
        leg.travel_end = current_time + timedelta(minutes=travel_minutes)
        leg.travel_details = travel

        # ── Visit Phase ──
        leg.visit_start = leg.travel_end
        visit_duration = attr.get("average_visit_duration", 60)
        leg.visit_duration_min = visit_duration
        leg.visit_end = leg.visit_start + timedelta(minutes=visit_duration)

        # ── Impact Score ──
        arrival_hour = leg.visit_start.hour
        traffic_index = travel["traffic_index"]

        # Get weather — use city-center coords so the cache key is always the same,
        # avoiding one HTTP call per attraction per permutation.
        city_center = CITY_WEATHER_COORDS.get(city.lower())
        w_lat = city_center[0] if city_center else current_lat
        w_lon = city_center[1] if city_center else current_lon
        weather = store.get_weather(city, month, arrival_hour, date_str, w_lat, w_lon)

        heat_impact = compute_heat_impact(city, month, arrival_hour, attr, weather)
        crowd_factor = compute_crowd_factor(attr, arrival_hour)
        traffic_vol = compute_traffic_volatility(traffic_index, current_hour)

        # Event congestion check
        event_mult = store.get_event_congestion_multiplier(
            city, travel.get("dest_zone", "Central"), day_name
        )
        if event_mult > 1.0:
            traffic_index = min(traffic_index * event_mult, 1.0)
            traffic_vol = min(traffic_vol * 1.2, 1.0)

        score = compute_leg_impact_score(
            traffic_index, heat_impact, crowd_factor, traffic_vol,
            preference_mode
        )
        leg.impact_score = score
        leg_scores.append(score)

        # ── Advance state ──
        current_time = leg.visit_end
        current_lat = attr["latitude"]
        current_lon = attr["longitude"]
        current_name = attr["name"]
        current_id = attr_id
        legs.append(leg)

    itinerary_score = compute_itinerary_score(leg_scores)
    return legs, itinerary_score


# ── Permutation Optimizer ─────────────────────────────────

def optimize_itinerary(
    start_lat: float,
    start_lon: float,
    attraction_ids: List[str],
    city: str,
    date: datetime,
    start_hour: int = 9,
    preference_mode: str = "balanced",
    travel_mode: str = "driving",
) -> OptimizationResult:
    """
    Main optimization function.
    Pre-fetches ORS travel matrix, then evaluates all permutations.

    Args:
        start_lat, start_lon: Starting location (hotel, etc.)
        attraction_ids: List of attraction IDs to visit (3-7)
        city: City name
        date: Date of visit
        start_hour: Start time (hour, default 9)
        preference_mode: "comfort", "fastest", or "balanced"
        travel_mode: "driving" or "walking"
    """
    t_start = time_module.perf_counter()
    store = get_data_store()

    # Validate attractions
    valid_ids = []
    for aid in attraction_ids:
        attr = store.get_attraction(aid)
        if attr:
            valid_ids.append(aid)
        else:
            print(f"  ⚠ Attraction ID not found: {aid}")

    n = len(valid_ids)
    if n == 0:
        result = OptimizationResult()
        result.explanation = "No valid attractions provided."
        return result

    if n > 7:
        print(f"  ⚠ {n} attractions = {_factorial(n)} permutations. Limiting to 7.")
        valid_ids = valid_ids[:7]
        n = 7

    # ── Pre-fetch travel matrix (critical for API efficiency) ─────────────────
    # Build location list: start + all attractions
    day_type = "weekend" if date.weekday() >= 5 else "weekday"
    month = date.month

    all_locations = [{"latitude": start_lat, "longitude": start_lon, "_id": "__start__"}]
    for aid in valid_ids:
        attr = store.get_attraction(aid)
        all_locations.append({
            "latitude": attr["latitude"],
            "longitude": attr["longitude"],
            "_id": aid,
        })

    matrix_result = estimate_travel_matrix(
        all_locations, city, start_hour, day_type, month, travel_mode
    )

    # Build index map: attraction_id → matrix row/column index
    location_index_map = {loc["_id"]: i for i, loc in enumerate(all_locations)}

    data_sources = {"routing": matrix_result.get("source", "static")}

    # ── Pre-fetch weather once using city center (one HTTP call, shared by all legs) ──
    # Weather within a city is effectively uniform — no need for per-attraction lat/lon.
    date_str = date.strftime("%Y-%m-%d")
    _city_center = CITY_WEATHER_COORDS.get(city.lower(), (start_lat, start_lon))
    store.get_weather(city, month, start_hour, date_str, _city_center[0], _city_center[1])

    # ── Evaluate all permutations ──────────────────────────────────────────────
    all_perms = list(itertools.permutations(valid_ids))
    num_perms = len(all_perms)

    best_score = float("inf")
    best_legs = None
    best_itinerary_score = None
    best_perm = None
    all_scores = []

    for perm in all_perms:
        legs, itin_score = simulate_timeline(
            start_lat, start_lon,
            list(perm), city, date, start_hour, preference_mode, travel_mode,
            precomputed_matrix=matrix_result,
            location_index_map=location_index_map,
        )
        total = itin_score["total_score"]
        all_scores.append({
            "permutation": [store.get_attraction(a)["name"] for a in perm],
            "total_score": total,
            "avg_score": itin_score["avg_score"],
        })

        if total < best_score:
            best_score = total
            best_legs = legs
            best_itinerary_score = itin_score
            best_perm = perm

    t_end = time_module.perf_counter()

    # Determine weather source — check if we fetched live data during pre-fetch
    city_center = CITY_WEATHER_COORDS.get(city.lower(), (start_lat, start_lon))
    sample_weather = store.get_weather(city, month, start_hour, date_str,
                                       city_center[0], city_center[1])
    data_sources["weather"] = sample_weather.get("source", "static")

    # Build result
    result = OptimizationResult()
    result.permutations_evaluated = num_perms
    result.computation_time_ms = (t_end - t_start) * 1000
    result.data_sources = data_sources

    if best_legs:
        result.ordered_route = [
            {
                "order": i + 1,
                "attraction_id": leg.attraction_id,
                "name": leg.attraction_name,
            }
            for i, leg in enumerate(best_legs)
        ]
        result.timeline = [leg.to_dict() for leg in best_legs]
        result.total_travel_time = sum(leg.travel_duration_min for leg in best_legs)
        result.total_visit_time = sum(leg.visit_duration_min for leg in best_legs)
        result.total_impact_score = best_itinerary_score["total_score"]
        result.impact_breakdown = best_itinerary_score
        result.itinerary_start = best_legs[0].travel_start.strftime("%H:%M")
        result.itinerary_end = best_legs[-1].visit_end.strftime("%H:%M")

        all_scores.sort(key=lambda x: x["total_score"])
        result.all_scores = all_scores

        result.explanation = _generate_explanation(
            best_legs, best_itinerary_score, all_scores, preference_mode,
            city, date, travel_mode
        )

    return result


def _generate_explanation(
    legs: List[ItineraryLeg],
    score: dict,
    all_scores: list,
    preference_mode: str,
    city: str,
    date: datetime,
    travel_mode: str = "driving",
) -> str:
    """Generate a human-readable explanation of the optimization result."""
    store = get_data_store()
    n = len(legs)
    total_perms = len(all_scores)

    best_total = all_scores[0]["total_score"] if all_scores else 0
    worst_total = all_scores[-1]["total_score"] if all_scores else 0
    improvement = ((worst_total - best_total) / worst_total * 100) if worst_total > 0 else 0

    route_names = " → ".join(leg.attraction_name for leg in legs)

    insights = []

    # Check heat avoidance
    weather = store.get_weather(city, date.month, 14)
    if weather["heat_discomfort"] >= 0.4:
        outdoor_legs = [l for l in legs if store.get_attraction(
            l.attraction_id).get("category") == "outdoor"]
        if outdoor_legs:
            earliest_outdoor = min(l.visit_start.hour for l in outdoor_legs)
            if earliest_outdoor < 11:
                insights.append(
                    f"Outdoor attractions scheduled in morning to avoid afternoon heat ({weather['temperature']:.0f}°C at 2 PM)")

    # Check crowd avoidance
    for leg in legs:
        attr = store.get_attraction(leg.attraction_id)
        peak_hours = attr.get("peak_hours", [])
        if leg.visit_start.hour not in peak_hours and peak_hours:
            insights.append(
                f"{leg.attraction_name} scheduled outside peak hours ({peak_hours})")
            break

    # Check traffic avoidance
    rush_legs = [l for l in legs if 7 <= l.travel_start.hour <= 9 or 17 <= l.travel_start.hour <= 20]
    non_rush = n - len(rush_legs)
    if non_rush > len(rush_legs):
        insights.append(f"{non_rush}/{n} travel segments avoid rush hour")

    mode_label = "on foot" if travel_mode == "walking" else "by car"
    insights_text = "\n".join(f"  • {i}" for i in insights) if insights else "  • Standard optimization applied"

    explanation = f"""Optimized {n}-stop itinerary for {city.capitalize()} on {date.strftime('%B %d, %Y')} ({preference_mode} mode, {mode_label}).

Route: {route_names}

Evaluated {total_perms} possible orderings.
This route scores {best_total:.3f} (best) vs {worst_total:.3f} (worst) — {improvement:.1f}% stress reduction.

Total travel: {sum(l.travel_duration_min for l in legs):.0f} min | Total visiting: {sum(l.visit_duration_min for l in legs):.0f} min

Key optimizations:
{insights_text}

Impact breakdown: Traffic={score['components']['traffic']:.3f}, Heat={score['components']['heat']:.3f}, Crowds={score['components']['crowd']:.3f}, Volatility={score['components']['volatility']:.3f}"""

    return explanation


def _factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * _factorial(n - 1)

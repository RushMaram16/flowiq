"""
SmartTrip AI - Optimization Engine
The core computational component that simulates all route permutations
and selects the itinerary with the lowest Travel Impact Score.

Architecture:
    Step 1: Generate all permutations of N attractions (N! for 3-5 = 6-120)
    Step 2: For each permutation, simulate the full timeline
    Step 3: Compute Travel Impact Score for each
    Step 4: Select the route with the lowest total score
"""

import itertools
import time as time_module
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from engine.data_loader import get_data_store
from engine.travel_estimator import estimate_travel_time
from engine.impact_score import (
    compute_crowd_factor,
    compute_traffic_volatility,
    compute_heat_impact,
    compute_leg_impact_score,
    compute_itinerary_score,
)


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
        self.travel_from: str = ""      # origin name
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
        # For debugging: scores of all permutations
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
) -> Tuple[List[ItineraryLeg], dict]:
    """
    Simulate a complete itinerary timeline for a given attraction ordering.

    Args:
        start_lat, start_lon: Starting location coordinates
        attraction_ids: Ordered list of attraction IDs to visit
        city: City name
        date: Date of the itinerary
        start_hour: Hour to start the itinerary (default 9 AM)
        preference_mode: comfort / fastest / balanced

    Returns:
        Tuple of (list of ItineraryLeg, itinerary_score dict)
    """
    store = get_data_store()
    month = date.month
    day_name = date.strftime("%A")
    day_type = "weekend" if date.weekday() >= 5 else "weekday"

    current_time = date.replace(
        hour=start_hour, minute=0, second=0, microsecond=0)
    current_lat = start_lat
    current_lon = start_lon
    current_name = "Start Location"

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
        travel = estimate_travel_time(
            current_lat, current_lon,
            attr["latitude"], attr["longitude"],
            city, current_hour, day_type, month,
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
        heat_impact = compute_heat_impact(city, month, arrival_hour, attr)
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
) -> OptimizationResult:
    """
    Main optimization function.
    Generates all permutations, simulates each, and returns the best.

    Args:
        start_lat, start_lon: Starting location (hotel, etc.)
        attraction_ids: List of attraction IDs to visit (3-5 recommended)
        city: City name (Madrid, Barcelona, Seville)
        date: Date of visit
        start_hour: Start time (hour, default 9)
        preference_mode: "comfort", "fastest", or "balanced"

    Returns:
        OptimizationResult with the optimal itinerary
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
        print(
            f"  ⚠ {n} attractions = {_factorial(n)} permutations. Limiting to 7.")
        valid_ids = valid_ids[:7]
        n = 7

    # Generate all permutations
    all_perms = list(itertools.permutations(valid_ids))
    num_perms = len(all_perms)

    # Simulate each permutation
    best_score = float("inf")
    best_legs = None
    best_itinerary_score = None
    best_perm = None
    all_scores = []

    for perm in all_perms:
        legs, itin_score = simulate_timeline(
            start_lat, start_lon,
            list(perm), city, date, start_hour, preference_mode
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

    # Build result
    result = OptimizationResult()
    result.permutations_evaluated = num_perms
    result.computation_time_ms = (t_end - t_start) * 1000

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
        result.total_travel_time = sum(
            leg.travel_duration_min for leg in best_legs)
        result.total_visit_time = sum(
            leg.visit_duration_min for leg in best_legs)
        result.total_impact_score = best_itinerary_score["total_score"]
        result.impact_breakdown = best_itinerary_score
        result.itinerary_start = best_legs[0].travel_start.strftime("%H:%M")
        result.itinerary_end = best_legs[-1].visit_end.strftime("%H:%M")

        # Sort all scores to show ranking
        all_scores.sort(key=lambda x: x["total_score"])
        result.all_scores = all_scores

        # Generate explanation
        result.explanation = _generate_explanation(
            best_legs, best_itinerary_score, all_scores, preference_mode, city, date
        )

    return result


def _generate_explanation(
    legs: List[ItineraryLeg],
    score: dict,
    all_scores: list,
    preference_mode: str,
    city: str,
    date: datetime,
) -> str:
    """Generate a human-readable explanation of the optimization result."""
    store = get_data_store()
    n = len(legs)
    total_perms = len(all_scores)

    best_total = all_scores[0]["total_score"] if all_scores else 0
    worst_total = all_scores[-1]["total_score"] if all_scores else 0
    improvement = ((worst_total - best_total) /
                   worst_total * 100) if worst_total > 0 else 0

    route_names = " → ".join(leg.attraction_name for leg in legs)

    # Identify key optimization decisions
    insights = []

    # Check heat avoidance
    weather = store.get_weather(city, date.month, 14)  # 2 PM temp
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
            break  # Only mention one example

    # Check traffic avoidance
    rush_legs = [l for l in legs if 7 <= l.travel_start.hour <=
                 9 or 17 <= l.travel_start.hour <= 20]
    non_rush = n - len(rush_legs)
    if non_rush > len(rush_legs):
        insights.append(f"{non_rush}/{n} travel segments avoid rush hour")

    insights_text = "\n".join(
        f"  • {i}" for i in insights) if insights else "  • Standard optimization applied"

    explanation = f"""Optimized {n}-stop itinerary for {city.capitalize()} on {date.strftime('%B %d, %Y')} ({preference_mode} mode).

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

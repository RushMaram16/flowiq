"""
SmartTrip AI - Travel Impact Score Calculator
Computes the stress/discomfort score for each leg and full itinerary.

Impact Score per leg = 
    0.4 × Traffic Intensity
  + 0.2 × Heat Discomfort Index  
  + 0.2 × Crowd Factor
  + 0.2 × Traffic Volatility

Preference modes adjust these weights.
"""

from typing import Dict, List, Optional
from engine.data_loader import get_data_store


# ── Weight Profiles by Preference Mode ───────────────────

WEIGHT_PROFILES = {
    "balanced": {
        "traffic":     0.40,
        "heat":        0.20,
        "crowd":       0.20,
        "volatility":  0.20,
    },
    "comfort": {
        "traffic":     0.25,
        "heat":        0.35,
        "crowd":       0.25,
        "volatility":  0.15,
    },
    "fastest": {
        "traffic":     0.55,
        "heat":        0.10,
        "crowd":       0.15,
        "volatility":  0.20,
    },
}


def compute_crowd_factor(attraction: dict, arrival_hour: int) -> float:
    """
    Compute crowd factor (0-1) for an attraction at a given hour.
    Uses peak_hours data and ideal time windows.
    """
    peak_hours = attraction.get("peak_hours", [])
    ideal_start = attraction.get("ideal_time_start", 9)
    ideal_end = attraction.get("ideal_time_end", 18)

    # Base crowd level from time-of-day
    if arrival_hour in peak_hours:
        base_crowd = 0.85
    elif ideal_start <= arrival_hour <= ideal_end:
        base_crowd = 0.40
    else:
        # Outside operating hours or very early/late
        base_crowd = 0.15

    # Priority/popularity multiplier — more popular = more crowded
    priority = attraction.get("priority_score", 5.0)
    popularity_factor = min(priority / 10.0, 1.0)  # normalize to 0-1

    # Weighted combination
    crowd = base_crowd * 0.7 + popularity_factor * 0.3

    return round(min(crowd, 1.0), 3)


def compute_traffic_volatility(traffic_index: float, hour: int) -> float:
    """
    Estimate traffic volatility (unpredictability).
    Rush hours have higher volatility; off-peak is more predictable.
    """
    # Rush hour ranges
    morning_rush = 7 <= hour <= 9
    evening_rush = 17 <= hour <= 20

    if morning_rush or evening_rush:
        base_vol = 0.6
    elif 10 <= hour <= 16:
        base_vol = 0.3
    else:
        base_vol = 0.15

    # Higher congestion = more volatility
    congestion_vol = traffic_index * 0.4

    return round(min(base_vol + congestion_vol, 1.0), 3)


def compute_heat_impact(
    city: str, month: int, hour: int,
    attraction: dict,
) -> float:
    """
    Compute heat discomfort impact considering whether attraction is heat-sensitive.
    """
    store = get_data_store()
    weather = store.get_weather(city, month, hour)
    heat_discomfort = weather["heat_discomfort"]

    is_heat_sensitive = attraction.get("heat_sensitive", False)
    category = attraction.get("category", "")

    if is_heat_sensitive or category == "outdoor":
        # Full heat impact for outdoor/heat-sensitive attractions
        return heat_discomfort
    elif category == "indoor":
        # Indoor attractions: minimal heat impact (just travel exposure)
        return heat_discomfort * 0.2
    else:
        # Landmarks, markets: moderate
        return heat_discomfort * 0.5


def compute_leg_impact_score(
    travel_traffic_index: float,
    heat_impact: float,
    crowd_factor: float,
    traffic_volatility: float,
    preference_mode: str = "balanced",
) -> dict:
    """
    Compute the Travel Impact Score for a single leg.

    Returns dict with component scores and total.
    """
    weights = WEIGHT_PROFILES.get(preference_mode, WEIGHT_PROFILES["balanced"])

    total = (
        weights["traffic"] * travel_traffic_index +
        weights["heat"] * heat_impact +
        weights["crowd"] * crowd_factor +
        weights["volatility"] * traffic_volatility
    )

    return {
        "total_score": round(total, 4),
        "traffic_component": round(weights["traffic"] * travel_traffic_index, 4),
        "heat_component": round(weights["heat"] * heat_impact, 4),
        "crowd_component": round(weights["crowd"] * crowd_factor, 4),
        "volatility_component": round(weights["volatility"] * traffic_volatility, 4),
        "raw_traffic": round(travel_traffic_index, 3),
        "raw_heat": round(heat_impact, 3),
        "raw_crowd": round(crowd_factor, 3),
        "raw_volatility": round(traffic_volatility, 3),
    }


def compute_itinerary_score(leg_scores: List[dict]) -> dict:
    """
    Aggregate leg scores into a total itinerary score.
    """
    if not leg_scores:
        return {"total_score": 0, "avg_score": 0, "max_score": 0, "legs": 0}

    totals = [leg["total_score"] for leg in leg_scores]

    return {
        "total_score": round(sum(totals), 4),
        "avg_score": round(sum(totals) / len(totals), 4),
        "max_score": round(max(totals), 4),
        "min_score": round(min(totals), 4),
        "legs": len(leg_scores),
        "components": {
            "traffic": round(sum(l["traffic_component"] for l in leg_scores), 4),
            "heat": round(sum(l["heat_component"] for l in leg_scores), 4),
            "crowd": round(sum(l["crowd_component"] for l in leg_scores), 4),
            "volatility": round(sum(l["volatility_component"] for l in leg_scores), 4),
        }
    }

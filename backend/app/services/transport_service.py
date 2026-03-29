from typing import List, Dict, Any, Optional

from app.models.transport_models import ItineraryStop
from app.services.ors_service import get_route


def build_route_legs(itinerary: List[ItineraryStop], start_point: str = None) -> List[Dict[str, Any]]:
    legs = []

    if not itinerary:
        return legs

    if start_point:
        legs.append({
            "from_place": start_point,
            "to_place": itinerary[0].place
        })

    for i in range(len(itinerary) - 1):
        legs.append({
            "from_place": itinerary[i].place,
            "to_place": itinerary[i + 1].place
        })

    return legs


def get_real_transport_options(from_place: str, to_place: str, allow_walking: bool = True) -> List[Dict[str, Any]]:
    modes = ["driving-car", "cycling-regular"]

    if allow_walking:
        modes.append("foot-walking")

    options = []

    for mode in modes:
        try:
            route = get_route(from_place, to_place, mode=mode)

            if mode == "driving-car":
                option_name = "taxi"
                cost_estimate = round(4.0 + route["distance_km"] * 2.5, 2)
                walking_distance_m = 100.0
                reason = "Fast road option based on real route data."
            elif mode == "cycling-regular":
                option_name = "cycling"
                cost_estimate = 2.0
                walking_distance_m = 150.0
                reason = "Efficient low-cost cycling route based on real route data."
            else:
                option_name = "walking"
                cost_estimate = 0.0
                walking_distance_m = route["distance_km"] * 1000
                reason = "Walking route based on real route data."

            options.append({
                "mode": option_name,
                "duration_min": route["duration_min"],
                "cost_estimate": cost_estimate,
                "walking_distance_m": round(walking_distance_m, 2),
                "recommendation_type": None,
                "reason": reason
            })

        except Exception as e:
            print(f"Error fetching route for {mode}: {e}")

    return options


def get_weather_penalty(option: Dict[str, Any], weather_data: Optional[Dict[str, Any]] = None) -> float:
    if not weather_data:
        return 0.0

    mode = option["mode"]
    condition = str(weather_data.get("condition", "")).lower()
    temperature_c = weather_data.get("temperature_c", 22)
    wind_kph = weather_data.get("wind_kph", 0)

    penalty = 0.0

    if mode == "walking":
        if "rain" in condition or "storm" in condition:
            penalty += 12.0
        if temperature_c >= 32 or temperature_c <= 5:
            penalty += 8.0

    if mode == "cycling":
        if "rain" in condition or "storm" in condition:
            penalty += 10.0
        if wind_kph >= 20:
            penalty += 8.0
        if temperature_c >= 32 or temperature_c <= 5:
            penalty += 6.0

    if mode == "taxi":
        if "rain" in condition or "storm" in condition:
            penalty -= 2.0

    return penalty


def rank_transport_options(
    options: List[Dict[str, Any]],
    weather_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Dict[str, Any]]:
    if not options:
        return {}

    fastest = min(options, key=lambda x: x["duration_min"])
    cheapest = min(options, key=lambda x: x["cost_estimate"])

    def balanced_score(opt):
        weather_penalty = get_weather_penalty(opt, weather_data)

        return (
            0.5 * opt["duration_min"] +
            0.3 * opt["cost_estimate"] +
            0.2 * (opt["walking_distance_m"] / 1000) +
            weather_penalty
        )

    balanced = min(options, key=balanced_score)

    fastest_result = fastest.copy()
    cheapest_result = cheapest.copy()
    balanced_result = balanced.copy()

    fastest_result["recommendation_type"] = "Fastest"
    cheapest_result["recommendation_type"] = "Cheapest"
    balanced_result["recommendation_type"] = "Balanced"

    return {
        "fastest": fastest_result,
        "cheapest": cheapest_result,
        "balanced": balanced_result
    }


def build_transport_plan(
    itinerary: List[ItineraryStop],
    allow_walking: bool = True,
    start_point: str = None,
    weather_data: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    legs = build_route_legs(itinerary, start_point)
    result = []

    for leg in legs:
        options = get_real_transport_options(
            from_place=leg["from_place"],
            to_place=leg["to_place"],
            allow_walking=allow_walking
        )

        ranked = rank_transport_options(options, weather_data=weather_data)

        result.append({
            "from_place": leg["from_place"],
            "to_place": leg["to_place"],
            "weather_used": weather_data,
            "options": options,
            "recommended_fastest": ranked.get("fastest"),
            "recommended_cheapest": ranked.get("cheapest"),
            "recommended_balanced": ranked.get("balanced")
        })

    return result

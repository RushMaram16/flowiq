"""
SmartTrip AI - Quick smoke test for the optimizer engine.
Run from the project root:  python engine/test_engine_manual.py
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.data_loader import get_data_store
from engine.optimizer import optimize_itinerary


def run(city: str, month: int, mode: str = "balanced"):
    store = get_data_store()
    attractions = store.get_attractions_by_city(city)
    top3 = sorted(attractions, key=lambda a: a["priority_score"], reverse=True)[:3]
    ids = [a["id"] for a in top3]
    names = [a["name"] for a in top3]

    # Pick a start coordinate near the city centre
    city_starts = {
        "Madrid":    (40.4200, -3.7050),
        "Barcelona": (41.3870,  2.1700),
        "Seville":   (37.3891, -5.9845),
    }
    lat, lon = city_starts.get(city, (40.4200, -3.7050))
    date = datetime(2025, month, 15)

    print(f"\n{'='*60}")
    print(f"  {city} — {date.strftime('%B %Y')} — mode: {mode}")
    print(f"{'='*60}")
    print(f"  Attractions: {', '.join(names)}")

    result = optimize_itinerary(
        start_lat=lat,
        start_lon=lon,
        attraction_ids=ids,
        city=city,
        date=date,
        start_hour=9,
        preference_mode=mode,
    )

    r = result.to_dict()
    print(f"  Evaluated {r['permutations_evaluated']} permutations in {r['computation_time_ms']:.0f}ms")
    print(f"  Impact score: {r['total_impact_score']:.4f}")
    print(f"  Timeline: {r['itinerary_start']} -> {r['itinerary_end']}\n")
    for leg in r["timeline"]:
        score = leg["impact_score"]["total_score"]
        print(
            f"    {leg['arrival_time']}  {leg['attraction_name']:<35s}"
            f"  visit {int(leg['visit_duration_min'])}min"
            f"  travel {leg['travel_duration_min']}min"
            f"  stress={score:.3f}"
        )
    bd = r["impact_breakdown"]["components"]
    print(
        f"\n  Breakdown → traffic={bd['traffic']:.3f}  heat={bd['heat']:.3f}"
        f"  crowd={bd['crowd']:.3f}  volatility={bd['volatility']:.3f}"
    )


if __name__ == "__main__":
    run("Madrid",    7,  "comfort")    # Hot July, comfort mode
    run("Barcelona", 4,  "balanced")   # Spring, balanced
    run("Seville",   8,  "fastest")    # Extreme heat August, fastest

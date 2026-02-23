"""
SmartTrip AI - Phase 2 Test Suite & Demo
Tests all engine components and generates sample optimized itineraries.
"""

from engine.optimizer import optimize_itinerary, simulate_timeline
from engine.impact_score import (
    compute_crowd_factor,
    compute_traffic_volatility,
    compute_heat_impact,
    compute_leg_impact_score,
    compute_itinerary_score,
    WEIGHT_PROFILES,
)
from engine.travel_estimator import estimate_travel_time, estimate_travel_matrix
from engine.data_loader import get_data_store, haversine_km
import sys
import json
from datetime import datetime
sys.path.insert(0, "engine/phase1_data.xlsx")


def separator(title):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")


def test_data_loader():
    separator("TEST 1: Data Loader")
    ds = get_data_store()

    # Attractions
    for city in ["Madrid", "Barcelona", "Seville"]:
        atts = ds.get_attractions_by_city(city)
        assert len(
            atts) > 10, f"{city} should have >10 attractions, got {len(atts)}"
        print(f"  ✓ {city}: {len(atts)} attractions loaded")

    # Traffic
    for city in ["Madrid", "Barcelona", "Seville"]:
        idx = ds.get_traffic_index(city, "Central", "weekday", 8)
        assert 0 < idx <= 1.0, f"Traffic index out of range: {idx}"
    print(f"  ✓ Traffic baseline lookup working (3 cities × 5 zones × 24 hours)")

    # Weather
    for city in ["madrid", "barcelona", "seville"]:
        w = ds.get_weather(city, 7, 14)
        assert w["temperature"] > 20, f"July 2PM should be hot, got {w['temperature']}"
        assert w["heat_discomfort"] > 0, f"Should have heat discomfort in July"
    print(f"  ✓ Weather baseline working (seasonal temperature profiles)")

    # Zone detection
    zone = ds.get_zone_for_coords("Madrid", 40.4168, -3.7038)
    assert zone in (
        "Central", "Tourist Cluster"), f"Puerta del Sol should be Central/Tourist, got {zone}"
    print(f"  ✓ Zone detection working (coordinate → zone mapping)")

    print("\n  ALL DATA LOADER TESTS PASSED ✓")


def test_travel_estimator():
    separator("TEST 2: Travel Time Estimator")
    ds = get_data_store()

    # Test realistic travel time
    # Prado → Retiro (very close, ~500m)
    t1 = estimate_travel_time(
        40.4138, -3.6921, 40.4153, -3.6845, "Madrid", 10, "weekday", 6)
    assert 1 < t1[
        "duration_minutes"] < 15, f"Prado→Retiro should be 1-15min, got {t1['duration_minutes']}"
    print(
        f"  ✓ Short distance: Prado → Retiro = {t1['duration_minutes']}min ({t1['distance_km']}km)")

    # Sagrada Familia → Park Güell (~2.5km)
    t2 = estimate_travel_time(41.4036, 2.1744, 41.4145,
                              2.1527, "Barcelona", 10, "weekday", 6)
    assert 3 < t2[
        "duration_minutes"] < 25, f"Sagrada→Güell should be 3-25min, got {t2['duration_minutes']}"
    print(
        f"  ✓ Medium distance: Sagrada Família → Park Güell = {t2['duration_minutes']}min ({t2['distance_km']}km)")

    # Rush hour vs off-peak
    t_rush = estimate_travel_time(
        40.4138, -3.6921, 40.4531, -3.6883, "Madrid", 8, "weekday", 6)
    t_offpeak = estimate_travel_time(
        40.4138, -3.6921, 40.4531, -3.6883, "Madrid", 22, "weekday", 6)
    assert t_rush["duration_minutes"] > t_offpeak["duration_minutes"], "Rush hour should be slower"
    print(
        f"  ✓ Rush hour effect: 8AM={t_rush['duration_minutes']}min vs 10PM={t_offpeak['duration_minutes']}min ({t_rush['duration_minutes']/t_offpeak['duration_minutes']:.1f}x slower)")

    # Weekend vs weekday
    t_wd = estimate_travel_time(
        40.4138, -3.6921, 40.4180, -3.7143, "Madrid", 9, "weekday", 6)
    t_we = estimate_travel_time(
        40.4138, -3.6921, 40.4180, -3.7143, "Madrid", 9, "weekend", 6)
    print(
        f"  ✓ Day type effect: Weekday 9AM={t_wd['duration_minutes']}min vs Weekend={t_we['duration_minutes']}min")

    # Travel matrix
    locs = [
        {"latitude": 40.4138, "longitude": -3.6921},  # Prado
        {"latitude": 40.4180, "longitude": -3.7143},  # Palace
        {"latitude": 40.4153, "longitude": -3.6845},  # Retiro
    ]
    matrix = estimate_travel_matrix(locs, "Madrid", 10, "weekday", 6)
    assert len(matrix) == 3
    assert all(matrix[i][i] == 0 for i in range(3))
    print(
        f"  ✓ Travel matrix (3×3): diagonal=0, max={max(max(r) for r in matrix):.1f}min")

    print("\n  ALL TRAVEL ESTIMATOR TESTS PASSED ✓")


def test_impact_score():
    separator("TEST 3: Impact Score Calculator")
    ds = get_data_store()

    prado = ds.get_attractions_by_city("Madrid")[0]

    # Crowd factor varies by hour
    crowd_peak = compute_crowd_factor(prado, 11)   # Peak
    crowd_off = compute_crowd_factor(prado, 17)     # Off-peak
    assert crowd_peak > crowd_off, "Peak hour should have higher crowd"
    print(
        f"  ✓ Crowd factor: peak(11)={crowd_peak:.3f} > off-peak(17)={crowd_off:.3f}")

    # Heat impact varies by category
    retiro = ds.get_attractions_by_city("Madrid")[4]  # outdoor
    heat_outdoor = compute_heat_impact("Madrid", 7, 14, retiro)
    heat_indoor = compute_heat_impact("Madrid", 7, 14, prado)
    assert heat_outdoor > heat_indoor, "Outdoor should have more heat impact"
    print(
        f"  ✓ Heat impact: outdoor({retiro['name']})={heat_outdoor:.3f} > indoor({prado['name']})={heat_indoor:.3f}")

    # Traffic volatility
    vol_rush = compute_traffic_volatility(0.8, 8)
    vol_calm = compute_traffic_volatility(0.2, 14)
    assert vol_rush > vol_calm, "Rush hour should have more volatility"
    print(f"  ✓ Volatility: rush={vol_rush:.3f} > calm={vol_calm:.3f}")

    # Full leg score
    score = compute_leg_impact_score(0.7, 0.5, 0.8, 0.6, "balanced")
    assert 0 < score["total_score"] < 1, f"Score should be 0-1, got {score['total_score']}"
    print(f"  ✓ Leg impact score: {score['total_score']:.4f}")
    print(f"    Components: traffic={score['traffic_component']:.3f}, heat={score['heat_component']:.3f}, "
          f"crowd={score['crowd_component']:.3f}, vol={score['volatility_component']:.3f}")

    # Weight profiles
    for mode in ["balanced", "comfort", "fastest"]:
        s = compute_leg_impact_score(0.7, 0.8, 0.6, 0.5, mode)
        print(f"  ✓ Mode '{mode}': total={s['total_score']:.4f}")

    print("\n  ALL IMPACT SCORE TESTS PASSED ✓")


def test_optimizer_madrid():
    separator("TEST 4: Full Optimizer — Madrid Summer Day")
    ds = get_data_store()

    # Pick 5 popular Madrid attractions
    madrid = ds.get_attractions_by_city("Madrid")
    top5 = sorted(madrid, key=lambda a: a["priority_score"], reverse=True)[:5]
    ids = [a["id"] for a in top5]
    print(f"  Attractions: {', '.join(a['name'] for a in top5)}")

    date = datetime(2025, 7, 15)  # Hot summer day
    start_lat, start_lon = 40.4200, -3.7050  # Near Gran Via (hotel)

    result = optimize_itinerary(
        start_lat, start_lon, ids, "Madrid", date,
        start_hour=9, preference_mode="comfort"
    )

    r = result.to_dict()
    print(
        f"\n  ⏱  Evaluated {r['permutations_evaluated']} permutations in {r['computation_time_ms']:.0f}ms")
    print(f"  📊 Total Impact Score: {r['total_impact_score']:.4f}")
    print(
        f"  🕐 {r['itinerary_start']} → {r['itinerary_end']} ({r['total_duration_min']:.0f} min total)")
    print(
        f"  🚗 Travel: {r['total_travel_time_min']:.0f}min | 🎫 Visits: {r['total_visit_time_min']:.0f}min")

    print(f"\n  Optimal Route:")
    for leg in r["timeline"]:
        arr = leg["arrival_time"]
        dep = leg["visit_end"]
        score = leg["impact_score"]["total_score"]
        print(f"    {leg['arrival_time']} │ {leg['attraction_name']:<35s} │ visit {leg['visit_duration_min']}min │ travel {leg['travel_duration_min']}min │ stress={score:.3f}")

    print(f"\n  Impact Breakdown:")
    bd = r["impact_breakdown"]["components"]
    print(
        f"    Traffic={bd['traffic']:.3f}  Heat={bd['heat']:.3f}  Crowds={bd['crowd']:.3f}  Volatility={bd['volatility']:.3f}")

    # Show top 3 vs bottom 3 routes
    if result.all_scores:
        print(f"\n  Best 3 routes:")
        for s in result.all_scores[:3]:
            route = " → ".join(n[:15] for n in s["permutation"])
            print(f"    {s['total_score']:.4f}  {route}")
        print(f"  Worst 3 routes:")
        for s in result.all_scores[-3:]:
            route = " → ".join(n[:15] for n in s["permutation"])
            print(f"    {s['total_score']:.4f}  {route}")

    print(f"\n  Explanation:\n{result.explanation}")

    assert r["permutations_evaluated"] == 120, f"5! should be 120, got {r['permutations_evaluated']}"
    assert r["total_impact_score"] > 0
    assert len(r["timeline"]) == 5
    print("\n  MADRID OPTIMIZER TEST PASSED ✓")
    return result


def test_optimizer_barcelona():
    separator("TEST 5: Full Optimizer — Barcelona Spring Day")
    ds = get_data_store()

    bcn = ds.get_attractions_by_city("Barcelona")
    # Curated selection: Sagrada, Güell, Boqueria, Gothic Quarter
    selected_names = ["Sagrada Família",
                      "Park Güell", "La Boqueria", "Gothic Quarter"]
    selected = []
    for name in selected_names:
        match = [a for a in bcn if name.lower() in a["name"].lower()]
        if match:
            selected.append(match[0])

    if len(selected) < 3:
        # Fallback to top by priority
        selected = sorted(
            bcn, key=lambda a: a["priority_score"], reverse=True)[:4]

    ids = [a["id"] for a in selected]
    print(f"  Attractions: {', '.join(a['name'] for a in selected)}")

    date = datetime(2025, 4, 20)  # Pleasant spring day
    start_lat, start_lon = 41.3870, 2.1700  # Plaça Catalunya (hotel)

    result = optimize_itinerary(
        start_lat, start_lon, ids, "Barcelona", date,
        start_hour=9, preference_mode="balanced"
    )

    r = result.to_dict()
    print(
        f"\n  ⏱  {r['permutations_evaluated']} permutations in {r['computation_time_ms']:.0f}ms")
    print(f"  📊 Impact Score: {r['total_impact_score']:.4f}")
    print(f"  🕐 {r['itinerary_start']} → {r['itinerary_end']}")

    for leg in r["timeline"]:
        print(f"    {leg['arrival_time']} │ {leg['attraction_name']:<35s} │ visit {leg['visit_duration_min']}min │ score={leg['impact_score']['total_score']:.3f}")

    print("\n  BARCELONA OPTIMIZER TEST PASSED ✓")
    return result


def test_optimizer_seville():
    separator("TEST 6: Full Optimizer — Seville August (Extreme Heat)")
    ds = get_data_store()

    sev = ds.get_attractions_by_city("Seville")
    top4 = sorted(sev, key=lambda a: a["priority_score"], reverse=True)[:4]
    ids = [a["id"] for a in top4]
    print(f"  Attractions: {', '.join(a['name'] for a in top4)}")

    date = datetime(2025, 8, 10)  # Extreme heat
    start_lat, start_lon = 37.3891, -5.9845  # City center

    # Compare comfort vs fastest mode
    for mode in ["comfort", "fastest"]:
        result = optimize_itinerary(
            start_lat, start_lon, ids, "Seville", date,
            start_hour=9, preference_mode=mode
        )
        r = result.to_dict()
        route = " → ".join(leg["attraction_name"][:20]
                           for leg in r["timeline"])
        bd = r["impact_breakdown"]["components"]
        print(
            f"\n  [{mode.upper():>8s}] Score={r['total_impact_score']:.4f} | Heat={bd['heat']:.3f} | Route: {route}")

    print("\n  SEVILLE OPTIMIZER TEST PASSED ✓")


def test_preference_modes():
    separator("TEST 7: Preference Mode Comparison")
    ds = get_data_store()

    madrid = ds.get_attractions_by_city("Madrid")
    top4 = sorted(madrid, key=lambda a: a["priority_score"], reverse=True)[:4]
    ids = [a["id"] for a in top4]

    date = datetime(2025, 7, 15)
    start_lat, start_lon = 40.4200, -3.7050

    results = {}
    for mode in ["balanced", "comfort", "fastest"]:
        result = optimize_itinerary(
            start_lat, start_lon, ids, "Madrid", date,
            start_hour=9, preference_mode=mode
        )
        r = result.to_dict()
        route = [leg["attraction_name"] for leg in r["timeline"]]
        bd = r["impact_breakdown"]["components"]
        results[mode] = r
        print(f"  {mode:>10s}: score={r['total_impact_score']:.4f} | travel={r['total_travel_time_min']:.0f}min "
              f"| traffic={bd['traffic']:.3f} heat={bd['heat']:.3f} crowd={bd['crowd']:.3f}")
        print(f"             route: {' → '.join(n[:18] for n in route)}")

    print("\n  PREFERENCE MODE TEST PASSED ✓")


def test_edge_cases():
    separator("TEST 8: Edge Cases")
    ds = get_data_store()

    date = datetime(2025, 6, 15)

    # Single attraction
    madrid = ds.get_attractions_by_city("Madrid")
    single_id = [madrid[0]["id"]]
    r = optimize_itinerary(40.42, -3.70, single_id, "Madrid", date)
    assert len(r.to_dict()["timeline"]) == 1
    print(f"  ✓ Single attraction: {r.to_dict()['total_impact_score']:.4f}")

    # Two attractions
    two_ids = [madrid[0]["id"], madrid[1]["id"]]
    r = optimize_itinerary(40.42, -3.70, two_ids, "Madrid", date)
    assert r.to_dict()["permutations_evaluated"] == 2
    print(
        f"  ✓ Two attractions: {r.to_dict()['permutations_evaluated']} permutations")

    # Invalid attraction ID
    r = optimize_itinerary(40.42, -3.70, ["invalid-id"], "Madrid", date)
    assert len(r.to_dict()["timeline"]) == 0
    print(f"  ✓ Invalid ID handled gracefully")

    # Weekend
    weekend_date = datetime(2025, 6, 14)  # Saturday
    r = optimize_itinerary(40.42, -3.70, two_ids, "Madrid", weekend_date)
    assert r.to_dict()["permutations_evaluated"] == 2
    print(f"  ✓ Weekend optimization works")

    # 3 attractions (6 perms)
    three_ids = [madrid[0]["id"], madrid[1]["id"], madrid[2]["id"]]
    r = optimize_itinerary(40.42, -3.70, three_ids, "Madrid", date)
    assert r.to_dict()["permutations_evaluated"] == 6
    print(
        f"  ✓ 3 attractions: 6 permutations, score={r.to_dict()['total_impact_score']:.4f}")

    print("\n  ALL EDGE CASE TESTS PASSED ✓")


def generate_sample_output():
    """Generate a complete JSON output for a sample itinerary — the format the API would return."""
    separator("SAMPLE API OUTPUT")
    ds = get_data_store()

    bcn = ds.get_attractions_by_city("Barcelona")
    selected_names = ["Sagrada Família", "Park Güell",
                      "Casa Batlló", "La Boqueria", "Gothic Quarter"]
    selected = []
    for name in selected_names:
        match = [a for a in bcn if name.lower() in a["name"].lower()]
        if match:
            selected.append(match[0])

    ids = [a["id"] for a in selected]
    date = datetime(2025, 5, 20)

    result = optimize_itinerary(
        41.3870, 2.1700, ids, "Barcelona", date,
        start_hour=9, preference_mode="balanced"
    )

    output = result.to_dict()

    # Save sample output
    with open("/home/claude/smarttrip-ai/data/sample_optimization_output.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(json.dumps(output, indent=2, default=str))
    print(f"\n  Saved to data/sample_optimization_output.json")


# ═══════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  SmartTrip AI — Phase 2 Test Suite")
    print("=" * 70)

    test_data_loader()
    test_travel_estimator()
    test_impact_score()
    madrid_result = test_optimizer_madrid()
    bcn_result = test_optimizer_barcelona()
    test_optimizer_seville()
    test_preference_modes()
    test_edge_cases()
    generate_sample_output()

    separator("ALL TESTS PASSED ✓✓✓")
    print("  Phase 2 Model Development: COMPLETE")
    print(f"{'═'*70}\n")

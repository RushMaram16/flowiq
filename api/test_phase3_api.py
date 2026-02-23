"""
SmartTrip AI - Phase 3 API Test Suite
Tests all endpoints using Flask's built-in test client (no server needed).
"""

from api.app import app
import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def separator(title):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")


client = app.test_client()


def test_health():
    separator("TEST 1: GET /api/health")
    r = client.get("/api/health")
    data = r.get_json()

    assert r.status_code == 200
    assert data["status"] == "healthy"
    assert data["attractions_loaded"] > 100
    assert "madrid" in data["cities"]
    print(f"  ✓ Status: {data['status']}")
    print(f"  ✓ Attractions loaded: {data['attractions_loaded']}")
    print(f"  ✓ Cities: {data['cities']}")
    print(f"  ✓ Version: {data['version']}")
    print("  PASSED ✓")


def test_api_docs():
    separator("TEST 2: GET / (API Documentation)")
    r = client.get("/api")
    data = r.get_json()

    assert r.status_code == 200
    assert "endpoints" in data
    assert "POST /api/optimize" in data["endpoints"]
    print(f"  ✓ API name: {data['name']}")
    print(f"  ✓ Endpoints documented: {len(data['endpoints'])}")
    print("  PASSED ✓")


def test_attractions_list():
    separator("TEST 3: GET /api/attractions")

    # Basic city query
    r = client.get("/api/attractions?city=madrid")
    data = r.get_json()
    assert r.status_code == 200
    assert data["success"]
    assert data["count"] > 20
    assert data["city"] == "Madrid"
    print(f"  ✓ Madrid: {data['count']} attractions")

    # Check first attraction has all required fields
    first = data["attractions"][0]
    required_fields = ["id", "name", "latitude", "longitude", "category",
                       "average_visit_duration", "priority_score"]
    for field in required_fields:
        assert field in first, f"Missing field: {field}"
    print(
        f"  ✓ Top attraction: {first['name']} (priority={first['priority_score']})")

    # Barcelona
    r = client.get("/api/attractions?city=barcelona")
    data = r.get_json()
    assert data["count"] > 20
    print(f"  ✓ Barcelona: {data['count']} attractions")

    # Seville
    r = client.get("/api/attractions?city=seville")
    data = r.get_json()
    assert data["count"] > 20
    print(f"  ✓ Seville: {data['count']} attractions")

    # Filter by category
    r = client.get("/api/attractions?city=madrid&category=indoor")
    data = r.get_json()
    assert data["count"] > 0
    assert all(a["category"] == "indoor" for a in data["attractions"])
    print(f"  ✓ Madrid indoor: {data['count']} attractions")

    # Filter by priority
    r = client.get("/api/attractions?city=barcelona&min_priority=9")
    data = r.get_json()
    assert all(a["priority_score"] >= 9.0 for a in data["attractions"])
    print(f"  ✓ Barcelona priority≥9: {data['count']} attractions")

    # Limit
    r = client.get("/api/attractions?city=madrid&limit=5")
    data = r.get_json()
    assert data["count"] == 5
    print(f"  ✓ Limit=5: returned {data['count']} attractions")

    # Missing city
    r = client.get("/api/attractions")
    assert r.status_code == 400
    print(f"  ✓ Missing city → 400 error")

    # Invalid city
    r = client.get("/api/attractions?city=paris")
    assert r.status_code == 400
    print(f"  ✓ Invalid city → 400 error")

    print("  PASSED ✓")


def test_attraction_by_id():
    separator("TEST 4: GET /api/attractions/<id>")

    # Get a valid ID first
    r = client.get("/api/attractions?city=madrid&limit=1")
    attraction = r.get_json()["attractions"][0]
    aid = attraction["id"]

    # Fetch by ID
    r = client.get(f"/api/attractions/{aid}")
    data = r.get_json()
    assert r.status_code == 200
    assert data["success"]
    assert data["attraction"]["name"] == attraction["name"]
    print(f"  ✓ Found: {data['attraction']['name']}")

    # Invalid ID
    r = client.get("/api/attractions/nonexistent-uuid")
    assert r.status_code == 404
    print(f"  ✓ Invalid ID → 404")

    print("  PASSED ✓")


def test_traffic_estimate():
    separator("TEST 5: GET /api/traffic-estimate")

    # Prado → Royal Palace (Madrid)
    r = client.get(
        "/api/traffic-estimate?"
        "origin_lat=40.4138&origin_lon=-3.6921"
        "&dest_lat=40.4180&dest_lon=-3.7143"
        "&city=Madrid&hour=9&day_type=weekday&month=7"
    )
    data = r.get_json()
    assert r.status_code == 200
    assert data["success"]
    assert data["duration_minutes"] > 0
    assert data["distance_km"] > 0
    assert 0 <= data["traffic_index"] <= 1
    print(
        f"  ✓ Prado → Palace: {data['duration_minutes']}min, {data['distance_km']}km")
    print(
        f"    traffic={data['traffic_index']}, speed={data['speed_kmh']}km/h")

    # Rush hour vs off-peak
    r_rush = client.get(
        "/api/traffic-estimate?"
        "origin_lat=40.4138&origin_lon=-3.6921"
        "&dest_lat=40.4531&dest_lon=-3.6883"
        "&city=Madrid&hour=8&day_type=weekday&month=6"
    )
    r_off = client.get(
        "/api/traffic-estimate?"
        "origin_lat=40.4138&origin_lon=-3.6921"
        "&dest_lat=40.4531&dest_lon=-3.6883"
        "&city=Madrid&hour=22&day_type=weekday&month=6"
    )
    rush = r_rush.get_json()
    off = r_off.get_json()
    assert rush["duration_minutes"] > off["duration_minutes"]
    print(
        f"  ✓ Rush(8AM)={rush['duration_minutes']}min > Off-peak(10PM)={off['duration_minutes']}min")

    # Missing city
    r = client.get(
        "/api/traffic-estimate?origin_lat=40&origin_lon=-3&dest_lat=40.1&dest_lon=-3.1")
    assert r.status_code == 400
    print(f"  ✓ Missing city → 400")

    print("  PASSED ✓")


def test_weather_estimate():
    separator("TEST 6: GET /api/weather-estimate")

    # Single hour
    r = client.get("/api/weather-estimate?city=madrid&month=7&hour=14")
    data = r.get_json()
    assert r.status_code == 200
    assert data["success"]
    assert data["temperature_c"] > 30  # July 2PM in Madrid should be hot
    assert data["heat_discomfort_index"] > 0
    print(
        f"  ✓ Madrid July 2PM: {data['temperature_c']}°C, heat_idx={data['heat_discomfort_index']}")

    # Full day profile
    r = client.get("/api/weather-estimate?city=seville&month=8")
    data = r.get_json()
    assert len(data["hours"]) == 24
    temps = [h["temperature_c"] for h in data["hours"]]
    max_temp = max(temps)
    min_temp = min(temps)
    print(f"  ✓ Seville August: {min_temp}°C (min) → {max_temp}°C (max)")
    assert max_temp > 35  # Seville August is extreme

    # Winter check
    r = client.get("/api/weather-estimate?city=madrid&month=1&hour=3")
    data = r.get_json()
    assert data["temperature_c"] < 10
    print(f"  ✓ Madrid Jan 3AM: {data['temperature_c']}°C (cold)")

    # Invalid inputs
    r = client.get("/api/weather-estimate?city=paris&month=7")
    assert r.status_code == 400
    r = client.get("/api/weather-estimate?city=madrid&month=13")
    assert r.status_code == 400
    print(f"  ✓ Invalid inputs → 400")

    print("  PASSED ✓")


def test_optimize():
    separator("TEST 7: POST /api/optimize")

    # Get 4 Madrid attractions
    r = client.get("/api/attractions?city=madrid&min_priority=9&limit=4")
    attractions = r.get_json()["attractions"]
    ids = [a["id"] for a in attractions]
    names = [a["name"] for a in attractions]
    print(f"  Selected: {', '.join(names)}")

    # Optimize
    payload = {
        "start_latitude": 40.4200,
        "start_longitude": -3.7050,
        "date": "2025-07-15",
        "attraction_ids": ids,
        "preference_mode": "comfort",
        "start_hour": 9
    }
    r = client.post("/api/optimize", json=payload)
    data = r.get_json()

    assert r.status_code == 200
    assert data["success"]
    assert data["permutations_evaluated"] == 24  # 4! = 24
    assert len(data["timeline"]) == 4
    assert data["total_impact_score"] > 0
    assert data["_cached"] == False

    print(
        f"  ✓ Evaluated {data['permutations_evaluated']} permutations in {data['computation_time_ms']:.0f}ms")
    print(f"  ✓ Impact Score: {data['total_impact_score']:.4f}")
    print(f"  ✓ Timeline: {data['itinerary_start']} → {data['itinerary_end']}")

    for leg in data["timeline"]:
        print(f"    {leg['arrival_time']} │ {leg['attraction_name']:<35s} │ {leg['visit_duration_min']}min │ score={leg['impact_score']['total_score']:.3f}")

    # Test caching — second call should be cached
    r2 = client.post("/api/optimize", json=payload)
    data2 = r2.get_json()
    assert data2["_cached"] == True
    assert data2["total_impact_score"] == data["total_impact_score"]
    print(f"  ✓ Cache hit on repeated request")

    print("  PASSED ✓")


def test_optimize_5_attractions():
    separator("TEST 8: POST /api/optimize (5 attractions, Barcelona)")

    r = client.get("/api/attractions?city=barcelona&min_priority=9&limit=5")
    attractions = r.get_json()["attractions"]
    ids = [a["id"] for a in attractions]

    payload = {
        "start_latitude": 41.3870,
        "start_longitude": 2.1700,
        "date": "2025-05-20",
        "attraction_ids": ids,
        "preference_mode": "balanced",
        "start_hour": 9
    }
    r = client.post("/api/optimize", json=payload)
    data = r.get_json()

    assert data["permutations_evaluated"] == 120  # 5! = 120
    assert len(data["timeline"]) == 5

    route = " → ".join(leg["attraction_name"][:20] for leg in data["timeline"])
    print(f"  ✓ Route: {route}")
    print(f"  ✓ 120 permutations in {data['computation_time_ms']:.0f}ms")
    print(f"  ✓ Score: {data['total_impact_score']:.4f}")

    print("  PASSED ✓")


def test_optimize_errors():
    separator("TEST 9: POST /api/optimize — Error Handling")

    # Empty body
    r = client.post("/api/optimize")
    assert r.status_code == 400
    print(f"  ✓ Empty body → 400")

    # Invalid preference mode
    r = client.post("/api/optimize", json={
        "start_latitude": 40.42, "start_longitude": -3.70,
        "date": "2025-07-15", "attraction_ids": ["abc"],
        "preference_mode": "invalid"
    })
    assert r.status_code == 400
    print(f"  ✓ Invalid preference_mode → 400")

    # Empty attractions
    r = client.post("/api/optimize", json={
        "start_latitude": 40.42, "start_longitude": -3.70,
        "date": "2025-07-15", "attraction_ids": [],
    })
    assert r.status_code == 400
    print(f"  ✓ Empty attractions → 400")

    # Invalid date
    r = client.post("/api/optimize", json={
        "start_latitude": 40.42, "start_longitude": -3.70,
        "date": "not-a-date", "attraction_ids": ["abc"],
    })
    assert r.status_code == 400
    print(f"  ✓ Invalid date → 400")

    # Too many attractions
    r = client.post("/api/optimize", json={
        "start_latitude": 40.42, "start_longitude": -3.70,
        "date": "2025-07-15",
        "attraction_ids": ["a", "b", "c", "d", "e", "f", "g", "h"],
    })
    assert r.status_code == 400
    print(f"  ✓ >7 attractions → 400")

    print("  PASSED ✓")


def test_preference_comparison():
    separator("TEST 10: Preference Mode Comparison via API")

    r = client.get("/api/attractions?city=madrid&min_priority=9&limit=4")
    ids = [a["id"] for a in r.get_json()["attractions"]]

    results = {}
    for mode in ["comfort", "balanced", "fastest"]:
        # Clear cache between modes
        client.post("/api/cache/clear")

        r = client.post("/api/optimize", json={
            "start_latitude": 40.4200,
            "start_longitude": -3.7050,
            "date": "2025-07-15",
            "attraction_ids": ids,
            "preference_mode": mode,
            "start_hour": 9
        })
        data = r.get_json()
        results[mode] = data

        route = [leg["attraction_name"][:20] for leg in data["timeline"]]
        bd = data["impact_breakdown"]["components"]
        print(f"  [{mode:>8s}] score={data['total_impact_score']:.4f} "
              f"traffic={bd['traffic']:.3f} heat={bd['heat']:.3f} crowd={bd['crowd']:.3f}")
        print(f"             {' → '.join(route)}")

    print("  PASSED ✓")


def test_cache():
    separator("TEST 11: Cache Operations")

    # Clear
    r = client.post("/api/cache/clear")
    assert r.get_json()["success"]
    print(f"  ✓ Cache cleared")

    # Stats
    r = client.get("/api/cache/stats")
    stats = r.get_json()["cache"]
    assert stats["entries"] == 0
    print(f"  ✓ Cache empty: {stats}")

    # Make a request
    client.get("/api/weather-estimate?city=madrid&month=7&hour=14")

    # Second request should be cached (weather uses cache in traffic-estimate, not weather directly)
    # Let's use traffic-estimate which does cache
    client.get(
        "/api/traffic-estimate?"
        "origin_lat=40.4138&origin_lon=-3.6921"
        "&dest_lat=40.4180&dest_lon=-3.7143"
        "&city=Madrid&hour=9"
    )
    r1 = client.get(
        "/api/traffic-estimate?"
        "origin_lat=40.4138&origin_lon=-3.6921"
        "&dest_lat=40.4180&dest_lon=-3.7143"
        "&city=Madrid&hour=9"
    )
    data = r1.get_json()
    assert data.get("_cached") == True
    print(f"  ✓ Traffic estimate cached on second call")

    r = client.get("/api/cache/stats")
    stats = r.get_json()["cache"]
    print(f"  ✓ Cache stats: {stats}")

    print("  PASSED ✓")


def test_404():
    separator("TEST 12: Error Handling")

    r = client.get("/api/nonexistent")
    assert r.status_code == 404
    print(f"  ✓ Unknown endpoint → 404")

    r = client.put("/api/optimize")
    assert r.status_code == 405
    print(f"  ✓ Wrong method → 405")

    print("  PASSED ✓")


# ═══════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  SmartTrip AI — Phase 3 API Test Suite")
    print("=" * 70)

    t_start = time.perf_counter()

    test_health()
    test_api_docs()
    test_attractions_list()
    test_attraction_by_id()
    test_traffic_estimate()
    test_weather_estimate()
    test_optimize()
    test_optimize_5_attractions()
    test_optimize_errors()
    test_preference_comparison()
    test_cache()
    test_404()

    t_total = (time.perf_counter() - t_start) * 1000

    separator(f"ALL 12 TESTS PASSED ✓✓✓  ({t_total:.0f}ms)")
    print("  Phase 3 Backend & APIs: COMPLETE")
    print(f"{'═'*70}\n")

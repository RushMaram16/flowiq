"""
SmartTrip AI - Backend API Server
Flask implementation of the architecture spec's API layer.

Endpoints:
    POST /api/optimize         — Optimize itinerary (core endpoint)
    GET  /api/attractions      — List attractions by city
    GET  /api/attractions/<id> — Get single attraction details
    GET  /api/traffic-estimate — Estimate travel time between points
    GET  /api/weather-estimate — Get weather/heat forecast for city
    GET  /api/health           — Health check
    GET  /api/cache/stats      — Cache statistics

Production migration:
    Replace Flask with FastAPI, swap CacheStore with Redis,
    swap SQLite/CSV with PostgreSQL. All business logic stays identical.
"""

from functools import wraps
from dataclasses import asdict
from datetime import datetime, date
import time
import json
import os
import sys
from flask import Flask, request, jsonify
from engine.data_loader import get_data_store
from engine.travel_estimator import estimate_travel_time
from engine.optimizer import optimize_itinerary
from api.cache import (
    get_cache, traffic_cache_key, weather_cache_key, optimize_cache_key
)
from api.schemas import (
    OptimizeRequest, TrafficEstimateRequest, to_dict
)
from fastapi import FastAPI
app = FastAPI()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

VERSION = "1.0.0-mvp"
SUPPORTED_CITIES = ["madrid", "barcelona", "seville"]

# Initialize data store on first request
_initialized = False


def ensure_initialized():
    global _initialized
    if not _initialized:
        get_data_store()
        _initialized = True


# ── Rate Limiter (simple in-memory) ──────────────────────

class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        if client_ip not in self._requests:
            self._requests[client_ip] = []

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return False

        self._requests[client_ip].append(now)
        return True


rate_limiter = RateLimiter(max_requests=60, window_seconds=60)


def rate_limit(f):
    """Rate limiting decorator."""
    @wraps(f)
    def decorated(*args, **kwargs):
        client_ip = request.remote_addr or "unknown"
        if not rate_limiter.is_allowed(client_ip):
            return jsonify({
                "success": False,
                "error": "Rate limit exceeded. Max 60 requests per minute.",
                "code": 429
            }), 429
        return f(*args, **kwargs)
    return decorated


# ── Error Handlers ───────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Endpoint not found", "code": 404}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "error": "Method not allowed", "code": 405}), 405


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"success": False, "error": "Internal server error", "code": 500}), 500


# ═══════════════════════════════════════════════════════════
# POST /api/optimize — Core Optimization Endpoint
# ═══════════════════════════════════════════════════════════

@app.route("/api/optimize", methods=["POST"])
@rate_limit
def optimize():
    """
    Optimize itinerary for given attractions.

    Request body (JSON):
        start_latitude: float
        start_longitude: float
        date: str (ISO format YYYY-MM-DD)
        attraction_ids: list of str (UUIDs)
        preference_mode: str (comfort|fastest|balanced)
        start_hour: int (0-23, default 9)
    """
    ensure_initialized()
    t_start = time.perf_counter()

    # Parse request
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400

    try:
        req = OptimizeRequest(
            start_latitude=float(data.get("start_latitude", 0)),
            start_longitude=float(data.get("start_longitude", 0)),
            date=data.get("date", ""),
            attraction_ids=data.get("attraction_ids", []),
            preference_mode=data.get("preference_mode", "balanced"),
            start_hour=int(data.get("start_hour", 9)),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid input: {str(e)}"}), 400

    # Validate
    error = req.validate()
    if error:
        return jsonify({"success": False, "error": error}), 400

    # Check cache
    cache = get_cache()
    cache_key = optimize_cache_key(
        req.start_latitude, req.start_longitude,
        req.attraction_ids, "", req.date,
        req.start_hour, req.preference_mode
    )
    cached = cache.get(cache_key)
    if cached:
        cached["_cached"] = True
        return jsonify(cached), 200

    # Determine city from attraction IDs
    store = get_data_store()
    city = None
    for aid in req.attraction_ids:
        attr = store.get_attraction(aid)
        if attr:
            city = attr["city"]
            break

    if not city:
        return jsonify({"success": False, "error": "No valid attraction IDs found"}), 400

    # Parse date
    try:
        visit_date = datetime.fromisoformat(req.date)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date format"}), 400

    # Run optimizer
    try:
        result = optimize_itinerary(
            req.start_latitude, req.start_longitude,
            req.attraction_ids, city, visit_date,
            req.start_hour, req.preference_mode
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Optimization failed: {str(e)}"}), 500

    response = result.to_dict()
    response["success"] = True
    response["city"] = city
    response["_cached"] = False

    # Cache result (15 min TTL)
    cache.set(cache_key, response)

    return jsonify(response), 200


# ═══════════════════════════════════════════════════════════
# GET /api/attractions — List Attractions
# ═══════════════════════════════════════════════════════════

@app.route("/api/attractions", methods=["GET"])
@rate_limit
def list_attractions():
    """
    List attractions for a city.

    Query params:
        city: str (required) — madrid, barcelona, or seville
        category: str (optional) — filter by category
        min_priority: float (optional) — minimum priority score
        limit: int (optional) — max results (default 50)
    """
    ensure_initialized()
    store = get_data_store()

    city = request.args.get("city", "").strip()
    if not city:
        return jsonify({"success": False, "error": "city parameter is required"}), 400

    # Normalize city name
    city_map = {
        "madrid": "Madrid", "barcelona": "Barcelona", "seville": "Seville",
        "sevilla": "Seville",
    }
    city_normalized = city_map.get(city.lower())
    if not city_normalized:
        return jsonify({
            "success": False,
            "error": f"Unsupported city. Choose from: {list(city_map.keys())}"
        }), 400

    attractions = store.get_attractions_by_city(city_normalized)

    # Apply filters
    category = request.args.get("category", "").strip().lower()
    if category:
        attractions = [a for a in attractions if a.get(
            "category", "").lower() == category]

    min_priority = request.args.get("min_priority", type=float)
    if min_priority is not None:
        attractions = [a for a in attractions if a.get(
            "priority_score", 0) >= min_priority]

    # Sort by priority descending
    attractions.sort(key=lambda a: a.get("priority_score", 0), reverse=True)

    # Limit
    limit = request.args.get("limit", 50, type=int)
    attractions = attractions[:limit]

    return jsonify({
        "success": True,
        "city": city_normalized,
        "count": len(attractions),
        "attractions": attractions,
    }), 200


# ═══════════════════════════════════════════════════════════
# GET /api/attractions/<id> — Get Single Attraction
# ═══════════════════════════════════════════════════════════

@app.route("/api/attractions/<attraction_id>", methods=["GET"])
@rate_limit
def get_attraction(attraction_id):
    """Get a single attraction by ID."""
    ensure_initialized()
    store = get_data_store()

    attr = store.get_attraction(attraction_id)
    if not attr:
        return jsonify({"success": False, "error": "Attraction not found"}), 404

    return jsonify({"success": True, "attraction": attr}), 200


# ═══════════════════════════════════════════════════════════
# GET /api/traffic-estimate — Travel Time Estimation
# ═══════════════════════════════════════════════════════════

@app.route("/api/traffic-estimate", methods=["GET"])
@rate_limit
def traffic_estimate():
    """
    Estimate travel time between two points.

    Query params:
        origin_lat, origin_lon: float (required)
        dest_lat, dest_lon: float (required)
        city: str (required)
        hour: int (optional, default 12)
        day_type: str (optional, weekday|weekend, default weekday)
        month: int (optional, default 6)
    """
    ensure_initialized()

    try:
        req = TrafficEstimateRequest(
            origin_lat=float(request.args.get("origin_lat", 0)),
            origin_lon=float(request.args.get("origin_lon", 0)),
            dest_lat=float(request.args.get("dest_lat", 0)),
            dest_lon=float(request.args.get("dest_lon", 0)),
            city=request.args.get("city", ""),
            hour=int(request.args.get("hour", 12)),
            day_type=request.args.get("day_type", "weekday"),
            month=int(request.args.get("month", 6)),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid parameters: {e}"}), 400

    error = req.validate()
    if error:
        return jsonify({"success": False, "error": error}), 400

    # Check cache
    cache = get_cache()
    cache_key = traffic_cache_key(
        req.origin_lat, req.origin_lon,
        req.dest_lat, req.dest_lon,
        req.city, req.hour, req.day_type, req.month
    )
    cached = cache.get(cache_key)
    if cached:
        cached["_cached"] = True
        return jsonify(cached), 200

    result = estimate_travel_time(
        req.origin_lat, req.origin_lon,
        req.dest_lat, req.dest_lon,
        req.city, req.hour, req.day_type, req.month
    )

    response = {
        "success": True,
        **result,
        "_cached": False,
    }

    cache.set(cache_key, response)
    return jsonify(response), 200


# ═══════════════════════════════════════════════════════════
# GET /api/weather-estimate — Weather & Heat Forecast
# ═══════════════════════════════════════════════════════════

@app.route("/api/weather-estimate", methods=["GET"])
@rate_limit
def weather_estimate():
    """
    Get hourly weather/heat discomfort for a city and month.

    Query params:
        city: str (required)
        month: int (required, 1-12)
        hour: int (optional) — if provided, returns single hour; otherwise all 24
    """
    ensure_initialized()
    store = get_data_store()

    city = request.args.get("city", "").strip().lower()
    if not city or city not in SUPPORTED_CITIES:
        return jsonify({
            "success": False,
            "error": f"city must be one of: {SUPPORTED_CITIES}"
        }), 400

    try:
        month = int(request.args.get("month", 0))
    except ValueError:
        return jsonify({"success": False, "error": "month must be an integer"}), 400

    if not (1 <= month <= 12):
        return jsonify({"success": False, "error": "month must be between 1 and 12"}), 400

    hour_param = request.args.get("hour")

    if hour_param is not None:
        # Single hour
        try:
            hour = int(hour_param)
        except ValueError:
            return jsonify({"success": False, "error": "hour must be an integer"}), 400

        weather = store.get_weather(city, month, hour)
        return jsonify({
            "success": True,
            "city": city,
            "month": month,
            "hour": hour,
            "temperature_c": weather["temperature"],
            "heat_discomfort_index": weather["heat_discomfort"],
        }), 200
    else:
        # Full day profile
        hours = []
        for h in range(24):
            w = store.get_weather(city, month, h)
            hours.append({
                "hour": h,
                "temperature_c": w["temperature"],
                "heat_discomfort_index": w["heat_discomfort"],
            })

        return jsonify({
            "success": True,
            "city": city,
            "month": month,
            "hours": hours,
        }), 200


# ═══════════════════════════════════════════════════════════
# GET /api/health — Health Check
# ═══════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    ensure_initialized()
    store = get_data_store()
    cache = get_cache()

    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "attractions_loaded": len(store.attractions_by_id),
        "cities": SUPPORTED_CITIES,
        "cache_stats": cache.stats,
    }), 200


# ═══════════════════════════════════════════════════════════
# GET /api/cache/stats — Cache Statistics
# ═══════════════════════════════════════════════════════════

@app.route("/api/cache/stats", methods=["GET"])
def cache_stats():
    """Return cache hit/miss statistics."""
    cache = get_cache()
    return jsonify({"success": True, "cache": cache.stats}), 200


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    """Clear all cached entries."""
    cache = get_cache()
    cache.clear()
    return jsonify({"success": True, "message": "Cache cleared"}), 200


# ═══════════════════════════════════════════════════════════
# ROOT / API DOCS
# ═══════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
@app.route("/api", methods=["GET"])
def api_docs():
    """API documentation endpoint."""
    return jsonify({
        "name": "SmartTrip AI API",
        "version": VERSION,
        "description": "Intelligent itinerary optimization for Spain tourism",
        "endpoints": {
            "POST /api/optimize": {
                "description": "Optimize itinerary for given attractions",
                "body": {
                    "start_latitude": "float",
                    "start_longitude": "float",
                    "date": "YYYY-MM-DD",
                    "attraction_ids": ["uuid1", "uuid2", "..."],
                    "preference_mode": "comfort|fastest|balanced",
                    "start_hour": "int (0-23, default 9)"
                }
            },
            "GET /api/attractions": {
                "description": "List attractions by city",
                "params": "?city=madrid&category=indoor&min_priority=7&limit=20"
            },
            "GET /api/attractions/<id>": {
                "description": "Get single attraction by UUID"
            },
            "GET /api/traffic-estimate": {
                "description": "Estimate travel time between two points",
                "params": "?origin_lat=40.41&origin_lon=-3.70&dest_lat=40.42&dest_lon=-3.69&city=madrid&hour=9"
            },
            "GET /api/weather-estimate": {
                "description": "Get hourly weather/heat discomfort",
                "params": "?city=madrid&month=7&hour=14"
            },
            "GET /api/health": {
                "description": "Health check"
            },
            "GET /api/cache/stats": {
                "description": "Cache statistics"
            },
        },
        "supported_cities": SUPPORTED_CITIES,
    }), 200


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"SmartTrip AI API v{VERSION}")
    print(f"Starting server on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)

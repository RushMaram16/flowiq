"""
SmartTrip AI - Backend API Server
Flask implementation of the architecture spec's API layer.

Endpoints:
    POST /api/optimize              — Optimize itinerary (core endpoint)
    GET  /api/attractions           — List attractions by city
    GET  /api/attractions/<id>      — Get single attraction details
    GET  /api/traffic-estimate      — Estimate travel time between points
    GET  /api/weather-estimate      — Get weather/heat forecast for city
    GET  /api/cities                — List all supported cities
    GET  /api/health                — Health check
    GET  /api/cache/stats           — Cache statistics
"""

from functools import wraps
from dataclasses import asdict
from datetime import datetime, date
import time
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_from_directory
from engine.data_loader import get_data_store
from engine.travel_estimator import estimate_travel_time
from engine.optimizer import optimize_itinerary
from api.cache import (
    get_cache, traffic_cache_key, weather_cache_key, optimize_cache_key
)
from api.schemas import (
    OptimizeRequest, TrafficEstimateRequest, to_dict
)


# ═══════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.config["JSON_SORT_KEYS"] = False

VERSION = "1.1.0-mvp"

# Known city center coordinates (used in /api/cities response)
CITY_CENTERS = {
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

_initialized = False


def ensure_initialized():
    global _initialized
    if not _initialized:
        get_data_store()
        _initialized = True


def _get_supported_cities() -> list:
    """Dynamically derive supported cities from attractions in the dataset."""
    store = get_data_store()
    return sorted(set(
        str(a.get("city", "")).strip()
        for a in store.attractions_by_id.values()
        if a.get("city")
    ), key=str.lower)


# ── Rate Limiter ──────────────────────────────────────────

class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict = {}

    def is_allowed(self, client_ip: str) -> bool:
        now = time.time()
        if client_ip not in self._requests:
            self._requests[client_ip] = []
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < self.window
        ]
        if len(self._requests[client_ip]) >= self.max_requests:
            return False
        self._requests[client_ip].append(now)
        return True


rate_limiter = RateLimiter(max_requests=60, window_seconds=60)


def rate_limit(f):
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
    if not request.path.startswith("/api"):
        return send_from_directory(FRONTEND_DIR, "index.html")
    return jsonify({"success": False, "error": "Endpoint not found", "code": 404}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "error": "Method not allowed", "code": 405}), 405


@app.errorhandler(500)
def internal_error(e):
    return jsonify({"success": False, "error": "Internal server error", "code": 500}), 500


# ═══════════════════════════════════════════════════════════
# GET /api/cities — List All Supported Cities
# ═══════════════════════════════════════════════════════════

@app.route("/api/cities", methods=["GET"])
@rate_limit
def list_cities():
    """
    List all cities available in the dataset.

    Returns city name, slug, coordinates, and attraction count.
    """
    ensure_initialized()
    store = get_data_store()

    city_map: dict = {}
    for a in store.attractions_by_id.values():
        city_raw = str(a.get("city", "")).strip()
        if not city_raw:
            continue
        slug = city_raw.lower()
        if slug not in city_map:
            city_map[slug] = {
                "name": city_raw,
                "slug": slug,
                "latitude": CITY_CENTERS.get(slug, (a["latitude"], a["longitude"]))[0],
                "longitude": CITY_CENTERS.get(slug, (a["latitude"], a["longitude"]))[1],
                "attraction_count": 0,
            }
        city_map[slug]["attraction_count"] += 1

    cities = sorted(city_map.values(), key=lambda c: c["name"])

    return jsonify({
        "success": True,
        "cities": cities,
        "count": len(cities),
    }), 200


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
        travel_mode: str (driving|walking, default driving)
    """
    ensure_initialized()
    t_start = time.perf_counter()

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
            travel_mode=data.get("travel_mode", "driving"),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "error": f"Invalid input: {str(e)}"}), 400

    error = req.validate()
    if error:
        return jsonify({"success": False, "error": error}), 400

    # Check cache
    cache = get_cache()
    cache_key = optimize_cache_key(
        req.start_latitude, req.start_longitude,
        req.attraction_ids, "", req.date,
        req.start_hour, req.preference_mode, req.travel_mode,
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

    try:
        visit_date = datetime.fromisoformat(req.date)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date format"}), 400

    try:
        result = optimize_itinerary(
            req.start_latitude, req.start_longitude,
            req.attraction_ids, city, visit_date,
            req.start_hour, req.preference_mode, req.travel_mode,
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Optimization failed: {str(e)}"}), 500

    response = result.to_dict()
    response["success"] = True
    response["city"] = city
    response["travel_mode"] = req.travel_mode
    response["_cached"] = False

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
        city: str (required)
        category: str (optional)
        min_priority: float (optional)
        limit: int (optional, default 50)
    """
    ensure_initialized()
    store = get_data_store()

    city_param = request.args.get("city", "").strip()
    if not city_param:
        return jsonify({"success": False, "error": "city parameter is required"}), 400

    # Find the exact city name (case-insensitive match against dataset)
    city_param_lower = city_param.lower()
    all_cities = store.get_supported_cities()

    # Build a normalisation map: lowercase slug → exact name in dataset
    city_norm_map = {c.lower(): c for c in all_cities}

    # Also handle common aliases
    aliases = {
        "sevilla": "seville",
        "san sebastian": "san sebastian",
        "donostia": "san sebastian",
        "sant sebastia": "san sebastian",
        "bilbo": "bilbao",
    }
    city_param_lower = aliases.get(city_param_lower, city_param_lower)
    city_normalized = city_norm_map.get(city_param_lower)

    if not city_normalized:
        return jsonify({
            "success": False,
            "error": f"Unsupported city '{city_param}'. Available: {list(city_norm_map.keys())}",
        }), 400

    attractions = store.get_attractions_by_city(city_normalized)

    # Apply filters
    category = request.args.get("category", "").strip().lower()
    if category:
        attractions = [a for a in attractions if a.get("category", "").lower() == category]

    min_priority = request.args.get("min_priority", type=float)
    if min_priority is not None:
        attractions = [a for a in attractions if a.get("priority_score", 0) >= min_priority]

    attractions.sort(key=lambda a: a.get("priority_score", 0), reverse=True)

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
        travel_mode: str (optional, driving|walking, default driving)
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

    travel_mode = request.args.get("travel_mode", "driving")

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
        req.city, req.hour, req.day_type, req.month,
        travel_mode=travel_mode,
    )

    response = {"success": True, **result, "_cached": False}
    cache.set(cache_key, response, ttl=300)  # 5 min for traffic data
    return jsonify(response), 200


# ═══════════════════════════════════════════════════════════
# GET /api/weather-estimate — Weather & Heat Forecast
# ═══════════════════════════════════════════════════════════

@app.route("/api/weather-estimate", methods=["GET"])
@rate_limit
def weather_estimate():
    """
    Get hourly weather/heat discomfort for a city.

    Query params:
        city: str (required)
        month: int (1-12) — used when no date given
        date: str (YYYY-MM-DD, optional) — enables live Open-Meteo data
        lat: float (optional, required when date is provided)
        lon: float (optional, required when date is provided)
        hour: int (optional) — single hour; otherwise returns full 24h profile
    """
    ensure_initialized()
    store = get_data_store()

    city = request.args.get("city", "").strip().lower()
    all_cities_lower = [c.lower() for c in store.get_supported_cities()]

    if not city or city not in all_cities_lower:
        return jsonify({
            "success": False,
            "error": f"city must be one of: {all_cities_lower}"
        }), 400

    # Resolve date → month
    date_str = request.args.get("date", "").strip()
    lat_param = request.args.get("lat", type=float)
    lon_param = request.args.get("lon", type=float)

    if date_str:
        try:
            parsed_date = datetime.fromisoformat(date_str)
            month = parsed_date.month
        except ValueError:
            return jsonify({"success": False, "error": "date must be YYYY-MM-DD"}), 400
    else:
        try:
            month = int(request.args.get("month", 0))
        except ValueError:
            return jsonify({"success": False, "error": "month must be an integer"}), 400
        if not (1 <= month <= 12):
            return jsonify({"success": False, "error": "month must be 1-12"}), 400

    # Single hour or full day
    hour_param = request.args.get("hour")
    if hour_param is not None:
        try:
            hour = int(hour_param)
        except ValueError:
            return jsonify({"success": False, "error": "hour must be an integer"}), 400
        weather = store.get_weather(city, month, hour, date_str or None, lat_param, lon_param)
        return jsonify({
            "success": True,
            "city": city,
            "month": month,
            "hour": hour,
            "temperature_c": weather["temperature"],
            "heat_discomfort_index": weather["heat_discomfort"],
            "data_source": weather.get("source", "static"),
        }), 200

    # Full 24-hour profile
    hours = []
    data_source = "static"
    for h in range(24):
        w = store.get_weather(city, month, h, date_str or None, lat_param, lon_param)
        if w.get("source") == "open_meteo_live":
            data_source = "open_meteo_live"
        entry = {
            "hour": h,
            "temperature_c": w["temperature"],
            "heat_discomfort_index": w["heat_discomfort"],
        }
        if "uv_index" in w:
            entry["uv_index"] = w["uv_index"]
        if "precip_prob" in w:
            entry["precipitation_probability"] = w["precip_prob"]
        hours.append(entry)

    return jsonify({
        "success": True,
        "city": city,
        "month": month,
        "date": date_str or None,
        "hours": hours,
        "data_source": data_source,
    }), 200


# ═══════════════════════════════════════════════════════════
# GET /api/health — Health Check
# ═══════════════════════════════════════════════════════════

@app.route("/api/health", methods=["GET"])
def health():
    ensure_initialized()
    store = get_data_store()
    cache = get_cache()
    supported_cities = _get_supported_cities()

    # Check which API clients are active
    api_clients = list(getattr(store, "_api_clients", {}).keys())

    return jsonify({
        "status": "healthy",
        "version": VERSION,
        "attractions_loaded": len(store.attractions_by_id),
        "cities": supported_cities,
        "city_count": len(supported_cities),
        "api_clients": api_clients,
        "cache_stats": cache.stats,
    }), 200


# ═══════════════════════════════════════════════════════════
# GET /api/cache/stats — Cache Statistics
# ═══════════════════════════════════════════════════════════

@app.route("/api/cache/stats", methods=["GET"])
def cache_stats():
    cache = get_cache()
    return jsonify({"success": True, "cache": cache.stats}), 200


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    cache = get_cache()
    cache.clear()
    return jsonify({"success": True, "message": "Cache cleared"}), 200


# ═══════════════════════════════════════════════════════════
# ROOT / API DOCS
# ═══════════════════════════════════════════════════════════

@app.route("/")
def serve_frontend():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api", methods=["GET"])
def api_docs():
    ensure_initialized()
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
                    "start_hour": "int (0-23, default 9)",
                    "travel_mode": "driving|walking (default driving)",
                }
            },
            "GET /api/cities": {
                "description": "List all supported cities with coordinates and attraction counts"
            },
            "GET /api/attractions": {
                "description": "List attractions by city",
                "params": "?city=granada&category=landmark&min_priority=7&limit=20"
            },
            "GET /api/attractions/<id>": {
                "description": "Get single attraction by UUID"
            },
            "GET /api/traffic-estimate": {
                "description": "Estimate travel time between two points",
                "params": "?origin_lat=40.41&origin_lon=-3.70&dest_lat=40.42&dest_lon=-3.69&city=madrid&hour=9&travel_mode=driving"
            },
            "GET /api/weather-estimate": {
                "description": "Get hourly weather/heat discomfort (live when date+lat+lon provided)",
                "params": "?city=granada&date=2026-07-15&lat=37.18&lon=-3.60"
            },
            "GET /api/health": {
                "description": "Health check — shows active API clients"
            },
        },
        "supported_cities": _get_supported_cities(),
    }), 200


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"SmartTrip AI API v{VERSION}")
    print(f"Starting server on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)

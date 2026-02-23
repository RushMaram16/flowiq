"""
SmartTrip AI - API Schemas
Request/response models for all endpoints.
Mirrors the architecture doc's API contract exactly.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import date


# ═══════════════════════════════════════════════════════════
# POST /api/optimize — Request & Response
# ═══════════════════════════════════════════════════════════

@dataclass
class OptimizeRequest:
    """POST /api/optimize input"""
    start_latitude: float
    start_longitude: float
    date: str                           # ISO format: "2025-07-15"
    attraction_ids: List[str]           # List of UUID strings
    preference_mode: str = "balanced"   # comfort | fastest | balanced
    start_hour: int = 9                 # Hour to start (0-23)

    def validate(self) -> Optional[str]:
        """Returns error message if invalid, None if valid."""
        if not (-90 <= self.start_latitude <= 90):
            return "start_latitude must be between -90 and 90"
        if not (-180 <= self.start_longitude <= 180):
            return "start_longitude must be between -180 and 180"
        if not self.attraction_ids:
            return "attraction_ids must not be empty"
        if len(self.attraction_ids) > 7:
            return "Maximum 7 attractions per itinerary"
        if self.preference_mode not in ("comfort", "fastest", "balanced"):
            return "preference_mode must be comfort, fastest, or balanced"
        if not (0 <= self.start_hour <= 23):
            return "start_hour must be between 0 and 23"
        try:
            date.fromisoformat(self.date)
        except (ValueError, TypeError):
            return "date must be in ISO format (YYYY-MM-DD)"
        return None


@dataclass
class LegResponse:
    """Single leg of the optimized timeline."""
    attraction_id: str
    attraction_name: str
    travel_from: str
    departure_time: str             # HH:MM
    arrival_time: str               # HH:MM
    travel_duration_min: float
    travel_distance_km: float
    visit_start: str                # HH:MM
    visit_end: str                  # HH:MM
    visit_duration_min: int
    impact_score: Dict[str, Any]


@dataclass
class OptimizeResponse:
    """POST /api/optimize output"""
    success: bool
    ordered_route: List[Dict[str, Any]]
    timeline: List[Dict[str, Any]]
    total_travel_time_min: float
    total_visit_time_min: float
    total_duration_min: float
    total_impact_score: float
    impact_breakdown: Dict[str, Any]
    itinerary_start: str
    itinerary_end: str
    permutations_evaluated: int
    computation_time_ms: float
    explanation: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# GET /api/attractions — Response
# ═══════════════════════════════════════════════════════════

@dataclass
class AttractionResponse:
    """Single attraction entry."""
    id: str
    name: str
    name_es: str
    city: str
    latitude: float
    longitude: float
    category: str
    zone: str
    average_visit_duration: int
    ideal_time_start: int
    ideal_time_end: int
    peak_hours: List[int]
    heat_sensitive: bool
    sunset_sensitive: bool
    priority_score: float
    description: str = ""
    opening_hours: str = ""
    fee: str = ""


@dataclass
class AttractionsListResponse:
    """GET /api/attractions output"""
    success: bool
    city: str
    count: int
    attractions: List[Dict[str, Any]]


# ═══════════════════════════════════════════════════════════
# GET /api/traffic-estimate — Response
# ═══════════════════════════════════════════════════════════

@dataclass
class TrafficEstimateRequest:
    origin_lat: float
    origin_lon: float
    dest_lat: float
    dest_lon: float
    city: str
    hour: int = 12
    day_type: str = "weekday"
    month: int = 6

    def validate(self) -> Optional[str]:
        if not self.city:
            return "city is required"
        if not (0 <= self.hour <= 23):
            return "hour must be between 0 and 23"
        if self.day_type not in ("weekday", "weekend"):
            return "day_type must be weekday or weekend"
        return None


@dataclass
class TrafficEstimateResponse:
    success: bool
    distance_km: float
    duration_minutes: float
    traffic_index: float
    speed_kmh: float
    free_flow_minutes: float
    origin_zone: str
    dest_zone: str
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# GET /api/weather-estimate — Response
# ═══════════════════════════════════════════════════════════

@dataclass
class WeatherEstimateResponse:
    success: bool
    city: str
    month: int
    hours: List[Dict[str, Any]]     # [{hour, temperature, heat_discomfort}]
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# GET /api/health — Response
# ═══════════════════════════════════════════════════════════

@dataclass
class HealthResponse:
    status: str
    version: str
    attractions_loaded: int
    cities: List[str]


# ═══════════════════════════════════════════════════════════
# Error Response
# ═══════════════════════════════════════════════════════════

@dataclass
class ErrorResponse:
    success: bool = False
    error: str = ""
    code: int = 400


def to_dict(obj) -> dict:
    """Convert dataclass to dict."""
    return asdict(obj)

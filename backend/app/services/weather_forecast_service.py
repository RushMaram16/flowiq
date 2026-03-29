import os
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, List


FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
GEOCODE_URL = "https://api.openweathermap.org/geo/1.0/direct"

# --- Scoring + messaging ------------------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def weather_score(
    temp: float,
    feels_like: float,
    wind: float,
    humidity: int,
    pop: float,
) -> int:
    """
    0–100 outdoor comfort score (higher = better)
    temp/feels_like: °C
    wind: m/s
    humidity: %
    pop: 0..1 (probability of precipitation)
    """
    score = 100

    # Comfort feels-like centered around ~21°C
    score -= abs(feels_like - 21) * 2.0

    # Wind penalty
    score -= wind * 1.5

    # Humidity penalty above 60
    score -= max(0, humidity - 60) * 0.5

    # Rain chance penalty (0..1)
    score -= _clamp(pop, 0.0, 1.0) * 40.0

    return int(_clamp(score, 0, 100))


def rating_fn(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Okay"
    return "Poor"


def make_sentence(feels_like: float, desc: str, pop: float, score: int) -> str:
    rain_pct = int(round(_clamp(pop, 0.0, 1.0) * 100))
    rating = rating_fn(score)

    if score >= 85:
        vibe = "Perfect for sightseeing and outdoor plans."
    elif score >= 70:
        vibe = "Great for exploring—minor weather discomfort possible."
    elif score >= 50:
        vibe = "Decent, but plan smart—some conditions may be annoying."
    else:
        vibe = "Not ideal for outdoor plans—consider indoor activities."

    return f"{rating} weather: feels like {round(feels_like,1)}°C, {desc}, rain chance {rain_pct}%. {vibe}"


# --- OpenWeather fetch --------------------------------------------------------

def _require_key() -> str:
    key = os.getenv("OPENWEATHER_API_KEY")
    if not key:
        raise RuntimeError("OPENWEATHER_API_KEY not set (put it in backend/.env)")
    return key


def fetch_forecast_by_coords(lat: float, lon: float) -> Dict[str, Any]:
    key = _require_key()

    params = {
        "lat": lat,
        "lon": lon,
        "appid": key,
        "units": "metric",
    }

    with httpx.Client(timeout=20) as client:
        r = client.get(FORECAST_URL, params=params)

    if r.status_code != 200:
        # Return a helpful payload instead of crashing
        return {"error_from_openweather": r.text, "status_code": r.status_code}

    return r.json()


# --- Main function used by app/main.py ----------------------------------------

def geocode_place(place: str, city_hint: str | None = None) -> Dict[str, Any]:
    key = _require_key()
    """
    Convert a place name into lat/lon using OpenWeather Geocoding API.
    Example: place="Plaza Mayor", city_hint="madrid"
    """
    q = place.strip()
    if city_hint:
        q = f"{q}, {city_hint}, ES"  # keep it focused on Spain

    params = {
        "q": q,
        "limit": 1,
        "appid": key,        
    }

    with httpx.Client(timeout=20) as client:
        r = client.get(GEOCODE_URL, params=params)

    if r.status_code != 200:
        raise RuntimeError(f"Geocode error: {r.status_code} {r.text}")

    arr = r.json()
    if not arr:
        raise RuntimeError(f"No geocode results for '{q}'")

    top = arr[0]
    lat = float(top["lat"])
    lon = float(top["lon"])

    name = top.get("name") or place
    state = top.get("state")
    country = top.get("country")

    label = name
    if state:
        label += f", {state}"
    if country:
        label += f", {country}"

    return {"lat": lat, "lon": lon, "label": label}
def pick_best_and_worst_time(
    lat: float,
    lon: float,
    hours_ahead: int = 24,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Picks the best time slot within the next `hours_ahead` hours using OpenWeather 3-hour forecast.

    Returns:
      - best_time_utc, best_time_local, best_weather_score, best_summary (sentence)
      - worst_time_utc, worst_time_local, worst_weather_score, worst_summary (sentence)
    """
    hours_ahead = max(3, min(int(hours_ahead), 120))  # forecast supports up to ~5 days

    data = fetch_forecast_by_coords(lat, lon)
    if "error_from_openweather" in data:
        return data

    tz_offset = int(data.get("city", {}).get("timezone", 0))
    now_utc = datetime.now(timezone.utc)
    end_utc = now_utc + timedelta(hours=hours_ahead)

    best: Optional[Dict[str, Any]] = None
    worst: Optional[Dict[str, Any]] = None

    items: List[Dict[str, Any]] = data.get("list", [])

    for item in items:
        dt_utc = datetime.fromtimestamp(int(item["dt"]), tz=timezone.utc)

        if dt_utc < now_utc or dt_utc > end_utc:
            continue

        main = item.get("main", {})
        weather = (item.get("weather") or [{}])[0]
        wind = item.get("wind", {})

        temp = float(main.get("temp", 0.0))
        feels = float(main.get("feels_like", temp))
        humidity = int(main.get("humidity", 0))
        desc = str(weather.get("description", "")).strip() or "weather"
        wind_ms = float(wind.get("speed", 0.0))
        pop = float(item.get("pop", 0.0))  # 0..1 (may be missing)

        score = weather_score(temp=temp, feels_like=feels, wind=wind_ms, humidity=humidity, pop=pop)

        slot = {
            "dt_utc": dt_utc,
            "temp": temp,
            "feels": feels,
            "humidity": humidity,
            "wind": wind_ms,
            "desc": desc,
            "pop": _clamp(pop, 0.0, 1.0),
            "score": score,
        }

        if best is None or slot["score"] > best["score"]:
            best = slot
        if worst is None or slot["score"] < worst["score"]:
            worst = slot

    if best is None:
        return {"error": "No forecast slots found in the given time window."}

    def to_local_iso(dt: datetime) -> str:
        # convert by timezone offset seconds
        return (dt + timedelta(seconds=tz_offset)).replace(tzinfo=None).isoformat(timespec="minutes")

    best_sentence = make_sentence(best["feels"], best["desc"], best["pop"], best["score"])
    worst_sentence = make_sentence(worst["feels"], worst["desc"], worst["pop"], worst["score"]) if worst else best_sentence

    return {
        "location": label or f"{lat},{lon}",

        "best_time_utc": best["dt_utc"].isoformat(timespec="minutes"),
        "best_time_local": to_local_iso(best["dt_utc"]),
        "best_weather_score": int(best["score"]),
        "best_summary": best_sentence,

        "worst_time_utc": worst["dt_utc"].isoformat(timespec="minutes") if worst else None,
        "worst_time_local": to_local_iso(worst["dt_utc"]) if worst else None,
        "worst_weather_score": int(worst["score"]) if worst else int(best["score"]),
        "worst_summary": worst_sentence,
    }

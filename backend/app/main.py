from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.services.weather_forecast_service import pick_best_and_worst_time, geocode_place

load_dotenv()

app = FastAPI(title="FlowIQ API", version="1.1.0")

# Preset city-center coordinates for dropdown support
CITY_COORDS = {
    "barcelona": (41.3851, 2.1734),
    "madrid": (40.4168, -3.7038),
    "seville": (37.3891, -5.9845),
    "sevilla": (37.3891, -5.9845),
}

@app.get("/")
def root():
    return {"status": "FlowIQ backend running"}

class BestTimeRequest(BaseModel):
    # Provide either city OR lat/lon (lat/lon overrides city if present)
    city: Optional[str] = "barcelona"
    place: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    hours_ahead: int = 24

@app.post("/weather/best-time")
def weather_best_time(req: BestTimeRequest):
    # If lat/lon provided, use exact location
    if req.lat is not None and req.lon is not None:
        result = pick_best_and_worst_time(
            lat=req.lat,
            lon=req.lon,
            hours_ahead=req.hours_ahead,
            label=req.city or "custom location"
        )
    elif req.place:
       geo = geocode_place(req.place, req.city)
       result = pick_best_and_worst_time(
           lat=geo["lat"],
           lon=geo["lon"],
           hours_ahead=req.hours_ahead,
           label=geo["label"],
       )
    else:
        # Otherwise use city preset
        if not req.city:
            raise HTTPException(status_code=400, detail="Provide either city or lat/lon")

        key = req.city.strip().lower()
        if key not in CITY_COORDS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported city '{req.city}'. Use one of: {sorted(set(CITY_COORDS.keys()))}"
            )

        lat, lon = CITY_COORDS[key]
        result = pick_best_and_worst_time(
            lat=lat,
            lon=lon,
            hours_ahead=req.hours_ahead,
            label=req.city
        )

    if not result:
        raise HTTPException(status_code=404, detail="Forecast unavailable")

    return result

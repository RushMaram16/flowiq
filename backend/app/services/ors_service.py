import os
import requests
from dotenv import load_dotenv

load_dotenv()

ORS_API_KEY = os.getenv("ORS_API_KEY")
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car"


def geocode_place(place_name: str):
    headers = {
        "Authorization": ORS_API_KEY
    }

    params = {
        "text": place_name,
        "size": 1
    }

    response = requests.get(ORS_GEOCODE_URL, headers=headers, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()
    features = data.get("features", [])

    if not features:
        raise ValueError(f"Could not geocode place: {place_name}")

    coordinates = features[0]["geometry"]["coordinates"]  # [lon, lat]
    return coordinates


def get_route(origin: str, destination: str, mode: str = "driving-car"):
    origin_coords = geocode_place(origin)
    destination_coords = geocode_place(destination)

    url = f"https://api.openrouteservice.org/v2/directions/{mode}"

    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type": "application/json"
    }

    body = {
        "coordinates": [
            origin_coords,
            destination_coords
        ]
    }

    response = requests.post(url, headers=headers, json=body, timeout=20)
    response.raise_for_status()

    data = response.json()
    route = data["routes"][0]["summary"]

    distance_m = route["distance"]
    duration_s = route["duration"]

    return {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "distance_km": round(distance_m / 1000, 2),
        "duration_min": round(duration_s / 60, 2)
    }

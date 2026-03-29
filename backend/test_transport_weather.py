from app.models.transport_models import ItineraryStop
from app.services.transport_service import build_transport_plan

itinerary = [
    ItineraryStop(place="Sagrada Familia, Barcelona", visit_start="09:00", visit_end="10:30"),
    ItineraryStop(place="Park Guell, Barcelona", visit_start="11:00", visit_end="12:30"),
    ItineraryStop(place="Casa Batllo, Barcelona", visit_start="13:00", visit_end="14:00")
]

weather_data = {
    "condition": "rainy",
    "temperature_c": 12,
    "wind_kph": 18
}

plan = build_transport_plan(
    itinerary=itinerary,
    allow_walking=True,
    start_point="Hotel Arts Barcelona, Barcelona",
    weather_data=weather_data
)

print(plan)


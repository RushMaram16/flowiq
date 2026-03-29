from app.services.data_collection_service import append_transport_row
from app.services.ors_service import get_route

modes = ["driving-car", "foot-walking", "cycling-regular"]

for m in modes:
    route = get_route(
        "Sagrada Familia, Barcelona",
        "Park Guell, Barcelona",
        mode=m
    )

    append_transport_row(
        city="Barcelona",
        origin=route["origin"],
        destination=route["destination"],
        mode=route["mode"],
        distance_km=route["distance_km"],
        duration_min=route["duration_min"],
        cost_estimate=12.0 if m == "driving-car" else 0.0,
        weather_source="multi_mode_test"
    )

    print(route)


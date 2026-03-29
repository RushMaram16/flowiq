import csv
import os
from datetime import datetime


DATASET_PATH = "data/transport_training_data.csv"


def append_transport_row(
    city: str,
    origin: str,
    destination: str,
    mode: str,
    distance_km: float,
    duration_min: float,
    cost_estimate: float,
    weather_source: str = "unknown"
):
    file_exists = os.path.exists(DATASET_PATH)

    with open(DATASET_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists or os.path.getsize(DATASET_PATH) == 0:
            writer.writerow([
                "city", "origin", "destination", "mode", "distance_km",
                "duration_min", "cost_estimate", "hour", "day_of_week", "weather_source"
            ])

        now = datetime.now()

        writer.writerow([
            city,
            origin,
            destination,
            mode,
            distance_km,
            duration_min,
            cost_estimate,
            now.hour,
            now.strftime("%A"),
            weather_source
        ])

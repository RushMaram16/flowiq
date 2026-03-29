import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GTFS_DIR = BASE_DIR / "data" / "gtfs" / "barcelona"

routes = pd.read_csv(GTFS_DIR / "routes.txt", low_memory=False)

print("=== TRANSPORT MODES ===")

mode_map = {
    0: "Tram",
    1: "Metro/Subway",
    2: "Rail/Train",
    3: "Bus",
    4: "Ferry",
    5: "Cable tram",
    6: "Aerial lift",
    7: "Funicular",
    11: "Trolleybus",
    12: "Monorail",
    100: "Railway",
    200: "Coach",
    400: "Urban railway/Metro",
    700: "Bus service",
    900: "Tram service",
}

counts = routes["route_type"].value_counts(dropna=False).sort_index()

for code, count in counts.items():
    try:
        code_int = int(code)
    except:
        code_int = code
    print(f"{code} -> {mode_map.get(code_int, f'Unknown ({code})')}: {count} routes")


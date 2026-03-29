import pandas as pd
from pathlib import Path
import math

BASE_DIR = Path(__file__).resolve().parent.parent
GTFS_DIR = BASE_DIR / "data" / "gtfs" / "barcelona"

stops = pd.read_csv(GTFS_DIR / "stops.txt")
routes = pd.read_csv(GTFS_DIR / "routes.txt")
trips = pd.read_csv(GTFS_DIR / "trips.txt", low_memory=False)
stop_times = pd.read_csv(GTFS_DIR / "stop_times.txt", low_memory=False)

print("✅ Data Loaded")
print(f"Stops: {len(stops)}")
print(f"Routes: {len(routes)}")
print(f"Trips: {len(trips)}")
print(f"Stop times: {len(stop_times)}")


def distance(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)


def find_nearest_stop(lat, lon, top_n=5):
    temp = stops.copy()
    temp["dist"] = temp.apply(
        lambda row: distance(lat, lon, row["stop_lat"], row["stop_lon"]),
        axis=1
    )
    return temp.sort_values("dist").head(top_n)


def find_direct_routes(from_stop_id, to_stop_id, max_results=10):
    from_times = stop_times[stop_times["stop_id"] == from_stop_id][
        ["trip_id", "arrival_time", "departure_time", "stop_sequence"]
    ].rename(columns={
        "arrival_time": "from_arrival",
        "departure_time": "from_departure",
        "stop_sequence": "from_sequence"
    })

    to_times = stop_times[stop_times["stop_id"] == to_stop_id][
        ["trip_id", "arrival_time", "departure_time", "stop_sequence"]
    ].rename(columns={
        "arrival_time": "to_arrival",
        "departure_time": "to_departure",
        "stop_sequence": "to_sequence"
    })

    merged = pd.merge(from_times, to_times, on="trip_id")
    merged = merged[merged["from_sequence"] < merged["to_sequence"]]

    if merged.empty:
        return merged

    merged = pd.merge(merged, trips[["trip_id", "route_id", "trip_headsign"]], on="trip_id", how="left")
    merged = pd.merge(
        merged,
        routes[["route_id", "route_short_name", "route_long_name", "route_type"]],
        on="route_id",
        how="left"
    )

    return merged.head(max_results)


def get_reachable_stops(from_stop_id):
    from_rows = stop_times[stop_times["stop_id"] == from_stop_id][["trip_id", "stop_sequence"]]
    reachable = []

    for _, row in from_rows.iterrows():
        trip_id = row["trip_id"]
        from_seq = row["stop_sequence"]

        later_stops = stop_times[
            (stop_times["trip_id"] == trip_id) &
            (stop_times["stop_sequence"] > from_seq)
        ][["stop_id", "trip_id", "stop_sequence"]]

        reachable.append(later_stops)

    if not reachable:
        return pd.DataFrame()

    return pd.concat(reachable).drop_duplicates()


def find_one_transfer_routes(from_stop_id, to_stop_id, max_results=10):
    first_leg_reachable = get_reachable_stops(from_stop_id)

    if first_leg_reachable.empty:
        return []

    possible_transfer_stops = first_leg_reachable["stop_id"].unique()
    results = []

    for transfer_stop_id in possible_transfer_stops[:300]:
        leg1 = find_direct_routes(from_stop_id, transfer_stop_id, max_results=3)
        leg2 = find_direct_routes(transfer_stop_id, to_stop_id, max_results=3)

        if not leg1.empty and not leg2.empty:
            transfer_name = stops.loc[stops["stop_id"] == transfer_stop_id, "stop_name"].values
            transfer_name = transfer_name[0] if len(transfer_name) else transfer_stop_id

            results.append({
                "transfer_stop_id": transfer_stop_id,
                "transfer_stop_name": transfer_name,
                "leg1": leg1.head(1),
                "leg2": leg2.head(1)
            })

        if len(results) >= max_results:
            break

    return results


# Example coordinates
from_lat, from_lon = 41.4036, 2.1744   # Sagrada Familia
to_lat, to_lon = 41.4145, 2.1527       # Park Guell

from_candidates = find_nearest_stop(from_lat, from_lon, top_n=3)
to_candidates = find_nearest_stop(to_lat, to_lon, top_n=3)

print("\nNearest origin stops:")
print(from_candidates[["stop_id", "stop_name", "dist"]])

print("\nNearest destination stops:")
print(to_candidates[["stop_id", "stop_name", "dist"]])

found_any = False

for _, from_row in from_candidates.iterrows():
    for _, to_row in to_candidates.iterrows():
        direct_result = find_direct_routes(from_row["stop_id"], to_row["stop_id"])
        if not direct_result.empty:
            found_any = True
            print("\n✅ Direct route found!")
            print(f"From stop: {from_row['stop_name']} ({from_row['stop_id']})")
            print(f"To stop: {to_row['stop_name']} ({to_row['stop_id']})")
            print(direct_result[[
                "trip_id",
                "route_id",
                "route_short_name",
                "route_long_name",
                "trip_headsign",
                "from_departure",
                "to_arrival"
            ]].head(10))
            break

        transfer_results = find_one_transfer_routes(from_row["stop_id"], to_row["stop_id"], max_results=3)
        if transfer_results:
            found_any = True
            print("\n✅ 1-transfer route(s) found!")
            print(f"From stop: {from_row['stop_name']} ({from_row['stop_id']})")
            print(f"To stop: {to_row['stop_name']} ({to_row['stop_id']})")

            for i, option in enumerate(transfer_results, start=1):
                print(f"\nOption {i}")
                print(f"Transfer at: {option['transfer_stop_name']} ({option['transfer_stop_id']})")

                print("Leg 1:")
                print(option["leg1"][[
                    "trip_id",
                    "route_id",
                    "route_short_name",
                    "route_long_name",
                    "trip_headsign",
                    "from_departure",
                    "to_arrival"
                ]])

                print("Leg 2:")
                print(option["leg2"][[
                    "trip_id",
                    "route_id",
                    "route_short_name",
                    "route_long_name",
                    "trip_headsign",
                    "from_departure",
                    "to_arrival"
                ]])
            break
    if found_any:
        break

if not found_any:
    print("\n❌ No direct or 1-transfer route found between the tested stop pairs.")
print("\n=== TRANSPORT MODES ===")

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
    400: "Metro",
    700: "Bus",
    900: "Tram"
}

counts = routes["route_type"].value_counts().sort_index()

for code, count in counts.items():
    try:
        code_int = int(code)
    except:
        code_int = code
    name = mode_map.get(code_int, f"Unknown ({code})")
    print(f"{code} -> {name}: {count} routes")

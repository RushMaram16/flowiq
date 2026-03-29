import pandas as pd
from pathlib import Path
import math

BASE_DIR = Path(__file__).resolve().parent.parent
GTFS_DIR = BASE_DIR / "data" / "gtfs" / "barcelona"

stops = pd.read_csv(GTFS_DIR / "stops.txt")
routes = pd.read_csv(GTFS_DIR / "routes.txt", low_memory=False)
trips = pd.read_csv(GTFS_DIR / "trips.txt", low_memory=False)
stop_times = pd.read_csv(GTFS_DIR / "stop_times.txt", low_memory=False)
stop_times["trip_id"] = stop_times["trip_id"].astype(str)
stop_times["stop_id"] = stop_times["stop_id"].astype(str)
stop_times["stop_sequence"] = pd.to_numeric(stop_times["stop_sequence"], errors="coerce")

stop_times_by_stop = {
    stop_id: group[["trip_id", "arrival_time", "departure_time", "stop_sequence"]].copy()
    for stop_id, group in stop_times.groupby("stop_id")
}

stop_times_by_trip = {
    trip_id: group.sort_values("stop_sequence")[["stop_id", "arrival_time", "departure_time", "stop_sequence"]].copy()
    for trip_id, group in stop_times.groupby("trip_id")
}

MODE_MAP = {
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

FARE_MAP = {
    "Metro/Subway": 2.55,
    "Metro": 2.55,
    "Bus": 2.40,
    "Rail/Train": 2.80,
    "Railway": 2.80,
    "Tram": 2.50,
    "Funicular": 3.00,
    "Coach": 5.00
}


def parse_time_to_minutes(t):
    if pd.isna(t):
        return None
    try:
        h, m, s = map(int, str(t).split(":"))
        return h * 60 + m + s / 60
    except:
        return None


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
    from_stop_id = str(from_stop_id)
    to_stop_id = str(to_stop_id)

    if from_stop_id not in stop_times_by_stop or to_stop_id not in stop_times_by_stop:
        return pd.DataFrame()

    from_times = stop_times_by_stop[from_stop_id][
        ["trip_id", "arrival_time", "departure_time", "stop_sequence"]
    ].rename(columns={
        "arrival_time": "from_arrival",
        "departure_time": "from_departure",
        "stop_sequence": "from_sequence"
    })

    to_times = stop_times_by_stop[to_stop_id][
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

    merged = pd.merge(
        merged,
        trips[["trip_id", "route_id", "trip_headsign"]],
        on="trip_id",
        how="left"
    )

    merged = pd.merge(
        merged,
        routes[["route_id", "route_short_name", "route_long_name", "route_type"]],
        on="route_id",
        how="left"
    )

    return merged.head(max_results)

def get_reachable_stops(from_stop_id):
    from_stop_id = str(from_stop_id)

    if from_stop_id not in stop_times_by_stop:
        return pd.DataFrame()

    from_rows = stop_times_by_stop[from_stop_id][["trip_id", "stop_sequence"]]
    reachable = []

    for _, row in from_rows.iterrows():
        trip_id = str(row["trip_id"])
        from_seq = row["stop_sequence"]

        if trip_id not in stop_times_by_trip:
            continue

        trip_stops = stop_times_by_trip[trip_id]
        later_stops = trip_stops[trip_stops["stop_sequence"] > from_seq][["stop_id", "stop_sequence"]].copy()
        later_stops["trip_id"] = trip_id

        if not later_stops.empty:
            reachable.append(later_stops)

    if not reachable:
        return pd.DataFrame()

    return pd.concat(reachable, ignore_index=True).drop_duplicates()

def find_one_transfer_routes(from_stop_id, to_stop_id, max_results=5):
    first_leg_reachable = get_reachable_stops(from_stop_id)

    if first_leg_reachable.empty:
        return []

    possible_transfer_stops = first_leg_reachable["stop_id"].unique()
    results = []

    print(f"Checking transfer options for {from_stop_id} -> {to_stop_id}")
    for idx, transfer_stop_id in enumerate(possible_transfer_stops[:20], start=1):
        if idx % 20 == 0:
            print(f"  checked {idx} transfer stops...")
        leg1 = find_direct_routes(from_stop_id, transfer_stop_id, max_results=3)
        leg2 = find_direct_routes(transfer_stop_id, to_stop_id, max_results=3)

        if not leg1.empty and not leg2.empty:
            transfer_name = stops.loc[stops["stop_id"] == transfer_stop_id, "stop_name"].values
            transfer_name = transfer_name[0] if len(transfer_name) else str(transfer_stop_id)

            results.append({
                "transfer_stop_id": transfer_stop_id,
                "transfer_stop_name": transfer_name,
                "leg1": leg1.iloc[0].to_dict(),
                "leg2": leg2.iloc[0].to_dict()
            })

        if len(results) >= max_results:
            break

    return results


def get_mode_name(route_type):
    try:
        route_type = int(route_type)
    except:
        pass
    return MODE_MAP.get(route_type, f"Unknown ({route_type})")


def estimate_leg_duration(leg):
    dep = parse_time_to_minutes(leg.get("from_departure"))
    arr = parse_time_to_minutes(leg.get("to_arrival"))
    if dep is None or arr is None:
        return 0
    return max(0, arr - dep)


def estimate_leg_cost(leg):
    mode = get_mode_name(leg.get("route_type"))
    return FARE_MAP.get(mode, 2.50)


def build_direct_recommendation(row):
    mode = get_mode_name(row["route_type"])
    duration = estimate_leg_duration(row)
    cost = estimate_leg_cost(row)

    return {
        "kind": "direct",
        "modes": [mode],
        "total_duration_min": round(duration, 1),
        "transfers": 0,
        "estimated_cost": round(cost, 2),
        "summary": f"{mode} direct route",
        "legs": [dict(row)]
    }


def build_transfer_recommendation(option):
    leg1 = option["leg1"]
    leg2 = option["leg2"]

    mode1 = get_mode_name(leg1["route_type"])
    mode2 = get_mode_name(leg2["route_type"])

    duration1 = estimate_leg_duration(leg1)
    duration2 = estimate_leg_duration(leg2)

    arr1 = parse_time_to_minutes(leg1.get("to_arrival"))
    dep2 = parse_time_to_minutes(leg2.get("from_departure"))
    transfer_wait = 0
    if arr1 is not None and dep2 is not None:
        transfer_wait = max(0, dep2 - arr1)

    total_duration = duration1 + duration2 + transfer_wait
    total_cost = estimate_leg_cost(leg1) + estimate_leg_cost(leg2)

    return {
        "kind": "one_transfer",
        "modes": [mode1, mode2],
        "total_duration_min": round(total_duration, 1),
        "transfers": 1,
        "estimated_cost": round(total_cost, 2),
        "summary": f"{mode1} + {mode2} via {option['transfer_stop_name']}",
        "transfer_stop_name": option["transfer_stop_name"],
        "legs": [leg1, leg2]
    }


def rank_recommendations(recommendations):
    if not recommendations:
        return {}

    fastest = min(recommendations, key=lambda x: x["total_duration_min"])
    cheapest = min(recommendations, key=lambda x: x["estimated_cost"])
    balanced = min(
        recommendations,
        key=lambda x: x["total_duration_min"] + (x["transfers"] * 8) + (x["estimated_cost"] * 2)
    )

    return {
        "fastest": fastest,
        "cheapest": cheapest,
        "balanced": balanced
    }


# Example: Sagrada Familia -> Park Guell
from_lat, from_lon = 41.4036, 2.1744
to_lat, to_lon = 41.4145, 2.1527

from_candidates = find_nearest_stop(from_lat, from_lon, top_n=3)
to_candidates = find_nearest_stop(to_lat, to_lon, top_n=3)

print("Origin candidates:")
print(from_candidates[["stop_id", "stop_name", "dist"]])

print("\nDestination candidates:")
print(to_candidates[["stop_id", "stop_name", "dist"]])

all_recommendations = []

for _, from_row in from_candidates.iterrows():
    for _, to_row in to_candidates.iterrows():
        print(f"\nTesting route from {from_row['stop_name']} to {to_row['stop_name']}")

        direct_routes = find_direct_routes(from_row["stop_id"], to_row["stop_id"], max_results=2)
        if not direct_routes.empty:
            for _, direct_row in direct_routes.iterrows():
                all_recommendations.append(build_direct_recommendation(direct_row))

        transfer_routes = find_one_transfer_routes(from_row["stop_id"], to_row["stop_id"], max_results=2)
        for option in transfer_routes:
            all_recommendations.append(build_transfer_recommendation(option))

ranked = rank_recommendations(all_recommendations)

print("\n=== FINAL RECOMMENDATIONS ===")
if not ranked:
    print("No recommendations found.")
else:
    for label, rec in ranked.items():
        print(f"\n{label.upper()}:")
        print(f"Summary: {rec['summary']}")
        print(f"Modes: {rec['modes']}")
        print(f"Duration: {rec['total_duration_min']} min")
        print(f"Transfers: {rec['transfers']}")
        print(f"Estimated cost: €{rec['estimated_cost']}")

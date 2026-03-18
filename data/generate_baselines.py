"""
SmartTrip AI - Baseline Generator
Generates Traffic Baseline and Weather Baseline rows for new Spanish cities
by interpolating from climate-similar existing cities.

Usage:
    cd Trip_optimizer
    python data/generate_baselines.py

Climate profiles:
    hot_mediterranean   → Seville template (Malaga, Cordoba, Granada)
    coastal_mediterranean → Barcelona template (Valencia, Palma)
    atlantic            → Bilbao, San Sebastian (cooler, wetter)
    inland_mediterranean → Madrid template (Toledo, Salamanca, Zaragoza, Granada)
    continental         → Madrid template (Salamanca, Toledo)
"""

import sys
import pathlib
import pandas as pd

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

EXCEL_PATH = ROOT / "engine" / "phase1_data.xlsx"


# ── Climate Template Mapping ─────────────────────────────────────────────────

# Maps new city → (template_city_in_excel, temp_offset_celsius, traffic_scale)
CITY_TEMPLATES = {
    # hot_mediterranean: Seville-like heat, but slightly cooler for Malaga (coastal)
    "Malaga":        ("Seville",    -2.0,  0.85),  # smaller city, less traffic
    "Cordoba":       ("Seville",    +1.0,  0.80),  # slightly hotter, smaller
    # inland_mediterranean: Madrid-like
    "Granada":       ("Seville",    -3.0,  0.75),  # cooler than Seville, smaller
    "Toledo":        ("Madrid",     +1.0,  0.60),  # smaller, warmer
    "Salamanca":     ("Madrid",     -1.0,  0.55),  # cooler, small city
    "Zaragoza":      ("Madrid",     +2.0,  0.90),  # larger, hot summers
    # coastal_mediterranean: Barcelona-like
    "Valencia":      ("Barcelona",  +2.0,  0.95),  # warmer and larger
    "Palma":         ("Barcelona",  +1.0,  0.70),  # island, smaller
    # atlantic: cooler, rainy
    "Bilbao":        ("Madrid",     -8.0,  0.85),  # much cooler, northern
    "San Sebastian": ("Madrid",    -10.0,  0.70),  # coolest, smallest
}

# Zone definitions for each new city (center_lat, center_lon, radius_km)
CITY_ZONES = {
    "Valencia":      (39.4699, -0.3763, [
        ("Central",         39.4753, -0.3804, 1.2),
        ("Tourist Cluster", 39.4633, -0.3490, 1.5),
        ("Residential",     39.4800, -0.3600, 2.5),
        ("Business",        39.4700, -0.3900, 1.8),
        ("Peripheral",      39.4400, -0.3700, 4.0),
    ]),
    "Bilbao":        (43.2630, -2.9350, [
        ("Central",         43.2627, -2.9253, 1.0),
        ("Tourist Cluster", 43.2690, -2.9300, 1.2),
        ("Residential",     43.2500, -2.9200, 2.0),
        ("Business",        43.2700, -2.9500, 1.5),
        ("Peripheral",      43.2400, -2.9600, 3.5),
    ]),
    "Granada":       (37.1773, -3.5986, [
        ("Central",         37.1773, -3.5986, 1.0),
        ("Tourist Cluster", 37.1760, -3.5890, 1.2),
        ("Residential",     37.1900, -3.5900, 2.0),
        ("Business",        37.1700, -3.6100, 1.5),
        ("Peripheral",      37.1600, -3.6200, 3.5),
    ]),
    "Malaga":        (36.7213, -4.4213, [
        ("Central",         36.7213, -4.4213, 1.0),
        ("Tourist Cluster", 36.7200, -4.4100, 1.5),
        ("Residential",     36.7300, -4.4000, 2.0),
        ("Business",        36.7100, -4.4400, 1.5),
        ("Peripheral",      36.7000, -4.4500, 3.5),
    ]),
    "Toledo":        (39.8567, -4.0244, [
        ("Central",         39.8567, -4.0244, 0.8),
        ("Tourist Cluster", 39.8600, -4.0200, 1.0),
        ("Residential",     39.8500, -4.0100, 1.8),
        ("Business",        39.8550, -4.0350, 1.2),
        ("Peripheral",      39.8400, -4.0400, 3.0),
    ]),
    "Salamanca":     (40.9701, -5.6635, [
        ("Central",         40.9701, -5.6635, 0.8),
        ("Tourist Cluster", 40.9650, -5.6600, 1.0),
        ("Residential",     40.9800, -5.6500, 2.0),
        ("Business",        40.9750, -5.6700, 1.2),
        ("Peripheral",      40.9600, -5.6800, 3.0),
    ]),
    "San Sebastian": (43.3183, -1.9812, [
        ("Central",         43.3183, -1.9812, 0.9),
        ("Tourist Cluster", 43.3200, -1.9700, 1.2),
        ("Residential",     43.3100, -1.9800, 2.0),
        ("Business",        43.3250, -1.9900, 1.5),
        ("Peripheral",      43.3000, -2.0000, 3.0),
    ]),
    "Cordoba":       (37.8882, -4.7794, [
        ("Central",         37.8882, -4.7794, 1.0),
        ("Tourist Cluster", 37.8800, -4.7800, 1.2),
        ("Residential",     37.8950, -4.7700, 2.0),
        ("Business",        37.8900, -4.7900, 1.5),
        ("Peripheral",      37.8700, -4.8000, 3.5),
    ]),
    "Palma":         (39.5696, 2.6502, [
        ("Central",         39.5696, 2.6502, 1.0),
        ("Tourist Cluster", 39.5750, 2.6600, 1.5),
        ("Residential",     39.5600, 2.6400, 2.0),
        ("Business",        39.5700, 2.6300, 1.5),
        ("Peripheral",      39.5500, 2.6200, 4.0),
    ]),
    "Zaragoza":      (41.6488, -0.8891, [
        ("Central",         41.6488, -0.8891, 1.2),
        ("Tourist Cluster", 41.6550, -0.8800, 1.5),
        ("Residential",     41.6600, -0.8700, 2.5),
        ("Business",        41.6400, -0.9000, 2.0),
        ("Peripheral",      41.6300, -0.9200, 4.0),
    ]),
}

# Zones not already in the list (for fallback)
ZONES = ["Central", "Tourist Cluster", "Residential", "Business", "Peripheral"]


def generate_traffic_baselines(traffic_df: pd.DataFrame, new_cities: list) -> pd.DataFrame:
    """Generate traffic baseline rows for new cities from templates."""
    new_rows = []

    for city_name in new_cities:
        if city_name not in CITY_TEMPLATES:
            print(f"  No template for {city_name}, skipping traffic baseline")
            continue

        template_city, _, traffic_scale = CITY_TEMPLATES[city_name]

        # Get template rows
        template_rows = traffic_df[
            traffic_df["city"].str.lower() == template_city.lower()
        ]
        if template_rows.empty:
            print(f"  Template city {template_city} not found in traffic data")
            continue

        for _, row in template_rows.iterrows():
            new_row = row.to_dict()
            new_row["city"] = city_name
            # Scale traffic index for city size
            new_row["avg_traffic_index"] = round(
                min(float(row["avg_traffic_index"]) * traffic_scale, 1.0), 3
            )
            new_rows.append(new_row)

        print(f"  Generated {len(template_rows)} traffic rows for {city_name} (from {template_city})")

    if new_rows:
        return pd.DataFrame(new_rows)
    return pd.DataFrame()


def generate_weather_baselines(weather_df: pd.DataFrame, new_cities: list) -> pd.DataFrame:
    """Generate weather baseline rows for new cities from templates."""
    new_rows = []

    for city_name in new_cities:
        if city_name not in CITY_TEMPLATES:
            print(f"  No template for {city_name}, skipping weather baseline")
            continue

        template_city, temp_offset, _ = CITY_TEMPLATES[city_name]

        # Get template rows
        template_rows = weather_df[
            weather_df["city"].str.lower() == template_city.lower()
        ]
        if template_rows.empty:
            print(f"  Template city {template_city} not found in weather data")
            continue

        for _, row in template_rows.iterrows():
            new_row = row.to_dict()
            new_row["city"] = city_name
            # Adjust temperature
            new_temp = float(row["avg_temperature_c"]) + temp_offset
            new_row["avg_temperature_c"] = round(new_temp, 1)
            # Recalculate heat discomfort index from new temperature
            new_row["heat_discomfort_index"] = round(
                _temperature_to_discomfort(new_temp), 3
            )
            new_rows.append(new_row)

        print(f"  Generated {len(template_rows)} weather rows for {city_name} (from {template_city})")

    if new_rows:
        return pd.DataFrame(new_rows)
    return pd.DataFrame()


def generate_zone_definitions(zones_df: pd.DataFrame, new_cities: list) -> pd.DataFrame:
    """Generate zone definition rows for new cities."""
    new_rows = []

    for city_name in new_cities:
        if city_name not in CITY_ZONES:
            print(f"  No zone config for {city_name}, skipping zones")
            continue

        _, _, zones = CITY_ZONES[city_name]
        for zone_name, lat, lon, radius in zones:
            new_rows.append({
                "city": city_name,
                "zone": zone_name,
                "center_latitude": lat,
                "center_longitude": lon,
                "radius_km": radius,
            })
        print(f"  Generated {len(zones)} zone rows for {city_name}")

    if new_rows:
        return pd.DataFrame(new_rows)
    return pd.DataFrame()


def _temperature_to_discomfort(temp_c: float) -> float:
    """Convert temperature to heat discomfort index (0-1)."""
    if temp_c <= 18:
        return 0.0
    elif temp_c <= 25:
        return (temp_c - 18) / 50.0
    elif temp_c <= 35:
        return 0.14 + (temp_c - 25) / 20.0
    elif temp_c <= 42:
        return 0.64 + (temp_c - 35) / 20.0
    else:
        return min(0.99, 0.99)


def main():
    print("SmartTrip AI - Baseline Generator")
    print(f"Excel path: {EXCEL_PATH}")

    if not EXCEL_PATH.exists():
        print(f"ERROR: {EXCEL_PATH} not found")
        sys.exit(1)

    # Load existing sheets
    try:
        traffic_df = pd.read_excel(EXCEL_PATH, sheet_name="Traffic Baseline", engine="openpyxl")
        weather_df = pd.read_excel(EXCEL_PATH, sheet_name="Weather Baseline", engine="openpyxl")
        zones_df = pd.read_excel(EXCEL_PATH, sheet_name="Zone Definitions", engine="openpyxl")
        print(f"Existing traffic rows: {len(traffic_df)}")
        print(f"Existing weather rows: {len(weather_df)}")
        print(f"Existing zone rows: {len(zones_df)}")
    except Exception as e:
        print(f"ERROR reading Excel: {e}")
        sys.exit(1)

    # Find new cities (in CITY_TEMPLATES but not yet in Excel)
    existing_traffic_cities = set(str(c).lower() for c in traffic_df["city"].unique())
    new_cities = [
        city for city in CITY_TEMPLATES
        if city.lower() not in existing_traffic_cities
    ]
    print(f"\nNew cities to generate: {new_cities}")

    if not new_cities:
        print("All cities already have baselines. Nothing to do.")
        return

    # Generate new rows
    print("\nGenerating traffic baselines...")
    new_traffic = generate_traffic_baselines(traffic_df, new_cities)

    print("\nGenerating weather baselines...")
    new_weather = generate_weather_baselines(weather_df, new_cities)

    print("\nGenerating zone definitions...")
    # Only add zones for cities not already in zones_df
    existing_zone_cities = set(str(c).lower() for c in zones_df["city"].unique())
    new_zone_cities = [c for c in new_cities if c.lower() not in existing_zone_cities]
    new_zones = generate_zone_definitions(zones_df, new_zone_cities)

    # Merge with existing
    if not new_traffic.empty:
        combined_traffic = pd.concat([traffic_df, new_traffic], ignore_index=True)
    else:
        combined_traffic = traffic_df

    if not new_weather.empty:
        combined_weather = pd.concat([weather_df, new_weather], ignore_index=True)
    else:
        combined_weather = weather_df

    if not new_zones.empty:
        combined_zones = pd.concat([zones_df, new_zones], ignore_index=True)
    else:
        combined_zones = zones_df

    # Write back
    print("\nWriting to Excel...")
    with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        combined_traffic.to_excel(writer, sheet_name="Traffic Baseline", index=False)
        combined_weather.to_excel(writer, sheet_name="Weather Baseline", index=False)
        combined_zones.to_excel(writer, sheet_name="Zone Definitions", index=False)

    print(f"\nDone!")
    print(f"  Traffic rows: {len(combined_traffic)}")
    print(f"  Weather rows: {len(combined_weather)}")
    print(f"  Zone rows: {len(combined_zones)}")


if __name__ == "__main__":
    main()

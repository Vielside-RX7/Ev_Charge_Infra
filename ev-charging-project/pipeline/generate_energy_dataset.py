"""
Synthetic Energy Consumption Dataset Generator for Virtual EV Trips.

Phase 12: Generates a reproducible, structured dataset of simulated EV trips
with realistic driving variations (distance, speed, elevation, traffic, weather,
driving style) suitable for future battery-energy consumption ML models.

Baseline Model: 0.150 kWh/km production Virtual EV baseline.

Output:
    data/processed/energy_consumption_features.csv

Usage:
    python pipeline/generate_energy_dataset.py
    python pipeline/generate_energy_dataset.py --num-trips 1500 --seed 42
"""

import argparse
import os
import random
import sys
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Default Constants & Seed
# ---------------------------------------------------------------------------
DEFAULT_NUM_TRIPS = 1200
DEFAULT_RANDOM_SEED = 42
BASELINE_CONSUMPTION_KWH_PER_KM = 0.150

# Output Path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "processed")
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "energy_consumption_features.csv")

# Vehicle Configurations
VEHICLE_PROFILES = [
    {
        "vehicle_id": "VIRTUAL_EV_001",
        "vehicle_name": "Virtual EV (Nexon EV Max)",
        "battery_capacity_kwh": 30.0,
        "curb_weight_kg": 1550,
    },
    {
        "vehicle_id": "VIRTUAL_EV_002",
        "vehicle_name": "Virtual EV Long Range",
        "battery_capacity_kwh": 40.5,
        "curb_weight_kg": 1680,
    },
]

DRIVING_STYLES = ["Eco", "Normal", "Aggressive"]
DRIVING_STYLE_PROBS = [0.25, 0.55, 0.20]

WEATHER_CONDITIONS = ["Clear", "Rain", "Hot", "Overcast"]
WEATHER_PROBS = [0.45, 0.20, 0.25, 0.10]


def calculate_energy_consumption(
    distance_km: float,
    average_speed_kmph: float,
    trip_duration_minutes: float,
    elevation_gain_m: float,
    traffic_density_score: float,
    ambient_temperature_c: float,
    driving_style: str,
    curb_weight_kg: float = 1550.0,
    rng: np.random.Generator = None,
) -> float:
    """
    Computes deterministic energy consumption for a simulated trip based on
    the 0.150 kWh/km production baseline, adjusted for physical and environmental factors.

    Physics Assumptions:
    1. Baseline: 0.150 kWh/km
    2. Driving Style Factor:
       - Eco: 0.92x (-8% through gradual acceleration & regenerative braking)
       - Normal: 1.00x (baseline)
       - Aggressive: 1.15x (+15% from rapid acceleration and friction braking)
    3. Aerodynamic Drag Factor:
       - Baseline calibrated at 45 km/h. At high speeds (>55 km/h), aerodynamic drag
         increases consumption quadratically.
    4. Elevation Potential Energy:
       - Ascent: + (mass * g * Δh) / (3.6e6 * η_ascent) [~0.0049 kWh per meter ascent]
       - Descent: - (mass * g * |Δh| * η_regen) / 3.6e6 [~0.0028 kWh per meter descent]
    5. Traffic Congestion Factor:
       - Stop-and-go auxiliary consumption and braking losses: +0.015 kWh/km * traffic_density
    6. Temperature / HVAC Auxiliary Power:
       - A/C operates when ambient temp > 25°C (~0.05 kW per °C above 25°C).
    7. Stochastic Sensor Noise:
       - ±1.5% normal variation simulating OBD battery measurement noise.
    """
    # 1. Base energy
    base_energy = distance_km * BASELINE_CONSUMPTION_KWH_PER_KM

    # 2. Driving style factor
    style_factors = {"Eco": 0.92, "Normal": 1.00, "Aggressive": 1.15}
    style_mult = style_factors.get(driving_style, 1.00)

    # 3. Speed / Aero factor
    if average_speed_kmph > 50.0:
        speed_mult = 1.0 + 0.0035 * (average_speed_kmph - 50.0)
    elif average_speed_kmph < 30.0:
        speed_mult = 1.0 + 0.0020 * (30.0 - average_speed_kmph)  # lower drivetrain efficiency
    else:
        speed_mult = 1.0

    # 4. Elevation factor
    if elevation_gain_m > 0:
        elevation_energy = (curb_weight_kg * 9.81 * elevation_gain_m) / (3.6e6 * 0.85)
    else:
        # Regenerative braking on descent (partial recovery)
        elevation_energy = (curb_weight_kg * 9.81 * elevation_gain_m * 0.65) / 3.6e6

    # 5. Traffic congestion factor
    traffic_loss = 0.015 * traffic_density_score * distance_km

    # 6. HVAC / Climate auxiliary energy
    if ambient_temperature_c > 25.0:
        hvac_power_kw = (ambient_temperature_c - 25.0) * 0.06
    elif ambient_temperature_c < 18.0:
        hvac_power_kw = (18.0 - ambient_temperature_c) * 0.04
    else:
        hvac_power_kw = 0.0
    hvac_energy = hvac_power_kw * (trip_duration_minutes / 60.0)

    # Combine mechanical, auxiliary, and baseline
    total_energy = (base_energy * style_mult * speed_mult) + elevation_energy + traffic_loss + hvac_energy

    # 7. Add small realistic measurement noise
    noise_mult = 1.0 + (rng.normal(0, 0.015) if rng else 0.0)
    total_energy = total_energy * noise_mult

    # Ensure strictly positive minimum energy
    return max(0.05, round(float(total_energy), 3))


def generate_energy_dataset(num_trips: int = DEFAULT_NUM_TRIPS, seed: int = DEFAULT_RANDOM_SEED) -> pd.DataFrame:
    """
    Generates a synthetic dataset of Virtual EV trips with feature/target separation.
    """
    print(f"[INFO] Initializing dataset generation with {num_trips} trips, seed={seed}...")
    random.seed(seed)
    rng = np.random.default_rng(seed)

    records = []

    for i in range(1, num_trips + 1):
        trip_id = f"SIM_TRIP_{i:04d}"

        # Select vehicle profile
        profile = rng.choice(VEHICLE_PROFILES, p=[0.75, 0.25])
        vehicle_id = profile["vehicle_id"]
        battery_capacity = profile["battery_capacity_kwh"]
        curb_weight = profile["curb_weight_kg"]

        # Starting battery state (20% to 95%)
        starting_soc = round(float(rng.uniform(20.0, 95.0)), 1)

        # Distance: mixture of short urban trips (2 to 15 km) and intercity trips (15 to 65 km)
        if rng.random() < 0.60:
            distance_km = round(float(rng.uniform(2.0, 15.0)), 2)
        else:
            distance_km = round(float(rng.uniform(15.0, 65.0)), 2)

        # Driving context
        driving_style = rng.choice(DRIVING_STYLES, p=DRIVING_STYLE_PROBS)
        weather = rng.choice(WEATHER_CONDITIONS, p=WEATHER_PROBS)

        # Ambient temperature (°C)
        if weather == "Hot":
            ambient_temp = round(float(rng.uniform(32.0, 42.0)), 1)
        elif weather == "Rain":
            ambient_temp = round(float(rng.uniform(20.0, 26.0)), 1)
        elif weather == "Overcast":
            ambient_temp = round(float(rng.uniform(22.0, 28.0)), 1)
        else:
            ambient_temp = round(float(rng.uniform(24.0, 34.0)), 1)

        # Traffic density (0.0: empty road, 1.0: dense gridlock)
        traffic_density = round(float(rng.beta(2, 4)), 3)

        # Average speed (km/h) inversely influenced by traffic
        base_speed = 65.0 - (traffic_density * 38.0)
        speed_jitter = float(rng.normal(0, 4.0))
        average_speed_kmph = round(max(15.0, min(85.0, base_speed + speed_jitter)), 1)

        # Duration (minutes) derived from distance and speed, with traffic delay
        nominal_duration_min = (distance_km / average_speed_kmph) * 60.0
        traffic_delay_min = traffic_density * (distance_km * 0.4)
        trip_duration_minutes = round(max(2.0, nominal_duration_min + traffic_delay_min), 1)

        # Elevation gain (meters)
        elevation_gain_m = round(float(rng.uniform(-120.0, 180.0)), 1)

        # Compute TARGET: energy_consumed_kwh
        energy_consumed_kwh = calculate_energy_consumption(
            distance_km=distance_km,
            average_speed_kmph=average_speed_kmph,
            trip_duration_minutes=trip_duration_minutes,
            elevation_gain_m=elevation_gain_m,
            traffic_density_score=traffic_density,
            ambient_temperature_c=ambient_temp,
            driving_style=driving_style,
            curb_weight_kg=curb_weight,
            rng=rng,
        )

        records.append({
            # Identifiers
            "trip_id": trip_id,
            "vehicle_id": vehicle_id,
            # Vehicle features (INPUT)
            "battery_capacity_kwh": battery_capacity,
            "starting_soc_percent": starting_soc,
            # Route & Trip features (INPUT)
            "distance_km": distance_km,
            "trip_duration_minutes": trip_duration_minutes,
            "average_speed_kmph": average_speed_kmph,
            "elevation_gain_m": elevation_gain_m,
            # Environmental features (INPUT)
            "traffic_density_score": traffic_density,
            "ambient_temperature_c": ambient_temp,
            "weather_condition": weather,
            # Driving Context (INPUT)
            "driving_style": driving_style,
            # Target (LABEL)
            "energy_consumed_kwh": energy_consumed_kwh,
            # Metadata
            "data_type": "synthetic_virtual_ev",
        })

    df = pd.DataFrame(records)
    return df


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic energy consumption dataset from Virtual EV trips.")
    parser.add_argument("--num-trips", type=int, default=DEFAULT_NUM_TRIPS, help="Number of trips to generate (default: 1200)")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED, help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--output", type=str, default=OUTPUT_CSV, help="Output CSV path")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    df = generate_energy_dataset(num_trips=args.num_trips, seed=args.seed)
    df.to_csv(args.output, index=False)

    print(f"[SUCCESS] Dataset generated successfully with {len(df)} rows and {len(df.columns)} columns.")
    print(f"[OUTPUT] Saved to: {args.output}")
    print("\nSummary Statistics:")
    print(df[["distance_km", "average_speed_kmph", "trip_duration_minutes", "energy_consumed_kwh"]].describe().to_string())


if __name__ == "__main__":
    main()

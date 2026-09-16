"""
LSTM Sequential Time-Series Dataset Generator for Virtual EV Telemetry.
========================================================================
Phase 15: Prepares a sequential time-series dataset of Virtual EV driving
telemetry suitable for future LSTM energy consumption prediction models.

Key Concept:
- Tabular Models (Linear Regression, XGBoost): One row = One trip summary
- LSTM Model: One sample = Sequence of ordered telemetry observations from a trip

Target Formulation:
- target_future_energy_kwh: Energy consumed over the subsequent prediction
  interval (horizon H=6 timesteps, 30 seconds) following the observed sequence
  (window L=20 timesteps, 100 seconds).
- Zero Target Leakage: Input sequence contains observations strictly up to t_N;
  future energy values from (t_N+1 to t_N+H) are strictly excluded from inputs.

Zero Split Leakage:
- Trips are split at the TRIP level first (70% train, 15% validation, 15% test).
- Sequences are generated strictly within individual trips (never crossing boundaries).
- Preprocessing scalers are fitted ONLY on the training split.

Outputs:
- data/processed/energy_lstm_sequences.npz
- models/artifacts/energy_lstm_dataset_metadata.json
- models/artifacts/energy_lstm_scaler.pkl

Usage:
    python pipeline/generate_lstm_energy_dataset.py
    python pipeline/generate_lstm_energy_dataset.py --seed 42 --seq-len 20 --horizon 6 --stride 2
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TABULAR_DATA_PATH = os.path.join(BASE_DIR, "data", "processed", "energy_consumption_features.csv")
OUTPUT_NPZ_PATH = os.path.join(BASE_DIR, "data", "processed", "energy_lstm_sequences.npz")
ARTIFACTS_DIR = os.path.join(BASE_DIR, "models", "artifacts")
METADATA_JSON_PATH = os.path.join(ARTIFACTS_DIR, "energy_lstm_dataset_metadata.json")
SCALER_PKL_PATH = os.path.join(ARTIFACTS_DIR, "energy_lstm_scaler.pkl")

# ---------------------------------------------------------------------------
# Sequence Generation Defaults
# ---------------------------------------------------------------------------
DEFAULT_RANDOM_SEED = 42
DEFAULT_SAMPLING_INTERVAL_SEC = 5.0   # Delta t = 5 seconds per telemetry observation
DEFAULT_SEQUENCE_LENGTH = 20          # L = 20 steps (100 seconds observation window)
DEFAULT_PREDICTION_HORIZON = 6        # H = 6 steps (30 seconds future prediction horizon)
DEFAULT_STRIDE = 2                    # S = 2 steps (10 seconds sliding window stride)

TRAIN_SPLIT_RATIO = 0.70
VAL_SPLIT_RATIO = 0.15
TEST_SPLIT_RATIO = 0.15

# ---------------------------------------------------------------------------
# Feature Schema
# ---------------------------------------------------------------------------
SEQUENCE_FEATURES = [
    "speed_kmph",             # Instantaneous vehicle speed (km/h)
    "latitude",               # Vehicle GPS latitude coordinate
    "longitude",              # Vehicle GPS longitude coordinate
    "soc_percent",            # Active battery State of Charge (%)
    "step_distance_km",       # Distance covered in current 5-second interval (km)
    "step_energy_kwh",        # Energy consumed in current 5-second interval (kWh)
    "elevation_m",            # Topographic route elevation (m)
    "elevation_delta_m",      # Elevation change in current interval (m)
    "traffic_density_score",  # Instantaneous traffic congestion score (0.0 - 1.0)
    "ambient_temperature_c",  # Ambient environmental temperature (°C)
    "battery_capacity_kwh",   # Static vehicle battery capacity (kWh)
    "driving_style_encoded",  # Driver aggressiveness (Eco=0, Normal=1, Aggressive=2)
]

STYLE_ENCODING = {
    "Eco": 0.0,
    "Normal": 1.0,
    "Aggressive": 2.0,
}

WEIGHT_MAP = {
    "VIRTUAL_EV_001": 1550.0,
    "VIRTUAL_EV_002": 1680.0,
}


def simulate_trip_telemetry(
    trip_row: pd.Series,
    sampling_interval_sec: float = DEFAULT_SAMPLING_INTERVAL_SEC,
    rng: np.random.Generator = None,
) -> pd.DataFrame:
    """
    Simulates ordered, timestamped telemetry observations for a single trip
    based on Virtual EV physics and recording architecture (tripRecorder.js / virtualEvModel.js).
    """
    if rng is None:
        rng = np.random.default_rng(42)

    trip_id = trip_row["trip_id"]
    vehicle_id = trip_row["vehicle_id"]
    battery_capacity = float(trip_row["battery_capacity_kwh"])
    starting_soc = float(trip_row["starting_soc_percent"])
    distance_km = float(trip_row["distance_km"])
    duration_min = float(trip_row["trip_duration_minutes"])
    avg_speed_kmph = float(trip_row["average_speed_kmph"])
    elevation_gain_m = float(trip_row["elevation_gain_m"])
    traffic_density = float(trip_row["traffic_density_score"])
    ambient_temp = float(trip_row["ambient_temperature_c"])
    driving_style = str(trip_row["driving_style"])
    curb_weight_kg = WEIGHT_MAP.get(vehicle_id, 1550.0)

    duration_sec = duration_min * 60.0
    num_steps = max(int(np.ceil(duration_sec / sampling_interval_sec)), DEFAULT_SEQUENCE_LENGTH + DEFAULT_PREDICTION_HORIZON + 2)

    # 1. Instantaneous Speed Profile (km/h)
    # Realistic driving: smooth ramp-up, cruising with traffic fluctuations, stops, ramp-down
    t = np.linspace(0, 1, num_steps)
    # Base envelope
    envelope = np.sin(np.pi * np.clip(t * 1.05, 0, 1)) ** 0.5
    # Micro traffic fluctuations & stop-and-go events
    fluctuations = rng.normal(0, 3.5, size=num_steps)
    stop_prob = traffic_density * 0.15
    stop_mask = rng.random(size=num_steps) < stop_prob
    
    raw_speeds = (avg_speed_kmph * envelope) + fluctuations
    raw_speeds[stop_mask] *= 0.15
    speeds = np.clip(raw_speeds, 0.0, 110.0)
    speeds[0] = 0.0
    speeds[-1] = 0.0

    # 2. Step distances (km)
    hours_per_step = sampling_interval_sec / 3600.0
    step_distances = speeds * hours_per_step
    # Scale distances so total exactly matches trip distance_km
    sum_dist = np.sum(step_distances)
    if sum_dist > 1e-4:
        scale_factor = distance_km / sum_dist
        step_distances *= scale_factor
        speeds = step_distances / hours_per_step
    else:
        step_distances = np.full(num_steps, distance_km / num_steps)
        speeds = step_distances / hours_per_step

    # 3. GPS Coordinate Trajectory (Mysore urban base coordinates)
    # Mysore EV base: 12.2958° N, 76.6394° E
    base_lat, base_lon = 12.2958, 76.6394
    # Heading angle with slight turns
    heading = rng.uniform(0, 2 * np.pi)
    curvature = rng.normal(0, 0.02, size=num_steps)
    headings = heading + np.cumsum(curvature)
    
    # 1 deg lat ~ 111 km, 1 deg lon ~ 111 * cos(lat) km
    delta_lat = (step_distances * np.cos(headings)) / 111.0
    delta_lon = (step_distances * np.sin(headings)) / (111.0 * np.cos(np.radians(base_lat)))
    lats = base_lat + np.cumsum(delta_lat)
    lons = base_lon + np.cumsum(delta_lon)

    # 4. Elevation Profile (m)
    # Undulating terrain summing to elevation_gain_m
    elevation_steps = (elevation_gain_m / num_steps) + rng.normal(0, 0.5, size=num_steps)
    elevation_steps[-1] += elevation_gain_m - np.sum(elevation_steps)
    elevations = 750.0 + np.cumsum(elevation_steps)  # Mysore average altitude ~750m ASL

    # 5. Step Energy Consumption (kWh) based on virtualEvModel.js and physics
    style_factors = {"Eco": 0.92, "Normal": 1.00, "Aggressive": 1.15}
    style_mult = style_factors.get(driving_style, 1.00)

    # Aerodynamic drag adjustment per step
    aero_mult = np.ones(num_steps)
    aero_mult[speeds > 50.0] = 1.0 + 0.0035 * (speeds[speeds > 50.0] - 50.0)
    aero_mult[speeds < 30.0] = 1.0 + 0.0020 * (30.0 - speeds[speeds < 30.0])

    # Elevation potential energy per step
    elev_energy = np.zeros(num_steps)
    pos_mask = elevation_steps > 0
    neg_mask = elevation_steps <= 0
    elev_energy[pos_mask] = (curb_weight_kg * 9.81 * elevation_steps[pos_mask]) / (3.6e6 * 0.85)
    elev_energy[neg_mask] = (curb_weight_kg * 9.81 * elevation_steps[neg_mask] * 0.65) / 3.6e6

    # Traffic stop auxiliary loss per step
    step_traffic = traffic_density + rng.normal(0, 0.05, size=num_steps)
    step_traffic = np.clip(step_traffic, 0.0, 1.0)
    traffic_energy = 0.015 * step_traffic * step_distances

    # HVAC auxiliary energy per step
    if ambient_temp > 25.0:
        hvac_power_kw = (ambient_temp - 25.0) * 0.06
    elif ambient_temp < 18.0:
        hvac_power_kw = (18.0 - ambient_temp) * 0.04
    else:
        hvac_power_kw = 0.0
    hvac_energy = np.full(num_steps, hvac_power_kw * (sampling_interval_sec / 3600.0))

    # Base mechanical energy (0.150 kWh/km baseline)
    base_step_energy = step_distances * 0.150 * style_mult * aero_mult

    # Total instantaneous step energy with sensor noise
    sensor_noise = rng.normal(1.0, 0.01, size=num_steps)
    step_energies = (base_step_energy + elev_energy + traffic_energy + hvac_energy) * sensor_noise
    step_energies = np.maximum(0.0001, step_energies)

    # 6. Cumulative Energy & State of Charge
    cumulative_energy = np.cumsum(step_energies)
    soc_pct = starting_soc - ((cumulative_energy / battery_capacity) * 100.0)
    soc_pct = np.clip(soc_pct, 0.0, 100.0)

    # Construct ordered DataFrame
    driving_style_num = STYLE_ENCODING.get(driving_style, 1.0)
    telemetry_df = pd.DataFrame({
        "trip_id": trip_id,
        "step_index": np.arange(num_steps),
        "timestamp_sec": np.arange(num_steps) * sampling_interval_sec,
        "speed_kmph": np.round(speeds, 2),
        "latitude": np.round(lats, 6),
        "longitude": np.round(lons, 6),
        "soc_percent": np.round(soc_pct, 2),
        "step_distance_km": np.round(step_distances, 4),
        "step_energy_kwh": np.round(step_energies, 5),
        "elevation_m": np.round(elevations, 2),
        "elevation_delta_m": np.round(elevation_steps, 3),
        "traffic_density_score": np.round(step_traffic, 3),
        "ambient_temperature_c": float(ambient_temp),
        "battery_capacity_kwh": float(battery_capacity),
        "driving_style_encoded": float(driving_style_num),
        "cumulative_energy_kwh": np.round(cumulative_energy, 4),
    })

    return telemetry_df


def construct_sequences_for_trips(
    trip_telemetries: Dict[str, pd.DataFrame],
    trip_ids: List[str],
    seq_len: int = DEFAULT_SEQUENCE_LENGTH,
    horizon: int = DEFAULT_PREDICTION_HORIZON,
    stride: int = DEFAULT_STRIDE,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Constructs sliding sequence windows strictly within trips.
    NEVER allows a sequence window to cross trip boundaries.

    Returns:
    - X: Array of shape (num_samples, seq_len, num_features)
    - y: Array of shape (num_samples,) representing future energy consumption over horizon H
    - sequence_trip_ids: List of trip IDs each sequence belongs to
    """
    X_list = []
    y_list = []
    seq_trip_ids = []

    # Sort trip_ids to ensure deterministic order
    sorted_trip_ids = sorted(trip_ids)

    for trip_id in sorted_trip_ids:
        df = trip_telemetries[trip_id]
        total_steps = len(df)
        required_steps = seq_len + horizon

        if total_steps < required_steps:
            continue

        feature_matrix = df[SEQUENCE_FEATURES].to_numpy(dtype=np.float32)
        step_energies = df["step_energy_kwh"].to_numpy(dtype=np.float32)

        # Slide window across trip
        for start_idx in range(0, total_steps - required_steps + 1, stride):
            end_idx = start_idx + seq_len
            horizon_end_idx = end_idx + horizon

            # Input window: [start_idx : end_idx] (L steps)
            x_seq = feature_matrix[start_idx:end_idx]

            # Target: sum of step energies in the subsequent horizon window [end_idx : horizon_end_idx] (H steps)
            # ZERO TARGET LEAKAGE: strictly computed from future steps following end_idx
            target_future_energy = float(np.sum(step_energies[end_idx:horizon_end_idx]))

            X_list.append(x_seq)
            y_list.append(target_future_energy)
            seq_trip_ids.append(trip_id)

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)
    return X, y, seq_trip_ids


def generate_lstm_dataset(
    tabular_csv_path: str = TABULAR_DATA_PATH,
    output_npz_path: str = OUTPUT_NPZ_PATH,
    metadata_json_path: str = METADATA_JSON_PATH,
    scaler_pkl_path: str = SCALER_PKL_PATH,
    seed: int = DEFAULT_RANDOM_SEED,
    seq_len: int = DEFAULT_SEQUENCE_LENGTH,
    horizon: int = DEFAULT_PREDICTION_HORIZON,
    stride: int = DEFAULT_STRIDE,
    sampling_interval_sec: float = DEFAULT_SAMPLING_INTERVAL_SEC,
) -> Dict:
    """
    Orchestrates sequential time-series dataset generation, train/val/test trip split,
    window construction, and training-only normalization.
    """
    print(f"============================================================")
    print(f"LSTM SEQUENTIAL EV ENERGY DATASET GENERATOR")
    print(f"Source: {tabular_csv_path}")
    print(f"Random Seed: {seed}")
    print(f"Window: L={seq_len} timesteps ({seq_len * sampling_interval_sec:.0f}s)")
    print(f"Horizon: H={horizon} timesteps ({horizon * sampling_interval_sec:.0f}s)")
    print(f"Stride: S={stride} timesteps ({stride * sampling_interval_sec:.0f}s)")
    print(f"============================================================\n")

    if not os.path.exists(tabular_csv_path):
        raise FileNotFoundError(f"Tabular energy dataset not found at: {tabular_csv_path}")

    tabular_df = pd.read_csv(tabular_csv_path)
    total_trips = len(tabular_df)
    print(f"[1/6] Loaded {total_trips} trips from tabular dataset.")

    # 1. Trip-Level Split (NO DATA LEAKAGE)
    rng = np.random.default_rng(seed)
    trip_ids = tabular_df["trip_id"].tolist()
    shuffled_trip_ids = rng.permutation(trip_ids).tolist()

    n_train = int(total_trips * TRAIN_SPLIT_RATIO)
    n_val = int(total_trips * VAL_SPLIT_RATIO)

    train_trips = set(shuffled_trip_ids[:n_train])
    val_trips = set(shuffled_trip_ids[n_train : n_train + n_val])
    test_trips = set(shuffled_trip_ids[n_train + n_val :])

    # Assert mutually exclusive trip IDs
    assert train_trips.isdisjoint(val_trips), "Train and Val trip IDs must not overlap!"
    assert train_trips.isdisjoint(test_trips), "Train and Test trip IDs must not overlap!"
    assert val_trips.isdisjoint(test_trips), "Val and Test trip IDs must not overlap!"
    print(f"[2/6] Trip-level partition: Train={len(train_trips)}, Val={len(val_trips)}, Test={len(test_trips)} trips.")

    # 2. Simulate ordered telemetry for each trip
    print(f"[3/6] Simulating sequential driving telemetry for {total_trips} trips (Δt={sampling_interval_sec}s)...")
    trip_telemetries = {}
    for idx, row in tabular_df.iterrows():
        # Seed generator per trip deterministically
        trip_seed = seed + int(row["trip_id"].split("_")[-1])
        trip_rng = np.random.default_rng(trip_seed)
        t_df = simulate_trip_telemetry(row, sampling_interval_sec=sampling_interval_sec, rng=trip_rng)
        trip_telemetries[row["trip_id"]] = t_df

    # 3. Construct sequences independently within each split
    print(f"[4/6] Constructing sequence windows (L={seq_len}, H={horizon}, S={stride}) per split...")
    X_train_raw, y_train, train_seq_trip_ids = construct_sequences_for_trips(
        trip_telemetries, list(train_trips), seq_len=seq_len, horizon=horizon, stride=stride
    )
    X_val_raw, y_val, val_seq_trip_ids = construct_sequences_for_trips(
        trip_telemetries, list(val_trips), seq_len=seq_len, horizon=horizon, stride=stride
    )
    X_test_raw, y_test, test_seq_trip_ids = construct_sequences_for_trips(
        trip_telemetries, list(test_trips), seq_len=seq_len, horizon=horizon, stride=stride
    )

    print(f"   - Train sequences: {X_train_raw.shape[0]} samples")
    print(f"   - Val sequences:   {X_val_raw.shape[0]} samples")
    print(f"   - Test sequences:  {X_test_raw.shape[0]} samples")

    # 4. Normalization / Scaling: FIT SCALER ONLY ON TRAINING DATA
    print(f"[5/6] Fitting StandardScaler strictly on training split (X_train)...")
    n_features = len(SEQUENCE_FEATURES)
    scaler = StandardScaler()
    
    # Flatten (N, L, F) -> (N*L, F) to compute timestep feature statistics on train only
    X_train_flat = X_train_raw.reshape(-1, n_features)
    scaler.fit(X_train_flat)

    # Transform all splits using training-fitted scaler
    X_train = scaler.transform(X_train_flat).reshape(X_train_raw.shape)
    X_val = scaler.transform(X_val_raw.reshape(-1, n_features)).reshape(X_val_raw.shape)
    X_test = scaler.transform(X_test_raw.reshape(-1, n_features)).reshape(X_test_raw.shape)

    # Verify no NaN or infinite values
    assert not np.isnan(X_train).any(), "NaN found in X_train!"
    assert not np.isnan(X_val).any(), "NaN found in X_val!"
    assert not np.isnan(X_test).any(), "NaN found in X_test!"
    assert not np.isnan(y_train).any(), "NaN found in y_train!"
    assert not np.isnan(y_val).any(), "NaN found in y_val!"
    assert not np.isnan(y_test).any(), "NaN found in y_test!"

    # 5. Save Outputs & Metadata
    print(f"[6/6] Persisting artifacts...")
    os.makedirs(os.path.dirname(output_npz_path), exist_ok=True)
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

    np.savez_compressed(
        output_npz_path,
        X_train=X_train,
        y_train=y_train,
        X_val=X_val,
        y_val=y_val,
        X_test=X_test,
        y_test=y_test,
        X_train_raw=X_train_raw,
        X_val_raw=X_val_raw,
        X_test_raw=X_test_raw,
        train_trip_ids=np.array(sorted(list(train_trips))),
        val_trip_ids=np.array(sorted(list(val_trips))),
        test_trip_ids=np.array(sorted(list(test_trips))),
        train_seq_trip_ids=np.array(train_seq_trip_ids),
        val_seq_trip_ids=np.array(val_seq_trip_ids),
        test_seq_trip_ids=np.array(test_seq_trip_ids),
        feature_names=np.array(SEQUENCE_FEATURES),
    )

    # Save scaler
    scaler_bundle = {
        "scaler": scaler,
        "feature_names": SEQUENCE_FEATURES,
        "mean_": scaler.mean_.tolist(),
        "scale_": scaler.scale_.tolist(),
        "var_": scaler.var_.tolist(),
        "n_samples_seen_": int(scaler.n_samples_seen_),
    }
    joblib.dump(scaler_bundle, scaler_pkl_path)

    # Save metadata JSON
    metadata = {
        "dataset_name": "Virtual EV LSTM Sequential Energy Dataset",
        "description": "Sequential driving telemetry windows for future EV energy prediction (Change 15).",
        "random_seed": seed,
        "data_type": "synthetic_virtual_ev",
        "sampling_interval_sec": sampling_interval_sec,
        "sequence_length_timesteps": seq_len,
        "sequence_length_seconds": seq_len * sampling_interval_sec,
        "prediction_horizon_timesteps": horizon,
        "prediction_horizon_seconds": horizon * sampling_interval_sec,
        "stride_timesteps": stride,
        "stride_seconds": stride * sampling_interval_sec,
        "target_definition": {
            "name": "target_future_energy_kwh",
            "description": "Net battery energy (kWh) consumed over the subsequent prediction horizon H following the observed sequence window.",
            "formula": "sum(step_energy_kwh[t_N+1 : t_N+H])",
            "leakage_guard": "Future step energies from t_N+1 to t_N+H are strictly excluded from the input sequence X (t_1 to t_N).",
        },
        "features": {
            "order": SEQUENCE_FEATURES,
            "count": len(SEQUENCE_FEATURES),
            "time_varying": [
                "speed_kmph", "latitude", "longitude", "soc_percent",
                "step_distance_km", "step_energy_kwh", "elevation_m",
                "elevation_delta_m", "traffic_density_score"
            ],
            "static_context": ["ambient_temperature_c", "battery_capacity_kwh", "driving_style_encoded"],
        },
        "trip_split": {
            "split_level": "trip_id (disjoint partition)",
            "train_trip_count": len(train_trips),
            "val_trip_count": len(val_trips),
            "test_trip_count": len(test_trips),
            "total_trips": total_trips,
            "train_ratio": TRAIN_SPLIT_RATIO,
            "val_ratio": VAL_SPLIT_RATIO,
            "test_ratio": TEST_SPLIT_RATIO,
        },
        "sequence_counts": {
            "train_sequences": int(X_train.shape[0]),
            "val_sequences": int(X_val.shape[0]),
            "test_sequences": int(X_test.shape[0]),
            "total_sequences": int(X_train.shape[0] + X_val.shape[0] + X_test.shape[0]),
        },
        "shapes": {
            "X_train": list(X_train.shape),
            "y_train": list(y_train.shape),
            "X_val": list(X_val.shape),
            "y_val": list(y_val.shape),
            "X_test": list(X_test.shape),
            "y_test": list(y_test.shape),
        },
        "normalization": {
            "method": "StandardScaler (zero mean, unit variance)",
            "fitted_on": "X_train only (validation and test sets never used for fitting)",
            "feature_means": dict(zip(SEQUENCE_FEATURES, [round(m, 6) for m in scaler.mean_])),
            "feature_scales": dict(zip(SEQUENCE_FEATURES, [round(s, 6) for s in scaler.scale_])),
        },
        "target_summary_statistics": {
            "train_y_mean_kwh": round(float(np.mean(y_train)), 6),
            "train_y_std_kwh": round(float(np.std(y_train)), 6),
            "train_y_min_kwh": round(float(np.min(y_train)), 6),
            "train_y_max_kwh": round(float(np.max(y_train)), 6),
            "test_y_mean_kwh": round(float(np.mean(y_test)), 6),
            "test_y_min_kwh": round(float(np.min(y_test)), 6),
            "test_y_max_kwh": round(float(np.max(y_test)), 6),
        },
    }

    with open(metadata_json_path, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n[SUCCESS] Sequential LSTM dataset generated successfully!")
    print(f"   - Array artifact:   {output_npz_path} ({os.path.getsize(output_npz_path) / 1e6:.2f} MB)")
    print(f"   - Scaler artifact:  {scaler_pkl_path}")
    print(f"   - Metadata JSON:    {metadata_json_path}")
    print(f"\nSequence Shapes:")
    print(f"   - X_train: {X_train.shape}  |  y_train: {y_train.shape}")
    print(f"   - X_val:   {X_val.shape}  |  y_val:   {y_val.shape}")
    print(f"   - X_test:  {X_test.shape}  |  y_test:  {y_test.shape}")
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Generate LSTM sequential EV energy dataset.")
    parser.add_argument("--tabular-path", type=str, default=TABULAR_DATA_PATH, help="Path to energy_consumption_features.csv")
    parser.add_argument("--output-npz", type=str, default=OUTPUT_NPZ_PATH, help="Output .npz path")
    parser.add_argument("--metadata-json", type=str, default=METADATA_JSON_PATH, help="Output metadata JSON path")
    parser.add_argument("--scaler-pkl", type=str, default=SCALER_PKL_PATH, help="Output scaler pkl path")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED, help="Random seed (default: 42)")
    parser.add_argument("--seq-len", type=int, default=DEFAULT_SEQUENCE_LENGTH, help="Sequence window length L (default: 20)")
    parser.add_argument("--horizon", type=int, default=DEFAULT_PREDICTION_HORIZON, help="Prediction horizon H (default: 6)")
    parser.add_argument("--stride", type=int, default=DEFAULT_STRIDE, help="Window stride S (default: 2)")
    parser.add_argument("--sampling-interval", type=float, default=DEFAULT_SAMPLING_INTERVAL_SEC, help="Sampling interval in seconds (default: 5.0)")

    args = parser.parse_args()

    generate_lstm_dataset(
        tabular_csv_path=args.tabular_path,
        output_npz_path=args.output_npz,
        metadata_json_path=args.metadata_json,
        scaler_pkl_path=args.scaler_pkl,
        seed=args.seed,
        seq_len=args.seq_len,
        horizon=args.horizon,
        stride=args.stride,
        sampling_interval_sec=args.sampling_interval,
    )


if __name__ == "__main__":
    main()

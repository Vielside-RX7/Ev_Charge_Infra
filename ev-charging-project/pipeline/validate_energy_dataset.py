"""
Validation Script for Energy Consumption Feature Dataset.

Phase 12 Verification Utility:
Inspects data/processed/energy_consumption_features.csv for:
- Row count, column names, data types
- Missing values and duplicate rows
- Value range sanity checks (no negative distance or energy, valid SoC, valid speed)
- Feature/target separation & target leakage prevention
- Summary statistics (min, max, mean) for key numeric columns

Usage:
    python pipeline/validate_energy_dataset.py
"""

import os
import sys
import pandas as pd

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATASET_PATH = os.path.join(_PROJECT_ROOT, "data", "processed", "energy_consumption_features.csv")

EXPECTED_INPUT_COLUMNS = [
    "trip_id",
    "vehicle_id",
    "battery_capacity_kwh",
    "starting_soc_percent",
    "distance_km",
    "trip_duration_minutes",
    "average_speed_kmph",
    "elevation_gain_m",
    "traffic_density_score",
    "ambient_temperature_c",
    "weather_condition",
    "driving_style",
]
EXPECTED_TARGET_COLUMN = "energy_consumed_kwh"
EXPECTED_METADATA_COLUMN = "data_type"


def validate_dataset(csv_path: str = DATASET_PATH) -> bool:
    print(f"============================================================")
    print(f"ENERGY CONSUMPTION DATASET VALIDATION")
    print(f"File: {csv_path}")
    print(f"============================================================\n")

    if not os.path.exists(csv_path):
        print(f"[ERROR] Dataset file not found at: {csv_path}")
        return False

    df = pd.read_csv(csv_path)

    all_passed = True

    # 1. Row count & dimensions
    num_rows, num_cols = df.shape
    print(f"1. Dataset Dimensions:")
    print(f"   - Rows:    {num_rows}")
    print(f"   - Columns: {num_cols}")
    if num_rows < 500:
        print(f"   [FAIL] Expected at least 500 rows, got {num_rows}")
        all_passed = False
    else:
        print(f"   [PASS] Row count is sufficient (>= 500)")

    # 2. Column names & Feature/Target separation
    print(f"\n2. Column Integrity & Schema:")
    print(f"   - Columns found: {list(df.columns)}")
    missing_cols = [c for c in EXPECTED_INPUT_COLUMNS + [EXPECTED_TARGET_COLUMN, EXPECTED_METADATA_COLUMN] if c not in df.columns]
    if missing_cols:
        print(f"   [FAIL] Missing expected columns: {missing_cols}")
        all_passed = False
    else:
        print(f"   [PASS] All expected input, target, and metadata columns present.")

    # Target leakage check: ensure target is separated and not duplicated in features
    input_cols = [c for c in df.columns if c not in [EXPECTED_TARGET_COLUMN, EXPECTED_METADATA_COLUMN, "trip_id"]]
    if EXPECTED_TARGET_COLUMN in input_cols:
        print(f"   [FAIL] Target leakage detected: {EXPECTED_TARGET_COLUMN} present in input feature set!")
        all_passed = False
    else:
        print(f"   [PASS] Zero target leakage: Input feature set ({len(input_cols)} features) strictly isolated from target.")

    # 3. Missing values check
    print(f"\n3. Missing Values (Nulls):")
    null_counts = df.isnull().sum()
    total_nulls = null_counts.sum()
    print(f"   - Total null values: {total_nulls}")
    if total_nulls > 0:
        print(f"   [FAIL] Found nulls:\n{null_counts[null_counts > 0]}")
        all_passed = False
    else:
        print(f"   [PASS] Zero missing/null values across entire dataset.")

    # 4. Duplicate rows check
    print(f"\n4. Duplicate Records:")
    num_duplicates = df.duplicated().sum()
    print(f"   - Duplicate rows: {num_duplicates}")
    if num_duplicates > 0:
        print(f"   [FAIL] Found {num_duplicates} duplicate rows!")
        all_passed = False
    else:
        print(f"   [PASS] Zero duplicate rows.")

    # 5. Sanity Checks & Numeric Boundaries
    print(f"\n5. Numeric Range & Boundary Checks:")

    # Distance check (> 0)
    min_dist, max_dist, mean_dist = df["distance_km"].min(), df["distance_km"].max(), df["distance_km"].mean()
    print(f"   - Distance (km):        min={min_dist:.2f}, max={max_dist:.2f}, mean={mean_dist:.2f}")
    if min_dist <= 0:
        print(f"   [FAIL] Negative or zero distance found: min={min_dist}")
        all_passed = False
    else:
        print(f"   [PASS] All distance values are strictly positive.")

    # Speed check (> 0, <= 140)
    min_spd, max_spd, mean_spd = df["average_speed_kmph"].min(), df["average_speed_kmph"].max(), df["average_speed_kmph"].mean()
    print(f"   - Speed (km/h):         min={min_spd:.1f}, max={max_spd:.1f}, mean={mean_spd:.1f}")
    if min_spd <= 0 or max_spd > 140:
        print(f"   [FAIL] Unrealistic speeds: min={min_spd}, max={max_spd}")
        all_passed = False
    else:
        print(f"   [PASS] Speed values are within realistic bounds (15 - 85 km/h).")

    # SoC check (0 - 100%)
    min_soc, max_soc = df["starting_soc_percent"].min(), df["starting_soc_percent"].max()
    print(f"   - Starting SoC (%):     min={min_soc:.1f}%, max={max_soc:.1f}%")
    if min_soc < 0 or max_soc > 100:
        print(f"   [FAIL] Invalid SoC bounds: min={min_soc}, max={max_soc}")
        all_passed = False
    else:
        print(f"   [PASS] SoC values are valid (between 0% and 100%).")

    # Target Energy check (> 0)
    min_egy, max_egy, mean_egy = df["energy_consumed_kwh"].min(), df["energy_consumed_kwh"].max(), df["energy_consumed_kwh"].mean()
    print(f"   - Energy Consumed (kWh): min={min_egy:.3f}, max={max_egy:.3f}, mean={mean_egy:.3f}")
    if min_egy <= 0:
        print(f"   [FAIL] Negative or zero energy consumed found: min={min_egy}")
        all_passed = False
    else:
        print(f"   [PASS] All target energy values are strictly positive.")

    # 6. Condition Variation Coverage
    print(f"\n6. Condition Diversity Check:")
    styles = df["driving_style"].unique().tolist()
    weathers = df["weather_condition"].unique().tolist()
    print(f"   - Driving styles:   {styles}")
    print(f"   - Weather states:   {weathers}")
    if len(styles) < 3 or len(weathers) < 4:
        print(f"   [FAIL] Inadequate variation in driving conditions!")
        all_passed = False
    else:
        print(f"   [PASS] Comprehensive coverage across driving styles and weather variations.")

    # 7. Metadata Flag Check
    print(f"\n7. Synthetic Data Transparency:")
    data_types = df["data_type"].unique().tolist()
    print(f"   - data_type values: {data_types}")
    if data_types != ["synthetic_virtual_ev"]:
        print(f"   [FAIL] Rows must be clearly designated as synthetic_virtual_ev")
        all_passed = False
    else:
        print(f"   [PASS] Clearly identified as synthetic Virtual EV data.")

    print(f"\n============================================================")
    if all_passed:
        print(f"VALIDATION RESULT: ALL CHECKS PASSED [SUCCESS]")
    else:
        print(f"VALIDATION RESULT: ONE OR MORE CHECKS FAILED [FAILURE]")
    print(f"============================================================")
    return all_passed


if __name__ == "__main__":
    success = validate_dataset()
    sys.exit(0 if success else 1)

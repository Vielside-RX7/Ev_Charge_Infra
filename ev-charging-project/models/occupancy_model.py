"""
Occupancy Model Training and Inference Module
=============================================
Trains and evaluates machine learning regression models to predict EV charger
utilization rate and availability probability across coarse day_time_blocks:
  - 0: overnight     (00:00-05:59)
  - 1: morning_peak  (06:00-09:59)
  - 2: midday        (10:00-15:59)
  - 3: evening_peak  (16:00-20:59)
  - 4: late_evening  (21:00-23:59)

Target Variable:
- utilization_rate: Estimated fraction of the time block the charger is occupied [0.0, 1.0]
  = min(1.0, (session_count * avg_duration_minutes) / 60.0)

Feature Definitions (Explicit, excluding session_count & avg_duration_minutes to prevent leakage):
- charger_id
- day_of_week (0=Monday .. 6=Sunday)
- day_time_block (0=overnight, 1=morning_peak, 2=midday, 3=evening_peak, 4=late_evening)
- is_weekend (1 if Saturday/Sunday, 0 otherwise)
- num_ports
- charging_power_kw

Artifacts Saved:
- models/artifacts/occupancy_model.pkl
- models/artifacts/occupancy_model_features.json
"""

import itertools
import json
import os
import sys
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, cross_val_score, train_test_split
from sqlalchemy import create_engine
from xgboost import XGBRegressor

# ---------------------------------------------------------------------------
# Path Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_PATH = os.path.join(BASE_DIR, "data", "processed", "charger_occupancy_features.csv")
ARTIFACTS_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
MODEL_PATH = os.path.join(ARTIFACTS_DIR, "occupancy_model.pkl")
FEATURES_PATH = os.path.join(ARTIFACTS_DIR, "occupancy_model_features.json")

load_dotenv(os.path.join(BASE_DIR, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------------------------------------------------------------------
# Feature & Target Definitions
# ---------------------------------------------------------------------------
TARGET_COL = "utilization_rate"

# Explicitly excluded columns to prevent data leakage (directly define target):
# session_count, avg_duration_minutes
EXCLUDED_COLS = [
    "session_count",
    "avg_duration_minutes",
    TARGET_COL,
]

FEATURE_COLS: List[str] = [
    "charger_id",
    "day_of_week",
    "day_time_block",
    "is_weekend",
    "num_ports",
    "charging_power_kw",
]

# Mapping reference for day_time_blocks
BLOCK_NAMES = {
    0: "overnight (00:00-05:59)",
    1: "morning_peak (06:00-09:59)",
    2: "midday (10:00-15:59)",
    3: "evening_peak (16:00-20:59)",
    4: "late_evening (21:00-23:59)",
}

# Global cache for loaded model and features
_CACHED_MODEL = None
_CACHED_FEATURES = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def hour_to_block(hour: int) -> int:
    """Convert hour (0-23) into day_time_block (0-4)."""
    if 0 <= hour <= 5:
        return 0
    elif 6 <= hour <= 9:
        return 1
    elif 10 <= hour <= 15:
        return 2
    elif 16 <= hour <= 20:
        return 3
    else:
        return 4


BLOCK_WIDTH_MINUTES = {
    0: 360,  # overnight (00:00-05:59) = 6 hours = 360 min
    1: 240,  # morning_peak (06:00-09:59) = 4 hours = 240 min
    2: 360,  # midday (10:00-15:59) = 6 hours = 360 min
    3: 300,  # evening_peak (16:00-20:59) = 5 hours = 300 min
    4: 180,  # late_evening (21:00-23:59) = 3 hours = 180 min
}


# ---------------------------------------------------------------------------
# Data Loading & Full Grid Construction
# ---------------------------------------------------------------------------
def load_and_construct_full_grid() -> Tuple[pd.DataFrame, pd.Series]:
    """
    Construct the full 875-row grid (25 chargers x 7 days x 5 time blocks),
    merge observed occupancy features, fill idle periods with 0, compute
    utilization_rate accounting for true block duration and port capacity,
    and merge charger hardware metadata.
    """
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Occupancy features CSV not found at: {DATA_PATH}")

    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set in environment.")

    # 1. Load chargers metadata from PostgreSQL
    engine = create_engine(DATABASE_URL, echo=False)
    chargers_df = pd.read_sql_table("chargers", engine)
    engine.dispose()
    chargers_df.columns = [str(c) for c in chargers_df.columns]

    # 2. Load observed occupancy features
    occ_df = pd.read_csv(DATA_PATH)
    occ_df.columns = [str(c) for c in occ_df.columns]

    # 3. Build complete grid of (charger_id x day_of_week x day_time_block)
    charger_ids = sorted(chargers_df["id"].unique())
    days = list(range(7))
    blocks = list(range(5))
    grid = list(itertools.product(charger_ids, days, blocks))
    full_df = pd.DataFrame(grid, columns=["charger_id", "day_of_week", "day_time_block"])

    # 4. Left-merge observed features onto full grid
    full_df = full_df.merge(
        occ_df[["charger_id", "day_of_week", "day_time_block", "session_count", "avg_duration_minutes"]],
        on=["charger_id", "day_of_week", "day_time_block"],
        how="left",
    )

    # 5. Fill idle combinations with 0 (no sessions = 0 duration = 0 utilization)
    full_df["session_count"] = full_df["session_count"].fillna(0).astype(int)
    full_df["avg_duration_minutes"] = full_df["avg_duration_minutes"].fillna(0.0).astype(float)
    full_df["is_weekend"] = (full_df["day_of_week"] >= 5).astype(int)

    # 6. Merge charger hardware specs
    chargers_sub = chargers_df[["id", "num_ports", "charging_power_kw"]].rename(
        columns={"id": "charger_id"}
    )
    chargers_sub["num_ports"] = chargers_sub["num_ports"].fillna(1).clip(lower=1).astype(int)
    chargers_sub["charging_power_kw"] = chargers_sub["charging_power_kw"].fillna(30.0).astype(float)
    full_df = full_df.merge(chargers_sub, on="charger_id", how="left")

    # 7. Compute target utilization_rate [0.0, 1.0] using true block width and port capacity
    full_df["block_width_minutes"] = full_df["day_time_block"].map(BLOCK_WIDTH_MINUTES)
    full_df["available_capacity_minutes"] = full_df["block_width_minutes"] * full_df["num_ports"]
    full_df["utilization_rate"] = np.minimum(
        1.0,
        (full_df["session_count"] * full_df["avg_duration_minutes"]) / full_df["available_capacity_minutes"]
    ).round(4)

    full_df.columns = [str(c) for c in full_df.columns]

    X = full_df[FEATURE_COLS]
    y = full_df[TARGET_COL]

    return X, y


# ---------------------------------------------------------------------------
# Training & Model Artifact Management
# ---------------------------------------------------------------------------
def train_and_save_occupancy_model() -> Dict[str, Any]:
    """
    Train and compare RandomForestRegressor and XGBRegressor on 875-row block grid
    using 80/20 train/test split and 5-fold cross-validation.
    Retrains the best model on the full 875-row dataset and saves artifacts.
    """
    print(f"Constructing 875-row grid & loading features from: {DATA_PATH} ...")
    X, y = load_and_construct_full_grid()

    print(f"Dataset constructed: {len(X)} rows ({len(X['charger_id'].unique())} chargers x 7 days x 5 time blocks)")
    print(f"Target: {TARGET_COL} (mean: {y.mean():.4f}, std: {y.std():.4f}, max: {y.max():.4f}, min: {y.min():.4f})")
    print(f"Feature columns ({len(FEATURE_COLS)}): {FEATURE_COLS}\n")

    # 80/20 train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=True
    )

    cv = KFold(n_splits=5, shuffle=True, random_state=42)

    candidate_models = {
        "RandomForestRegressor": RandomForestRegressor(
            n_estimators=100,
            max_depth=8,
            random_state=42,
        ),
        "XGBRegressor": XGBRegressor(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=4,
            random_state=42,
        ),
    }

    results: Dict[str, Dict[str, float]] = {}

    print("=" * 75)
    print(f"{'Model':<25} | {'Test R^2':<12} | {'Test MAE':<12} | {'5-Fold CV R^2':<15}")
    print("-" * 75)

    best_model_name = None
    best_score = -float("inf")

    for name, model in candidate_models.items():
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        test_r2 = float(r2_score(y_test, y_pred))
        test_mae = float(mean_absolute_error(y_test, y_pred))
        cv_r2 = float(cross_val_score(model, X, y, cv=cv, scoring="r2").mean())

        results[name] = {"test_r2": test_r2, "test_mae": test_mae, "cv_r2": cv_r2}
        print(f"{name:<25} | {test_r2:<12.4f} | {test_mae:<12.4f} | {cv_r2:<15.4f}")

        if test_r2 > best_score:
            best_score = test_r2
            best_model_name = name

    print("=" * 75)
    print(f"\nBest performing model: {best_model_name} (Test R^2 = {best_score:.4f})")

    # Retrain on full 875-sample dataset
    best_estimator = candidate_models[best_model_name]
    best_estimator.fit(X, y)

    # Save artifacts
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    joblib.dump(best_estimator, MODEL_PATH)
    with open(FEATURES_PATH, "w") as f:
        json.dump(FEATURE_COLS, f, indent=2)

    print(f"\nSaved model artifact: {MODEL_PATH}")
    print(f"Saved feature list artifact: {FEATURES_PATH}")

    # Feature Importances
    importances = best_estimator.feature_importances_
    fi_pairs = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)

    print("\n--- Feature Importances (Descending) ---")
    for feat_name, imp in fi_pairs:
        print(f"  {feat_name:<25}: {imp:.4f} ({imp * 100:.1f}%)")
    print("-" * 45)

    return {
        "best_model_name": best_model_name,
        "results": results,
        "feature_importances": dict(fi_pairs),
    }


# ---------------------------------------------------------------------------
# Inference Interface
# ---------------------------------------------------------------------------
def predict_occupancy(feature_dict: Dict[str, Any]) -> Dict[str, float]:
    """
    Inference helper function for the API layer.
    Loads saved model and feature list, builds the feature vector,
    and returns predicted utilization_rate and probability_available.

    Parameters
    ----------
    feature_dict : dict
        Dictionary containing keys:
        - charger_id: int
        - day_of_week: int (0=Monday .. 6=Sunday)
        - day_time_block: int (0-4) [or hour_of_day (0-23) which will be mapped automatically]
        - is_weekend: int (optional, derived from day_of_week if missing)
        - num_ports: int
        - charging_power_kw: float

    Returns
    -------
    dict
        {
            "utilization_rate": float in [0.0, 1.0],
            "probability_available": float in [0.0, 1.0]
        }
    """
    global _CACHED_MODEL, _CACHED_FEATURES

    if _CACHED_MODEL is None or _CACHED_FEATURES is None:
        if not os.path.exists(MODEL_PATH) or not os.path.exists(FEATURES_PATH):
            raise FileNotFoundError(
                f"Model artifacts not found. Please train the model first by running "
                f"python models/occupancy_model.py"
            )
        _CACHED_MODEL = joblib.load(MODEL_PATH)
        with open(FEATURES_PATH, "r") as f:
            _CACHED_FEATURES = json.load(f)

    input_data = dict(feature_dict)

    # Convert hour_of_day to day_time_block if day_time_block not passed
    if "day_time_block" not in input_data and "hour_of_day" in input_data:
        input_data["day_time_block"] = hour_to_block(int(input_data["hour_of_day"]))

    # Derive is_weekend if not explicitly provided
    if "is_weekend" not in input_data and "day_of_week" in input_data:
        input_data["is_weekend"] = int(int(input_data["day_of_week"]) >= 5)

    # Build vector in exact expected order
    vector: List[float] = []
    for feat in _CACHED_FEATURES:
        val = input_data.get(feat, 0.0)
        vector.append(float(val) if val is not None else 0.0)

    # Create DataFrame with exact column names to prevent estimator warnings
    df_sample = pd.DataFrame([vector], columns=_CACHED_FEATURES)
    raw_pred = float(_CACHED_MODEL.predict(df_sample)[0])

    utilization = float(np.clip(raw_pred, 0.0, 1.0))
    prob_available = float(np.clip(1.0 - utilization, 0.0, 1.0))

    return {
        "utilization_rate": round(utilization, 4),
        "probability_available": round(prob_available, 4),
    }


# ---------------------------------------------------------------------------
# Main Execution Block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        eval_summary = train_and_save_occupancy_model()

        # Sanity check: Compare Weekday morning peak vs Weekend evening for 2 chargers
        print("\n--- Sanity Check: Demand Pattern Evaluation ---")
        test_chargers = [
            {"id": 1, "name": "Urs Kar (25 kW)", "ports": 1, "power": 25.0},
            {"id": 7, "name": "Grand Mercure (120 kW)", "ports": 2, "power": 120.0},
        ]

        for c in test_chargers:
            # Weekday (Tuesday=1) Morning Peak (block 1: 06:00-09:59)
            wd_slot = {
                "charger_id": c["id"],
                "day_of_week": 1,
                "day_time_block": 1,
                "num_ports": c["ports"],
                "charging_power_kw": c["power"],
            }
            res_wd = predict_occupancy(wd_slot)

            # Weekend (Saturday=5) Evening Peak / Late Evening (block 3: 16:00-20:59)
            we_slot = {
                "charger_id": c["id"],
                "day_of_week": 5,
                "day_time_block": 3,
                "num_ports": c["ports"],
                "charging_power_kw": c["power"],
            }
            res_we = predict_occupancy(we_slot)

            print(f"Charger #{c['id']} ({c['name']}):")
            print(f"  - Weekday Morning Peak (Block 1) -> Util: {res_wd['utilization_rate']:.4f} | Availability: {res_wd['probability_available']:.4f}")
            print(f"  - Weekend Evening Peak (Block 3) -> Util: {res_we['utilization_rate']:.4f} | Availability: {res_we['probability_available']:.4f}")
        print("-" * 55)

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

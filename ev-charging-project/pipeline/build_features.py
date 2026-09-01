"""
Feature-engineering pipeline for the EV Charging platform.

Loads charger, session, review, and fault data from PostgreSQL, runs
cleaning passes, then computes two feature sets saved as CSVs:

    1. ``data/processed/charger_reliability_features.csv``
       One row per charger — session success/failure stats, fault
       history, and review aggregates. Energy and duration performance
       metrics (avg_energy_delivered_kwh, avg_session_duration_minutes)
       are computed strictly on successful sessions to prevent data
       leakage from failed session 0-energy/short-duration artifacts.

    2. ``data/processed/charger_occupancy_features.csv``
       One row per (charger, day_of_week, day_time_block) bucket —
       session counts and durations for occupancy prediction (5 daily blocks:
       0=overnight, 1=morning_peak, 2=midday, 3=evening_peak, 4=late_evening).

Usage:
    python pipeline/build_features.py
"""

import os
import sys

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

# ---------------------------------------------------------------------------
# Project paths & env
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "pipeline"))

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[ERROR] DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(1)

OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "data", "processed")
RELIABILITY_CSV = os.path.join(OUTPUT_DIR, "charger_reliability_features.csv")
OCCUPANCY_CSV = os.path.join(OUTPUT_DIR, "charger_occupancy_features.csv")

from clean_data import clean_faults, clean_reviews, clean_sessions  # noqa: E402


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_tables(engine) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the four relevant tables into DataFrames."""
    chargers = pd.read_sql_table("chargers", engine)
    sessions = pd.read_sql_table("charging_sessions", engine)
    reviews = pd.read_sql_table("reviews", engine)
    faults = pd.read_sql_table("faults", engine)
    return chargers, sessions, reviews, faults


# ---------------------------------------------------------------------------
# Reliability features (one row per charger)
# ---------------------------------------------------------------------------

def compute_reliability_features(
    chargers: pd.DataFrame,
    sessions: pd.DataFrame,
    reviews: pd.DataFrame,
    faults: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate per-charger reliability features.

    Note on data leakage prevention:
        ``avg_energy_delivered_kwh`` and ``avg_session_duration_minutes`` are
        computed *strictly* on successful sessions (status == 'success').
        Failed sessions have 0 energy and short hardcoded durations, which if
        included would act as indirect proxies for ``success_rate``.
        If a charger has 0 successful sessions, these features default to -1.0.
    """

    # ── Session counts & rates (all sessions) ───────────────────────
    sess_agg = (
        sessions.groupby("charger_id")
        .agg(
            num_sessions=("id", "count"),
            num_success=("status", lambda s: (s == "success").sum()),
            num_failed=("status", lambda s: (s == "failed").sum()),
            num_interrupted=("status", lambda s: (s == "interrupted").sum()),
        )
        .reset_index()
    )

    # Compute duration in minutes
    sessions = sessions.copy()
    sessions["duration_min"] = (
        (sessions["end_time"] - sessions["start_time"]).dt.total_seconds() / 60.0
    )

    # ── Energy & Duration (successful sessions ONLY to prevent leakage) ──
    success_sessions = sessions[sessions["status"] == "success"]
    perf_agg = (
        success_sessions.groupby("charger_id")
        .agg(
            avg_energy_delivered_kwh=("energy_delivered_kwh", "mean"),
            avg_session_duration_minutes=("duration_min", "mean"),
        )
        .reset_index()
    )
    sess_agg = sess_agg.merge(perf_agg, on="charger_id", how="left")

    # Rates
    sess_agg["success_rate"] = sess_agg["num_success"] / sess_agg["num_sessions"]
    sess_agg["failure_rate"] = sess_agg["num_failed"] / sess_agg["num_sessions"]
    sess_agg["interrupted_rate"] = sess_agg["num_interrupted"] / sess_agg["num_sessions"]

    # Reorder columns to maintain exact schema
    sess_cols = [
        "charger_id",
        "num_sessions",
        "num_success",
        "num_failed",
        "num_interrupted",
        "avg_energy_delivered_kwh",
        "avg_session_duration_minutes",
        "success_rate",
        "failure_rate",
        "interrupted_rate",
    ]
    sess_agg = sess_agg[sess_cols]

    # ── Fault aggregates ────────────────────────────────────────────
    now = pd.Timestamp.now()

    fault_agg = (
        faults.groupby("charger_id")
        .agg(
            num_faults=("id", "count"),
            num_resolved_faults=("resolved", "sum"),
            last_fault_date=("reported_at", "max"),
        )
        .reset_index()
    )
    fault_agg["resolved_fault_ratio"] = (
        fault_agg["num_resolved_faults"] / fault_agg["num_faults"]
    )
    fault_agg["days_since_last_fault"] = (
        (now - fault_agg["last_fault_date"]).dt.days
    )
    fault_agg = fault_agg.drop(columns=["last_fault_date"])

    # ── Review aggregates ───────────────────────────────────────────
    review_agg = (
        reviews.groupby("charger_id")
        .agg(
            avg_rating=("rating", "mean"),
            avg_sentiment_score=("sentiment_score", "mean"),
            review_count=("id", "count"),
            num_reviews_low=("rating", lambda r: (r <= 2).sum()),
            num_reviews_high=("rating", lambda r: (r >= 4).sum()),
        )
        .reset_index()
    )

    # ── Merge everything onto the charger list ──────────────────────
    result = chargers[["id"]].rename(columns={"id": "charger_id"})
    result = result.merge(sess_agg, on="charger_id", how="left")
    result = result.merge(fault_agg, on="charger_id", how="left")
    result = result.merge(review_agg, on="charger_id", how="left")

    # Fill chargers with no sessions/faults/reviews
    fill_zero_cols = [
        "num_sessions", "num_success", "num_failed", "num_interrupted",
        "num_faults", "num_resolved_faults", "review_count",
        "num_reviews_low", "num_reviews_high",
    ]
    for col in fill_zero_cols:
        if col in result.columns:
            result[col] = result[col].fillna(0).astype(int)

    fill_zero_float = [
        "success_rate", "failure_rate", "interrupted_rate",
        "resolved_fault_ratio",
    ]
    for col in fill_zero_float:
        if col in result.columns:
            result[col] = result[col].fillna(0.0)

    # Chargers with zero successful sessions or zero scored reviews get sentinel -1.0
    fill_sentinel_cols = [
        "avg_energy_delivered_kwh",
        "avg_session_duration_minutes",
        "avg_sentiment_score",
    ]
    for col in fill_sentinel_cols:
        if col in result.columns:
            result[col] = result[col].fillna(-1.0)

    # Round floats for readability
    float_cols = result.select_dtypes(include="float").columns
    result[float_cols] = result[float_cols].round(4)

    return result


# ---------------------------------------------------------------------------
# Occupancy features (one row per charger × day_of_week × day_time_block)
# ---------------------------------------------------------------------------

def get_day_time_block(hour: int) -> int:
    """Map hour of day (0-23) to 5 coarse day_time_blocks:
    0 = overnight     (00:00-05:59)
    1 = morning_peak  (06:00-09:59)
    2 = midday        (10:00-15:59)
    3 = evening_peak  (16:00-20:59)
    4 = late_evening  (21:00-23:59)
    """
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


def compute_occupancy_features(
    chargers: pd.DataFrame,
    sessions: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate per-charger time-block occupancy features.

    Collapses 24 hourly buckets into 5 coarse ``day_time_block`` categories
    to reduce per-bucket sparsity:
      - 0: overnight (00:00-05:59) = 360 min
      - 1: morning_peak (06:00-09:59) = 240 min
      - 2: midday (10:00-15:59) = 360 min
      - 3: evening_peak (16:00-20:59) = 300 min
      - 4: late_evening (21:00-23:59) = 180 min

    Utilization rate is computed relative to the block's true temporal width
    and station port capacity:
      available_capacity_minutes = block_width_minutes * max(num_ports, 1)
      utilization_rate = min(1.0, (session_count * avg_duration_minutes) / available_capacity_minutes)

    .. note::
        "Holiday" as an occupancy feature is not available yet —
        ``is_weekend`` is used as a rough proxy.
    """
    sessions = sessions.copy()
    sessions["day_of_week"] = sessions["start_time"].dt.dayofweek   # 0=Mon..6=Sun
    sessions["hour_of_day"] = sessions["start_time"].dt.hour        # 0-23
    sessions["day_time_block"] = sessions["hour_of_day"].apply(get_day_time_block)
    sessions["duration_min"] = (
        (sessions["end_time"] - sessions["start_time"]).dt.total_seconds() / 60.0
    )

    occ = (
        sessions.groupby(["charger_id", "day_of_week", "day_time_block"])
        .agg(
            session_count=("id", "count"),
            avg_duration_minutes=("duration_min", "mean"),
        )
        .reset_index()
    )

    # Derive is_weekend from day_of_week
    occ["is_weekend"] = occ["day_of_week"] >= 5

    # Merge num_ports from chargers table
    ports_df = chargers[["id", "num_ports"]].rename(columns={"id": "charger_id"})
    ports_df["num_ports"] = ports_df["num_ports"].fillna(1).clip(lower=1).astype(int)
    occ = occ.merge(ports_df, on="charger_id", how="left")
    occ["num_ports"] = occ["num_ports"].fillna(1).clip(lower=1).astype(int)

    # Compute utilization_rate accounting for block duration and port count
    block_widths = occ["day_time_block"].map(BLOCK_WIDTH_MINUTES)
    available_capacity = block_widths * occ["num_ports"]
    occ["utilization_rate"] = np.minimum(
        1.0,
        (occ["session_count"] * occ["avg_duration_minutes"]) / available_capacity
    ).round(4)

    # Round for readability
    occ["avg_duration_minutes"] = occ["avg_duration_minutes"].round(2)

    return occ.sort_values(["charger_id", "day_of_week", "day_time_block"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    engine = create_engine(DATABASE_URL, echo=False)

    # ── Load ────────────────────────────────────────────────────────
    print("Loading tables from database ...")
    chargers, sessions_raw, reviews_raw, faults_raw = load_tables(engine)
    engine.dispose()

    print(f"  chargers          : {len(chargers)} rows")
    print(f"  charging_sessions : {len(sessions_raw)} rows")
    print(f"  reviews           : {len(reviews_raw)} rows")
    print(f"  faults            : {len(faults_raw)} rows")
    print()

    # ── Clean ───────────────────────────────────────────────────────
    print("=== Cleaning ===")
    sessions = clean_sessions(sessions_raw)
    reviews = clean_reviews(reviews_raw)
    faults = clean_faults(faults_raw)
    print()

    # ── Feature engineering ─────────────────────────────────────────
    print("=== Building features ===")

    reliability = compute_reliability_features(chargers, sessions, reviews, faults)
    occupancy = compute_occupancy_features(chargers, sessions)

    # ── Save ────────────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    reliability.to_csv(RELIABILITY_CSV, index=False)
    print(f"  Saved reliability features : {RELIABILITY_CSV}")
    print(f"    {len(reliability)} chargers, {len(reliability.columns)} columns")

    occupancy.to_csv(OCCUPANCY_CSV, index=False)
    print(f"  Saved occupancy features   : {OCCUPANCY_CSV}")
    print(f"    {len(occupancy)} charger/hour buckets, {len(occupancy.columns)} columns")

    # ── Sanity stats ────────────────────────────────────────────────
    print("\n=== Sanity Check ===")
    sr = reliability["success_rate"]
    print(f"  success_rate  -> min={sr.min():.4f}  max={sr.max():.4f}  mean={sr.mean():.4f}")
    fr = reliability["failure_rate"]
    print(f"  failure_rate  -> min={fr.min():.4f}  max={fr.max():.4f}  mean={fr.mean():.4f}")
    ar = reliability["avg_rating"]
    ar_clean = ar.dropna()
    if len(ar_clean):
        print(f"  avg_rating    -> min={ar_clean.min():.2f}    max={ar_clean.max():.2f}    mean={ar_clean.mean():.2f}")
    print()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

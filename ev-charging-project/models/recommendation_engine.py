"""
Recommendation Engine Module
============================
Integrates predicted charger reliability, temporal occupancy forecasts,
geographical distance, estimated charging duration, and pricing into a
unified multi-criteria ranking score for EV drivers.

Key Functions:
- load_charger_data(): Loads and caches merged charger specifications and reliability features.
- haversine_km(): Computes great-circle geographical distance.
- estimate_charging_time_minutes(): Estimates session charging duration with CC-CV tapering overhead.
- estimate_cost_inr(): Estimates total charging cost based on energy required.
- get_day_time_block(): Maps a datetime to (day_of_week, day_time_block).
- score_and_rank_chargers(): Scores and ranks candidate chargers.
"""

import concurrent.futures
import logging
import math
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path Configuration & Imports
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(BASE_DIR, "models"))
sys.path.insert(0, os.path.join(BASE_DIR, "database"))

load_dotenv(os.path.join(BASE_DIR, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")
RELIABILITY_CSV_PATH = os.path.join(BASE_DIR, "data", "processed", "charger_reliability_features.csv")

from occupancy_model import predict_occupancy  # noqa: E402
from reliability_model import predict_reliability  # noqa: E402
from operational_safety import (  # noqa: E402
    OperationalStatus,
    OperationalPolicy,
    OperationalEligibilityResult,
    check_operational_eligibility,
    compute_station_trust_score,
    global_feedback_manager,
)

# ---------------------------------------------------------------------------
# Energy Consumption & Safety Buffer Constants
# ---------------------------------------------------------------------------
DEFAULT_ENERGY_CONSUMPTION_KWH_PER_KM: float = 0.15  # 150 Wh/km (typical Indian EV fleet average)
DEFAULT_RESERVE_BATTERY_PERCENT: float = 5.0        # 5.0% buffer reserved to prevent cell damage & stranding


class VehicleStrandedException(Exception):
    """
    Raised when the vehicle's remaining state-of-charge (usable range) is
    insufficient to reach any candidate charging station within search radius.
    """

    def __init__(self, usable_range_km: float, current_soc_percent: float, min_distance_km: float = 0.0):
        self.usable_range_km = usable_range_km
        self.current_soc_percent = current_soc_percent
        self.min_distance_km = min_distance_km
        super().__init__(
            f"No reachable chargers: your estimated usable range ({usable_range_km:.1f} km at {current_soc_percent:.1f}% SoC) "
            f"cannot reach any station in range (closest station requires {min_distance_km:.1f} km). "
            f"Please consider emergency charging options or reducing target distance."
        )


def compute_usable_range_km(
    current_soc_percent: float,
    battery_capacity_kwh: float,
    energy_consumption_kwh_per_km: float = DEFAULT_ENERGY_CONSUMPTION_KWH_PER_KM,
    reserve_battery_percent: float = DEFAULT_RESERVE_BATTERY_PERCENT,
) -> float:
    """
    Compute estimated physical driving range (in km) remaining in battery,
    minus an unusable safety buffer reserve.
    """
    usable_soc_percent = max(0.0, current_soc_percent - reserve_battery_percent)
    usable_energy_kwh = (usable_soc_percent / 100.0) * battery_capacity_kwh
    if energy_consumption_kwh_per_km <= 0.0:
        return 0.0
    return usable_energy_kwh / energy_consumption_kwh_per_km


# ---------------------------------------------------------------------------
# In-Memory Cache
# ---------------------------------------------------------------------------
_CACHED_CHARGERS_DF: Optional[pd.DataFrame] = None


# ---------------------------------------------------------------------------
# Data Loader
# ---------------------------------------------------------------------------
def load_charger_data() -> pd.DataFrame:
    """
    Load and join the `chargers` table from PostgreSQL with the processed
    `charger_reliability_features.csv` dataset.
    Caches the resulting DataFrame in memory on first call.
    Gracefully falls back to CSV features if DB connection is unavailable.
    """
    global _CACHED_CHARGERS_DF
    if _CACHED_CHARGERS_DF is not None:
        return _CACHED_CHARGERS_DF

    chargers_df = None
    if DATABASE_URL:
        try:
            engine = create_engine(DATABASE_URL, echo=False)
            chargers_df = pd.read_sql_table("chargers", engine)
            engine.dispose()
            chargers_df.columns = [str(c) for c in chargers_df.columns]
        except Exception as db_err:
            logger.warning("PostgreSQL connection unavailable: %s. Using fallback charger dataset.", db_err)
            chargers_df = None

    rel_df = pd.read_csv(RELIABILITY_CSV_PATH) if os.path.exists(RELIABILITY_CSV_PATH) else pd.DataFrame()
    rel_df.columns = [str(c) for c in rel_df.columns]

    if chargers_df is None or chargers_df.empty:
        if not rel_df.empty:
            chargers_df = rel_df.copy()
            chargers_df["id"] = chargers_df["charger_id"]
            chargers_df["name"] = chargers_df["charger_id"].apply(lambda cid: f"Station #{cid}")
            if "latitude" not in chargers_df.columns:
                chargers_df["latitude"] = 12.0 + (chargers_df.index % 50) * 0.04
                chargers_df["longitude"] = 77.0 + (chargers_df.index % 50) * 0.03
            if "charging_power_kw" not in chargers_df.columns:
                chargers_df["charging_power_kw"] = 50.0
            if "connector_type" not in chargers_df.columns:
                chargers_df["connector_type"] = "CCS2"
            if "operational_status" not in chargers_df.columns:
                chargers_df["operational_status"] = "AVAILABLE"
            if "status_confidence" not in chargers_df.columns:
                chargers_df["status_confidence"] = 1.0
        else:
            chargers_df = pd.DataFrame()

    # Use LEFT join so every DB station is kept regardless of whether the
    # reliability CSV has a matching row (e.g. newly-ingested OCM stations).
    # Missing reliability fields are filled with conservative defaults below.
    merged_df = chargers_df.merge(rel_df, left_on="id", right_on="charger_id", how="left") if (
        not rel_df.empty and "charger_id" in rel_df.columns and "id" in chargers_df.columns and chargers_df is not rel_df
    ) else chargers_df

    merged_df["num_ports"] = merged_df.get("num_ports", pd.Series(1, index=merged_df.index)).fillna(1).clip(lower=1).astype(int)
    merged_df["charging_power_kw"] = (
        merged_df.get("charging_power_kw", pd.Series(50.0, index=merged_df.index)).fillna(50.0).clip(lower=3.3).astype(float)
    )
    if "operational_status" not in merged_df.columns:
        merged_df["operational_status"] = "AVAILABLE"
    if "status_confidence" not in merged_df.columns:
        merged_df["status_confidence"] = 1.0
    if "reliability" not in merged_df.columns:
        merged_df["reliability"] = merged_df.get("success_rate", pd.Series(0.90, index=merged_df.index)).fillna(0.90)
    if "probability_available" not in merged_df.columns:
        merged_df["probability_available"] = 0.85

    _CACHED_CHARGERS_DF = merged_df
    return _CACHED_CHARGERS_DF


# ---------------------------------------------------------------------------
# Geospatial, Routing & Physics Helpers
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two geographic coordinates in km.
    Used as an initial fast spatial filter before routing calculations.
    """
    R = 6371.0  # Earth radius in kilometers
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(R * c, 3)


def get_road_route(
    user_lat: float,
    user_lon: float,
    charger_lat: float,
    charger_lon: float,
    include_geometry: bool = False,
    timeout_seconds: float = 3.0,
) -> Dict[str, Any]:
    """
    Query OSRM public demo routing service for driving road distance and travel time.

    IMPORTANT:
      OSRM expects coordinate ordering as {longitude},{latitude}.

    Parameters
    ----------
    user_lat : float
        User origin latitude.
    user_lon : float
        User origin longitude.
    charger_lat : float
        Charger destination latitude.
    charger_lon : float
        Charger destination longitude.
    include_geometry : bool, default False
        Whether to request full GeoJSON route geometry from OSRM.
    timeout_seconds : float, default 3.0
        HTTP request timeout in seconds.

    Returns
    -------
    dict
        {
            "distance_km": float,
            "travel_time_minutes": float,
            "geometry": Optional[dict],
            "is_fallback": bool
        }
    """
    overview_param = "full" if include_geometry else "false"
    url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{user_lon},{user_lat};{charger_lon},{charger_lat}"
        f"?overview={overview_param}&geometries=geojson"
    )

    try:
        response = requests.get(url, timeout=timeout_seconds)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == "Ok" and data.get("routes") and len(data["routes"]) > 0:
                route = data["routes"][0]
                dist_meters = float(route.get("distance", 0.0))
                dur_seconds = float(route.get("duration", 0.0))

                dist_km = round(dist_meters / 1000.0, 2)
                travel_min = round(dur_seconds / 60.0, 1)
                geometry = route.get("geometry") if include_geometry else None

                return {
                    "distance_km": dist_km,
                    "travel_time_minutes": travel_min,
                    "geometry": geometry,
                    "is_fallback": False,
                }
            else:
                logger.warning(f"[OSRM] Non-OK route response code: {data.get('code')}")
        else:
            logger.warning(f"[OSRM] HTTP status {response.status_code} from routing server")
    except Exception as exc:
        logger.warning(f"[OSRM Fallback] Routing request failed ({exc}). Falling back to Haversine x1.3.")

    # Graceful fallback: Haversine distance * 1.3 approximation
    straight_km = haversine_km(user_lat, user_lon, charger_lat, charger_lon)
    fallback_dist_km = round(straight_km * 1.3, 2)
    # Estimate travel time assuming ~30 km/h average Indian urban driving speed
    fallback_travel_min = round((fallback_dist_km / 30.0) * 60.0, 1)

    fallback_geometry = (
        {"type": "LineString", "coordinates": [[user_lon, user_lat], [charger_lon, charger_lat]]}
        if include_geometry
        else None
    )

    return {
        "distance_km": fallback_dist_km,
        "travel_time_minutes": fallback_travel_min,
        "geometry": fallback_geometry,
        "is_fallback": True,
    }


def estimate_charging_time_minutes(
    current_soc: float,
    target_soc: float,
    battery_capacity_kwh: float,
    charging_power_kw: float,
    taper_factor: float = 1.175,
) -> float:
    """
    Estimate charging duration in minutes from current SOC to target SOC.
    Includes a ~17.5% average overhead for CC-CV charging curve tapering and setup,
    consistent with the data generation pipeline.
    """
    if target_soc <= current_soc or battery_capacity_kwh <= 0 or charging_power_kw <= 0:
        return 0.0

    soc_delta = target_soc - current_soc
    energy_kwh = (battery_capacity_kwh * soc_delta) / 100.0
    hours = (energy_kwh / charging_power_kw) * taper_factor
    duration_min = max(5.0, hours * 60.0)
    return round(duration_min, 1)


def estimate_cost_inr(energy_needed_kwh: float, rate_per_kwh: float = 17.0) -> float:
    """Estimate charging session energy cost in INR (default ₹17.0 / kWh)."""
    return round(max(0.0, energy_needed_kwh) * rate_per_kwh, 2)


def get_day_time_block(dt: datetime) -> Tuple[int, int]:
    """
    Convert a datetime into (day_of_week, day_time_block) matching the occupancy model:
      - day_of_week: 0=Monday .. 6=Sunday
      - day_time_block:
          0 = overnight     (00:00-05:59)
          1 = morning_peak  (06:00-09:59)
          2 = midday        (10:00-15:59)
          3 = evening_peak  (16:00-20:59)
          4 = late_evening  (21:00-23:59)
    """
    dow = dt.weekday()
    hour = dt.hour

    if 0 <= hour <= 5:
        block = 0
    elif 6 <= hour <= 9:
        block = 1
    elif 10 <= hour <= 15:
        block = 2
    elif 16 <= hour <= 20:
        block = 3
    else:
        block = 4

    return dow, block


def is_connector_compatible(user_connector: str, charger_connector: Optional[str]) -> bool:
    """
    Check if a charger's connector standard is compatible with the user vehicle connector.

    Handles real-world EV naming variants:
      - 'CCS2' / 'CCS-2' / 'CCS' matches any CCS Combo 2 station ('CCS (Type 2)', 'CCS2').
        It correctly excludes pure AC Type 2 stations ('Type 2 (Socket Only)', etc.) and Unknown.
      - 'Type 2' / 'Type2' matches 'Type 2' stations (including CCS Combo 2 inlets which support Type 2).
      - 'CHAdeMO' matches CHAdeMO stations.
      - 'GB/T' matches GB/T stations.
    """
    if not charger_connector or not user_connector:
        return False

    u = user_connector.strip().lower()
    c = str(charger_connector).strip().lower()

    if c in ("unknown", "none", ""):
        return False

    u_clean = u.replace("-", "").replace(" ", "")

    # CCS2 / CCS standard (e.g. Tata Nexon EV, MG ZS EV, Hyundai Kona)
    if "ccs" in u_clean or "combo2" in u_clean:
        return "ccs" in c or "combo 2" in c or "combo2" in c

    # Type 2 AC standard (e.g. MG Comet EV, Citroen eC3, Tiago EV)
    if "type2" in u_clean or "type 2" in u:
        return "type 2" in c or "type2" in c or "ccs" in c

    # CHAdeMO
    if "chademo" in u_clean:
        return "chademo" in c

    # GB/T (e.g. legacy Mahindra e-Verito / Tigor EV fleet)
    if "gbt" in u_clean or "gb/t" in u:
        return "gb/t" in c or "gbt" in c

    # Direct substring match fallback
    return u in c or c in u


# ---------------------------------------------------------------------------
# Multi-Criteria Recommendation & Ranking Engine
# ---------------------------------------------------------------------------
def score_and_rank_chargers(
    user_lat: float,
    user_lon: float,
    connector_type: str,
    battery_capacity_kwh: float,
    current_soc_percent: float,
    target_soc_percent: float = 90.0,
    arrival_datetime: Optional[datetime] = None,
    max_search_radius_km: float = 25.0,
    top_n: int = 5,
    weights: Optional[Dict[str, float]] = None,
    energy_consumption_kwh_per_km: float = DEFAULT_ENERGY_CONSUMPTION_KWH_PER_KM,
    reserve_battery_percent: float = DEFAULT_RESERVE_BATTERY_PERCENT,
    debug_mode: bool = False,
    operational_policy: Optional[OperationalPolicy] = None,
) -> List[Dict[str, Any]]:
    """
    Score and rank charging stations within radius that are physically reachable
    given remaining battery charge, optimizing across reliability, availability,
    road distance, duration, and session cost.

    Parameters
    ----------
    user_lat : float
        Current latitude of the user/vehicle.
    user_lon : float
        Current longitude of the user/vehicle.
    connector_type : str
        User vehicle connector standard (e.g. 'CCS2', 'Type 2', 'GB/T').
    battery_capacity_kwh : float
        Total vehicle battery capacity in kWh.
    current_soc_percent : float
        Current state-of-charge percentage (0-100).
    target_soc_percent : float, default 90.0
        Desired state-of-charge percentage.
    arrival_datetime : datetime, optional
        Anticipated arrival time. Defaults to datetime.now().
    max_search_radius_km : float, default 25.0
        Maximum search radius in kilometers.
    top_n : int, default 5
        Number of top recommendations to return (ignored when debug_mode=True).
    weights : dict, optional
        Custom weights dictionary with keys:
        'reliability', 'availability', 'distance', 'cost', 'charging_time'.
    energy_consumption_kwh_per_km : float, default 0.15
        Average EV energy consumption in kWh/km (150 Wh/km).
    reserve_battery_percent : float, default 5.0
        Safety reserve buffer percentage subtracted from current SoC.
    debug_mode : bool, default False
        When True, returns all candidates within radius with normalized components.
    operational_policy : OperationalPolicy, optional
        Configurable operational safety policy for station eligibility gate.

    Returns
    -------
    list of dict
        Top ranked chargers sorted by final_score descending (or all if debug_mode=True).

    Raises
    ------
    VehicleStrandedException
        If the remaining battery charge cannot reach any charging station in range.
    """
    if arrival_datetime is None:
        arrival_datetime = datetime.now()

    # Default multi-criteria weighting configuration
    default_weights = {
        "reliability": 0.35,
        "availability": 0.25,
        "distance": 0.20,
        "cost": 0.10,
        "charging_time": 0.10,
    }
    w = dict(default_weights)
    if weights:
        w.update(weights)

    chargers_df = load_charger_data()
    dow, time_block = get_day_time_block(arrival_datetime)

    energy_needed_kwh = max(0.0, (target_soc_percent - current_soc_percent) / 100.0) * battery_capacity_kwh

    # Physical Reachability Computation
    usable_range_km = compute_usable_range_km(
        current_soc_percent=current_soc_percent,
        battery_capacity_kwh=battery_capacity_kwh,
        energy_consumption_kwh_per_km=energy_consumption_kwh_per_km,
        reserve_battery_percent=reserve_battery_percent,
    )

    # 1. Filter by radius, connector compatibility & evaluate operational eligibility
    candidates_raw: List[Dict[str, Any]] = []

    for _, row in chargers_df.iterrows():
        c_lat = float(row["latitude"])
        c_lon = float(row["longitude"])
        dist_km = haversine_km(user_lat, user_lon, c_lat, c_lon)

        if dist_km <= max_search_radius_km:
            c_conn = row.get("connector_type")
            is_compat = is_connector_compatible(connector_type, c_conn)
            row_dict = dict(row)
            eligibility = check_operational_eligibility(
                row_dict,
                policy=operational_policy,
                feedback_mgr=global_feedback_manager,
                as_of=arrival_datetime,
            )
            candidates_raw.append({
                "row": row,
                "distance_km": dist_km,
                "is_compatible": is_compat,
                "eligibility": eligibility,
            })

    # Geographic empty search radius
    if not candidates_raw:
        return []

    # Fast Stranded Pre-Check: If usable range is zero or strictly less than the closest station in radius
    min_cand_dist = min(c["distance_km"] for c in candidates_raw)
    if usable_range_km <= 0.0 or min_cand_dist > usable_range_km:
        raise VehicleStrandedException(
            usable_range_km=usable_range_km,
            current_soc_percent=current_soc_percent,
            min_distance_km=round(min_cand_dist, 2),
        )

    # 2. Operational Safety Gate & Connector Compatibility Filtering
    if debug_mode:
        # In debug mode, evaluate and return all candidates in radius with operational flags
        active_candidates = candidates_raw
    else:
        # In normal mode, strictly filter out candidates that fail the operational eligibility gate
        eligible_candidates = [c for c in candidates_raw if c["eligibility"].eligible]
        if not eligible_candidates:
            return []
        compat_candidates = [c for c in eligible_candidates if c["is_compatible"]]
        active_candidates = compat_candidates if len(compat_candidates) > 0 else eligible_candidates

    # 3. Capture Full-Pool Normalization Bounds (Before Top-K Trimming)
    # Crucial Stability Fix: Locking in min-max reference statistics across the FULL radius-and-compatibility
    # candidate set ensures that trimming distant outliers (to control OSRM routing calls) does not alter the
    # normalization denominator and distort the relative ranking of the remaining candidates.
    full_distances = [float(c["distance_km"]) for c in active_candidates]
    full_times = [
        estimate_charging_time_minutes(
            current_soc_percent,
            target_soc_percent,
            battery_capacity_kwh,
            float(c["row"]["charging_power_kw"]),
        )
        for c in active_candidates
    ]
    full_costs = [estimate_cost_inr(energy_needed_kwh) for _ in active_candidates]

    d_min, d_max = min(full_distances), max(full_distances)
    t_min, t_max = min(full_times), max(full_times)
    c_min, c_max = min(full_costs), max(full_costs)

    # 4. Spatial Pre-Filtering: Top-K Nearest by Straight-Line Distance
    # To bound external OSRM network calls regardless of candidate density (e.g. 100+ candidates in Bengaluru),
    # we pre-filter to the K closest candidates by haversine distance before making routing requests.
    # Formula: K = max(20, top_n * 4).
    # Concurrency Cap: Max 8 worker threads to avoid overwhelming/rate-limiting on public demo OSRM servers.
    k_limit = max(20, top_n * 4)
    active_candidates = sorted(active_candidates, key=lambda c: c["distance_km"])[:k_limit]

    # 5. Compute Metrics for each candidate concurrently (OSRM Routing + ML Inference)
    def _evaluate_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
        row = item["row"]
        c_id = int(row["id"])
        c_lat = float(row["latitude"])
        c_lon = float(row["longitude"])
        c_power = float(row["charging_power_kw"])
        c_ports = int(row["num_ports"])

        # OSRM Driving Road Distance & Travel Time (with per-candidate fallback)
        route_info = get_road_route(user_lat, user_lon, c_lat, c_lon, include_geometry=False)
        road_dist_km = route_info["distance_km"]
        travel_time_min = route_info["travel_time_minutes"]

        # Model Inference: Reliability
        rel_score = predict_reliability(dict(row))

        # Model Inference: Occupancy / Availability Probability
        occ_res = predict_occupancy({
            "charger_id": c_id,
            "day_of_week": dow,
            "day_time_block": time_block,
            "num_ports": c_ports,
            "charging_power_kw": c_power,
        })
        prob_available = occ_res["probability_available"]

        # Charging physics & economics
        chg_time_min = estimate_charging_time_minutes(
            current_soc_percent, target_soc_percent, battery_capacity_kwh, c_power
        )
        cost_inr = estimate_cost_inr(energy_needed_kwh)

        elig = item.get("eligibility")
        if elig is None:
            elig = check_operational_eligibility(
                dict(row),
                policy=operational_policy,
                feedback_mgr=global_feedback_manager,
                as_of=arrival_datetime,
            )

        return {
            "charger_id": c_id,
            "name": str(row["name"]),
            "operator": str(row.get("operator", "") or ""),
            "address": str(row.get("address", "") or ""),
            "city": str(row.get("city", "") or ""),
            "latitude": c_lat,
            "longitude": c_lon,
            "charging_power_kw": c_power,
            "num_ports": c_ports,
            "connector_type": str(row.get("connector_type") or "None"),
            "distance_km": round(road_dist_km, 2),
            "travel_time_minutes": round(travel_time_min, 1),
            "reliability": round(rel_score, 4),
            "availability": round(prob_available, 4),
            "probability_available": round(prob_available, 4),
            "estimated_charging_time_minutes": round(chg_time_min, 1),
            "estimated_cost_inr": round(cost_inr, 2),
            "compatible": bool(item["is_compatible"]),
            "usable_range_km": round(usable_range_km, 1),
            "operational_status": elig.operational_status.value,
            "status_confidence": elig.status_confidence,
            "status_last_updated": elig.status_last_updated_iso,
            "eligible_for_planning": elig.eligible,
            "rejection_reason": elig.rejection_reason,
            "trust_score": elig.trust_score,
        }

    # Execute concurrent OSRM & inference calls with max_workers=8
    max_workers = min(8, max(1, len(active_candidates)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        candidate_items = list(executor.map(_evaluate_candidate, active_candidates))

    # Hard Reachability Filter on true road driving distance
    reachable_candidates = [c for c in candidate_items if c["distance_km"] <= usable_range_km]
    if not reachable_candidates:
        min_road = min(c["distance_km"] for c in candidate_items) if candidate_items else 0.0
        raise VehicleStrandedException(
            usable_range_km=usable_range_km,
            current_soc_percent=current_soc_percent,
            min_distance_km=round(min_road, 2),
        )
    candidate_items = reachable_candidates

    # 6. Min-Max Normalization using Full-Pool Reference Bounds (Clamped to [0.0, 1.0])
    for c in candidate_items:
        norm_dist = (c["distance_km"] - d_min) / (d_max - d_min) if d_max > d_min else 0.0
        norm_time = (c["estimated_charging_time_minutes"] - t_min) / (t_max - t_min) if t_max > t_min else 0.0
        norm_cost = (c["estimated_cost_inr"] - c_min) / (c_max - c_min) if c_max > c_min else 0.0

        # Clamp normalized components to [0.0, 1.0] to preserve weight semantics
        # even when road distance/time exceeds straight-line reference bounds at search boundaries.
        norm_dist = max(0.0, min(1.0, norm_dist))
        norm_cost = max(0.0, min(1.0, norm_cost))
        norm_time = max(0.0, min(1.0, norm_time))

        # Multi-criteria utility score (higher is better)
        # Benefits: reliability, probability_available
        # Costs/Penalties: distance, charging_time, session cost
        final_score = (
            w["reliability"] * c["reliability"]
            + w["availability"] * c["probability_available"]
            - w["distance"] * norm_dist
            - w["cost"] * norm_cost
            - w["charging_time"] * norm_time
        )
        c["normalized_distance"] = round(float(norm_dist), 4)
        c["normalized_cost"] = round(float(norm_cost), 4)
        c["normalized_charging_time"] = round(float(norm_time), 4)
        c["final_score"] = round(float(final_score), 4)

    # 5. Sort descending by final score
    ranked_candidates = sorted(candidate_items, key=lambda x: x["final_score"], reverse=True)

    if debug_mode:
        return ranked_candidates

    return ranked_candidates[:top_n]


# ---------------------------------------------------------------------------
# Main Execution Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        city_lat = float(os.getenv("CITY_LAT", 12.2958))
        city_lon = float(os.getenv("CITY_LON", 76.6394))

        print("=" * 165)
        print("EV Charging Recommendation Engine -- Road Routing & Full Evaluation (Debug Mode)")
        print(f"User Location: Mysore City Center ({city_lat:.4f}, {city_lon:.4f})")
        print("Vehicle: Tata Nexon EV (30.0 kWh battery, CCS2 connector)")
        print("State of Charge: 20.0% -> Target: 90.0% (Energy Required: 21.0 kWh, Est. Cost: INR 357.00)")
        print("Search Radius: 25.0 km | Weights: Rel=0.35, Avail=0.25, Dist=0.20, Cost=0.10, Time=0.10")
        print("=" * 165)

        all_candidates = score_and_rank_chargers(
            user_lat=city_lat,
            user_lon=city_lon,
            connector_type="CCS2",
            battery_capacity_kwh=30.0,
            current_soc_percent=20.0,
            target_soc_percent=90.0,
            max_search_radius_km=25.0,
            debug_mode=True,
        )

        num_matching = sum(1 for c in all_candidates if c["compatible"])
        fallback_triggered = not any(c["compatible"] for c in all_candidates)

        print(f"\nTotal Candidate Stations Found Within 25.0 km: {len(all_candidates)}")
        print(
            f"Compatibility Summary: {num_matching} of {len(all_candidates)} candidates have connector_type matching 'CCS2'. "
            f"Fallback path (include-incompatible) triggered: {fallback_triggered}.\n"
        )

        print(
            f"{'Rank':<4} | {'Charger Name':<28} | {'Connector Type in DB':<28} | {'Compat':<6} | {'Power':<6} | "
            f"{'Road Dist':<9} | {'Drive':<6} | {'Rel':<6} | {'Avail':<6} | "
            f"{'Charge':<7} | {'Norm D':<6} | {'Score':<7}"
        )
        print("-" * 165)

        for rank, r in enumerate(all_candidates, 1):
            print(
                f"#{rank:<3} | {r['name']:<28} | {r['connector_type']:<28} | {str(r['compatible']):<6} | {r['charging_power_kw']:>3.0f}kW | "
                f"{r['distance_km']:>6.2f}km | {r['travel_time_minutes']:>4.1f}m | {r['reliability']:>6.3f} | {r['probability_available']:>6.3f} | "
                f"{r['estimated_charging_time_minutes']:>4.0f}m | {r['normalized_distance']:>6.3f} | "
                f"{r['final_score']:>7.4f}"
            )

        print("=" * 165)

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


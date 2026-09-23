"""
FastAPI Backend API Module
==========================
Exposes RESTful endpoints for the AI-powered EV charging recommendation platform:
  - GET /health: Health and database connectivity status.
  - POST /recommend: Multi-criteria ranked charging station recommendations.

Usage:
    uvicorn api.main:app --reload --port 8000
"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Path Configuration & Models Import
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))

from recommendation_engine import (  # noqa: E402
    load_charger_data,
    score_and_rank_chargers,
    get_road_route,
    VehicleStrandedException,
)
from energy_prediction_service import energy_prediction_service  # noqa: E402
from joint_route_charging_engine import (  # noqa: E402
    VehicleState,
    ChargerCandidate,
    JointPlanResult,
    evaluate_joint_route_and_charging,
)
from modified_astar import RoutingGraph  # noqa: E402
from operational_safety import (  # noqa: E402
    OperationalStatus,
    OperationalPolicy,
    FeedbackResult,
    ChargingFeedbackEvent,
    global_feedback_manager,
    check_operational_eligibility,
    compute_station_trust_score,
)
from multi_stop_planner import (  # noqa: E402
    MultiStopTripPlanner,
    MultiStopPlanResult,
    ChargingStopDetail,
    JourneyLeg,
)


# ---------------------------------------------------------------------------
# Application Lifespan (Pre-warming in-memory cache)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm in-memory cache and ML models on startup."""
    try:
        df = load_charger_data()
        print(f"[API Startup] Pre-warmed cache with {len(df)} charging stations.")
    except Exception as exc:
        print(f"[API Startup Warning] Failed to pre-warm cache: {exc}", file=sys.stderr)
    yield


# ---------------------------------------------------------------------------
# FastAPI App & Middleware
# ---------------------------------------------------------------------------
app = FastAPI(
    title="EV Charging Intelligence API",
    description="AI-powered EV charging recommendation platform for India",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------
class RecommendationRequest(BaseModel):
    user_lat: float = Field(
        ...,
        description="Current latitude coordinate of the vehicle / user",
        examples=[12.2958],
    )
    user_lon: float = Field(
        ...,
        description="Current longitude coordinate of the vehicle / user",
        examples=[76.6394],
    )
    connector_type: str = Field(
        ...,
        min_length=1,
        description="User vehicle connector standard (e.g. 'CCS2', 'Type 2', 'GB/T')",
        examples=["CCS2"],
    )
    battery_capacity_kwh: float = Field(
        ...,
        gt=0.0,
        description="Total battery capacity in kWh",
        examples=[30.0],
    )
    current_soc_percent: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Current State of Charge percentage (0.0 to 100.0)",
        examples=[20.0],
    )
    target_soc_percent: float = Field(
        default=90.0,
        ge=0.0,
        le=100.0,
        description="Target State of Charge percentage (0.0 to 100.0)",
        examples=[90.0],
    )
    arrival_datetime: Optional[datetime] = Field(
        default=None,
        description="Anticipated arrival datetime (defaults to current time if omitted)",
        examples=["2026-08-25T16:00:00"],
    )
    max_search_radius_km: float = Field(
        default=25.0,
        gt=0.0,
        description="Maximum search radius in kilometers (must be positive)",
        examples=[25.0],
    )
    top_n: int = Field(
        default=5,
        ge=1,
        le=25,
        description="Number of ranked recommendations to return (1 to 25)",
        examples=[5],
    )
    energy_consumption_kwh_per_km: float = Field(
        default=0.15,
        gt=0.0,
        description="Average vehicle energy consumption in kWh/km (default 0.15 = 150 Wh/km)",
        examples=[0.15],
    )
    reserve_battery_percent: float = Field(
        default=5.0,
        ge=0.0,
        le=50.0,
        description="Safety reserve battery buffer percentage (default 5.0%)",
        examples=[5.0],
    )

    @model_validator(mode="after")
    def validate_soc_range(self) -> "RecommendationRequest":
        if self.target_soc_percent <= self.current_soc_percent:
            raise ValueError(
                f"target_soc_percent ({self.target_soc_percent}) must be strictly greater than "
                f"current_soc_percent ({self.current_soc_percent})"
            )
        return self


class ChargerRecommendation(BaseModel):
    charger_id: int = Field(..., description="Database identifier of the charger")
    name: str = Field(..., description="Name of the charging station")
    operator: Optional[str] = Field(default="", description="Charging network operator")
    address: Optional[str] = Field(default="", description="Street address")
    city: Optional[str] = Field(default="", description="City name")
    latitude: float = Field(..., description="Latitude coordinate")
    longitude: float = Field(..., description="Longitude coordinate")
    charging_power_kw: float = Field(..., description="Maximum charging power output in kW")
    num_ports: int = Field(..., description="Number of charging points/plugs")
    connector_type: str = Field(..., description="Standard connector type supported")
    distance_km: float = Field(..., description="Driving road distance from user in km (via OSRM)")
    travel_time_minutes: float = Field(..., description="Estimated road driving travel time in minutes")
    reliability: float = Field(..., description="Predicted historical reliability score [0.0 - 1.0]")
    probability_available: float = Field(..., description="Predicted availability probability [0.0 - 1.0]")
    availability: Optional[float] = Field(default=None, description="Operational port availability probability [0.0 - 1.0]")
    estimated_charging_time_minutes: float = Field(..., description="Estimated charging duration in minutes")
    estimated_cost_inr: float = Field(..., description="Estimated charging cost in INR")
    compatible: bool = Field(..., description="Whether the charger matches the requested connector standard")
    usable_range_km: Optional[float] = Field(default=None, description="Estimated vehicle usable range in km")
    normalized_distance: Optional[float] = Field(default=None, description="Normalized distance penalty [0.0 - 1.0]")
    normalized_cost: Optional[float] = Field(default=None, description="Normalized cost penalty [0.0 - 1.0]")
    normalized_charging_time: Optional[float] = Field(default=None, description="Normalized charging time penalty [0.0 - 1.0]")
    operational_status: str = Field(default="AVAILABLE", description="Operational status (AVAILABLE, DEGRADED, OUT_OF_SERVICE, MAINTENANCE, UNKNOWN)")
    status_confidence: float = Field(default=1.0, description="Confidence in operational availability [0.0 - 1.0]")
    status_last_updated: Optional[str] = Field(default=None, description="ISO timestamp of latest operational telemetry update")
    eligible_for_planning: bool = Field(default=True, description="Whether station passed operational eligibility gate")
    rejection_reason: Optional[str] = Field(default=None, description="Reason if station was rejected by operational gate")
    trust_score: Optional[float] = Field(default=None, description="Composite station trust score [0.0 - 1.0]")
    final_score: float = Field(..., description="Multi-criteria optimization final score")


class RouteRequest(BaseModel):
    user_lat: float = Field(..., description="Current user latitude", examples=[12.2958])
    user_lon: float = Field(..., description="Current user longitude", examples=[76.6394])
    charger_id: Optional[int] = Field(default=None, description="Target charger database ID", examples=[7])
    charger_lat: Optional[float] = Field(default=None, description="Target charger latitude (optional if charger_id provided)")
    charger_lon: Optional[float] = Field(default=None, description="Target charger longitude (optional if charger_id provided)")


class RouteResponse(BaseModel):
    charger_id: Optional[int] = Field(default=None, description="Target charger database ID")
    charger_name: Optional[str] = Field(default="", description="Target charger name")
    distance_km: float = Field(..., description="Driving road distance in kilometers")
    travel_time_minutes: float = Field(..., description="Estimated travel duration in minutes")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON LineString route geometry")
    is_fallback: bool = Field(default=False, description="Whether fallback straight-line geometry was used")


class TripEnergyPredictionRequest(BaseModel):
    distance_km: float = Field(..., gt=0.0, description="Road distance of the planned trip in km", examples=[15.0])
    trip_duration_minutes: Optional[float] = Field(default=None, description="Estimated trip duration in minutes")
    average_speed_kmph: Optional[float] = Field(default=45.0, description="Estimated average speed in km/h", examples=[45.0])
    elevation_gain_m: Optional[float] = Field(default=0.0, description="Net elevation gain in meters", examples=[20.0])
    battery_capacity_kwh: Optional[float] = Field(default=30.0, description="Active battery capacity in kWh", examples=[30.0])
    starting_soc_percent: Optional[float] = Field(default=80.0, description="Current battery SoC %", examples=[80.0])
    traffic_density_score: Optional[float] = Field(default=0.25, description="Traffic congestion score 0.0 - 1.0", examples=[0.25])
    ambient_temperature_c: Optional[float] = Field(default=28.0, description="Ambient temperature in °C", examples=[28.0])
    vehicle_id: Optional[str] = Field(default="VIRTUAL_EV_001", description="Vehicle identifier")
    weather_condition: Optional[str] = Field(default="Clear", description="Macro weather condition")
    driving_style: Optional[str] = Field(default="Normal", description="Driving style: Eco, Normal, Aggressive")

    @model_validator(mode="before")
    @classmethod
    def handle_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "distance_km" not in values:
                if "trip_distance_km" in values:
                    values["distance_km"] = values["trip_distance_km"]
                elif "distance" in values:
                    values["distance_km"] = values["distance"]
            if "trip_duration_minutes" not in values and "trip_duration_seconds" in values:
                values["trip_duration_minutes"] = values["trip_duration_seconds"] / 60.0
            if "average_speed_kmph" not in values and "average_speed_kmh" in values:
                values["average_speed_kmph"] = values["average_speed_kmh"]
        return values


class NearTermEnergyPredictionRequest(BaseModel):
    telemetry_sequence: List[Dict[str, Any]] = Field(
        ...,
        description="Ordered list of recent telemetry observations (requires at least 20 observations)"
    )


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Service health indicator")
    database: str = Field(default="connected", description="Database connectivity status")
    total_chargers_loaded: int = Field(..., description="Total stations available in memory")


# ---------------------------------------------------------------------------
# Joint Route + Charging Planning Models (Change 20)
# ---------------------------------------------------------------------------
class ChargerCandidateInput(BaseModel):
    charger_id: str = Field(..., description="Unique charger identifier")
    name: str = Field(..., description="Station name")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude")
    charging_power_kw: float = Field(default=50.0, gt=0.0, description="Charging power output in kW")
    connector_type: str = Field(default="CCS2", description="Supported connector type")
    reliability: float = Field(default=0.90, ge=0.0, le=1.0, description="Historical reliability score [0.0 - 1.0]")
    probability_available: float = Field(default=0.80, ge=0.0, le=1.0, description="Availability probability [0.0 - 1.0]")
    charging_wait_minutes: Optional[float] = Field(default=None, ge=0.0, description="Explicit queue wait minutes if known")
    estimated_charging_time_minutes: Optional[float] = Field(default=None, ge=0.0, description="Explicit charging session duration if known")
    operational_status: Optional[str] = Field(default="AVAILABLE", description="Operational status: AVAILABLE, DEGRADED, OUT_OF_SERVICE, MAINTENANCE, UNKNOWN")
    status_confidence: Optional[float] = Field(default=1.0, description="Status confidence [0.0 - 1.0]")
    status_last_updated: Optional[str] = Field(default=None, description="ISO timestamp of status update")
    eligible_for_planning: Optional[bool] = Field(default=True, description="Whether eligible for planning")
    rejection_reason: Optional[str] = Field(default=None, description="Rejection reason if ineligible")


class TripPlanRequest(BaseModel):
    vehicle_id: str = Field(default="VIRTUAL_EV_001", description="Vehicle ID")
    battery_capacity_kwh: float = Field(..., gt=0.0, description="Total battery capacity in kWh", examples=[30.0])
    current_soc_percent: float = Field(..., ge=0.0, le=100.0, description="Current State of Charge percentage (0.0 to 100.0)", examples=[35.0])
    origin_latitude: float = Field(..., ge=-90.0, le=90.0, description="Starting point latitude", examples=[12.9716])
    origin_longitude: float = Field(..., ge=-180.0, le=180.0, description="Starting point longitude", examples=[77.5946])
    destination_latitude: float = Field(..., ge=-90.0, le=90.0, description="Destination latitude", examples=[12.2958])
    destination_longitude: float = Field(..., ge=-180.0, le=180.0, description="Destination longitude", examples=[76.6394])
    connector_type: str = Field(default="CCS2", description="EV connector standard (CCS2, Type 2, CHAdeMO, GB/T)")
    target_soc_percent: float = Field(default=80.0, ge=0.0, le=100.0, description="Target SoC % to reach after charging session", examples=[80.0])
    search_radius_km: float = Field(default=25.0, gt=0.0, description="Max search radius for candidate chargers in km")
    top_n: int = Field(default=5, ge=1, le=25, description="Number of candidate chargers to evaluate")
    reserve_battery_percent: float = Field(default=5.0, ge=0.0, le=50.0, description="Safety reserve battery buffer percentage")
    energy_consumption_kwh_per_km: float = Field(default=0.150, gt=0.0, description="Vehicle consumption rate in kWh/km")
    driving_style: Optional[str] = Field(default="Normal", description="Driving style (Eco, Normal, Aggressive)")
    traffic_profile: Optional[str] = Field(default=None, description="Optional traffic condition descriptor")
    weather_condition: Optional[str] = Field(default=None, description="Optional weather condition descriptor")
    candidate_chargers: Optional[List[ChargerCandidateInput]] = Field(
        default=None,
        description="Optional explicit candidate chargers. If omitted, candidates are loaded from database recommendations.",
    )
    multi_stop: Optional[bool] = Field(
        default=None,
        description="Whether to use Change 28 Automatic Multi-Stop Journey Planner. Defaults to False for legacy compatibility.",
    )


class SelectedChargerInfo(BaseModel):
    id: str = Field(..., description="Charger station ID")
    name: str = Field(..., description="Charger station name")
    latitude: float = Field(..., description="Latitude coordinate")
    longitude: float = Field(..., description="Longitude coordinate")
    connector: str = Field(..., description="Connector standard")
    reliability: float = Field(..., description="Historical reliability score")
    availability: float = Field(..., description="Port availability probability")
    charging_power_kw: float = Field(..., description="Power rating in kW")


class LegDetail(BaseModel):
    distance_km: float = Field(..., description="Road driving distance in km")
    energy_kwh: float = Field(..., description="Estimated energy consumption in kWh")
    travel_time_minutes: float = Field(..., description="Estimated driving travel time in minutes")
    geometry: Optional[Dict[str, Any]] = Field(default=None, description="GeoJSON LineString route geometry")
    is_fallback: bool = Field(default=False, description="Whether fallback straight-line geometry was used")


class RouteMetrics(BaseModel):
    total_distance_km: float = Field(..., description="Total journey distance in km")
    total_energy_kwh: float = Field(..., description="Total journey energy consumption in kWh")
    total_traffic_delay_minutes: float = Field(..., description="Total traffic congestion delay in minutes")
    charging_wait_minutes: float = Field(..., description="Estimated queue wait time at charger in minutes")
    charging_duration_minutes: float = Field(..., description="Estimated charging session duration in minutes")
    total_cost: float = Field(..., description="Normalized multi-criteria journey cost [0.0 - 1.0]")
    cost_breakdown: Optional[Dict[str, Any]] = Field(default=None, description="Change 18A cost components breakdown")


class EnergyAccounting(BaseModel):
    starting_energy_kwh: float = Field(..., description="Initial energy present in battery in kWh")
    energy_required_kwh: float = Field(..., description="Total energy needed across trip in kWh")
    arrival_energy_kwh: float = Field(..., description="Battery energy upon arrival at charger / destination in kWh")
    energy_added_kwh: float = Field(..., description="Energy added at charger in kWh")
    departure_energy_kwh: float = Field(..., description="Battery energy after charging on departure in kWh")
    reserve_energy_kwh: float = Field(..., description="Safety buffer energy in kWh")


class AlgorithmInfo(BaseModel):
    route_search: str = Field(default="Modified Energy-Aware A* (Change 18B)")
    planning_engine: str = Field(default="Joint Route + Charging Decision Engine (Change 19)")
    energy_prediction: str = Field(default="XGBoost Whole-Trip Predictor (Change 24)")


class AIEnergyPredictionInfo(BaseModel):
    available: bool = Field(default=False, description="Whether XGBoost AI prediction was available and used")
    model: str = Field(default="XGBoost", description="Model family identifier")
    predicted_energy_kwh: Optional[float] = Field(default=None, description="ML-predicted journey energy consumption in kWh")
    baseline_energy_kwh: float = Field(..., description="Deterministic physical energy requirement in kWh (0.150 kWh/km)")
    delta_kwh: Optional[float] = Field(default=None, description="Difference between AI prediction and physical baseline in kWh")
    delta_percent: Optional[float] = Field(default=None, description="Percentage difference relative to baseline")
    used_for_planning: bool = Field(default=True, description="Whether AI prediction influenced planning cost evaluation")
    used_for_battery_state: bool = Field(default=False, description="Whether AI prediction mutates physical battery telemetry (always False)")


class TripPlanResponse(BaseModel):
    success: bool = Field(..., description="Whether trip planning completed successfully")
    decision_type: str = Field(..., description="'DIRECT', 'CHARGE', or 'INFEASIBLE'")
    vehicle_id: str = Field(..., description="Vehicle identifier")
    origin: Dict[str, float] = Field(..., description="Origin coordinates {latitude, longitude}")
    destination: Dict[str, float] = Field(..., description="Destination coordinates {latitude, longitude}")

    # Section 16 Multi-Stop Fields
    total_distance_km: Optional[float] = Field(default=None, description="Total journey distance in km")
    total_energy_kwh: Optional[float] = Field(default=None, description="Total journey energy consumption in kWh")
    total_travel_time_minutes: Optional[float] = Field(default=None, description="Total travel time in minutes")
    charging_stop_count: int = Field(default=0, description="Total number of charging stops")
    charging_stops: List[Dict[str, Any]] = Field(default_factory=list, description="Ordered list of charging stops (Section 16)")
    total_charging_wait_minutes: float = Field(default=0.0, description="Total planned queue wait time in minutes")
    total_charging_duration_minutes: float = Field(default=0.0, description="Total planned charging duration in minutes")
    total_cost: float = Field(default=0.0, description="Total journey cost metric")
    feasible: bool = Field(default=True, description="Physical and operational feasibility of journey")
    algorithm_used: str = Field(default="Automatic Multi-Stop Energy-Aware EV Journey Planner (Change 28)", description="Planner algorithm name")

    # Route Legs: In Section 16 this is a List[Dict[str, Any]]. In legacy Change 19/20, this was Dict[str, Optional[LegDetail]].
    legs: Any = Field(..., description="Leg details (Section 16 list or legacy dict)")

    # Legacy fields
    selected_charger: Optional[SelectedChargerInfo] = Field(default=None, description="Selected charging stop details (null for DIRECT)")
    route: Optional[RouteMetrics] = Field(default=None, description="Overall journey route metrics and cost")
    energy: Optional[EnergyAccounting] = Field(default=None, description="Comprehensive battery energy ledger")
    algorithm: Optional[AlgorithmInfo] = Field(default_factory=AlgorithmInfo, description="Algorithms used for decision")
    ai_energy_prediction: Optional[AIEnergyPredictionInfo] = Field(default=None, description="AI-informed energy prediction diagnostics (Change 24)")
    explanation: str = Field(..., description="Human-readable rationale for the selected plan")
    candidate_count_evaluated: int = Field(default=0, description="Total candidate chargers evaluated")


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/", summary="API Root")
def root() -> Dict[str, str]:
    return {
        "message": "Welcome to the EV Charging Intelligence API",
        "docs": "/docs",
        "health": "/health",
        "recommend": "/recommend",
        "route": "/route",
        "energy_status": "/energy/status",
        "energy_predict_trip": "/energy/predict/trip",
        "energy_predict_near_term": "/energy/predict/near-term",
        "trip_plan": "/trip/plan",
        "charger_feedback": "/chargers/{charger_id}/feedback",
        "charger_operational_status": "/chargers/{charger_id}/operational-status",
    }


@app.get("/energy/status", summary="AI Energy Prediction Models Status")
def get_energy_status() -> Dict[str, Any]:
    """
    Returns the availability, architecture, and readiness of both the
    XGBoost (whole-trip) and LSTM (near-term 30s) AI energy predictors.
    """
    return energy_prediction_service.get_status()


@app.post("/energy/predict/trip", summary="Predict Whole-Trip Energy (XGBoost)")
def predict_trip_energy(request: TripEnergyPredictionRequest) -> Dict[str, Any]:
    """
    Predict total energy consumption across an entire planned route using the trained XGBoost model.
    Presents prediction alongside the production 0.150 kWh/km baseline.
    """
    return energy_prediction_service.predict_trip_energy(request.model_dump())


@app.post("/energy/predict/near-term", summary="Predict Near-Term Energy (LSTM)")
def predict_near_term_energy(request: NearTermEnergyPredictionRequest) -> Dict[str, Any]:
    """
    Predict near-term energy consumption over the upcoming 30-second horizon using the trained Keras LSTM model.
    Strictly requires >= 20 sequential telemetry observations. Returns 'insufficient_history' if < 20.
    """
    return energy_prediction_service.predict_near_term_energy(request.telemetry_sequence)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health and Readiness Check",
)
def health_check() -> HealthResponse:
    """
    Check the health of the API service and verify database/model connectivity.
    """
    try:
        df = load_charger_data()
        return HealthResponse(
            status="ok",
            database="connected",
            total_chargers_loaded=len(df),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service unavailable: {exc}",
        )


@app.post(
    "/route",
    response_model=RouteResponse,
    summary="Get Detailed OSRM Route to Charger",
)
def get_route(request: RouteRequest) -> RouteResponse:
    """
    Fetch on-demand full GeoJSON road driving route geometry, distance, and duration
    from user location to a specific target charger.
    """
    df = load_charger_data()
    c_lat, c_lon = request.charger_lat, request.charger_lon
    c_name = ""

    if request.charger_id is not None:
        matching = df[df["id"] == request.charger_id]
        if matching.empty:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Charger with id={request.charger_id} not found in database.",
            )
        row = matching.iloc[0]
        c_lat = float(row["latitude"])
        c_lon = float(row["longitude"])
        c_name = str(row["name"])
    elif c_lat is None or c_lon is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either charger_id or both charger_lat and charger_lon must be provided.",
        )

    route_info = get_road_route(
        user_lat=request.user_lat,
        user_lon=request.user_lon,
        charger_lat=c_lat,
        charger_lon=c_lon,
        include_geometry=True,
    )

    return RouteResponse(
        charger_id=request.charger_id,
        charger_name=c_name,
        distance_km=route_info["distance_km"],
        travel_time_minutes=route_info["travel_time_minutes"],
        geometry=route_info["geometry"] or {
            "type": "LineString",
            "coordinates": [[request.user_lon, request.user_lat], [c_lon, c_lat]],
        },
        is_fallback=route_info.get("is_fallback", False),
    )


@app.post(
    "/recommend",
    response_model=List[ChargerRecommendation],
    summary="Get Multi-Criteria EV Charger Recommendations",
)
def get_recommendations(request: RecommendationRequest) -> List[Dict[str, Any]]:
    """
    Generate ranked EV charging station recommendations optimized across
    predicted reliability, availability probability, distance, duration, and tariff.
    """
    try:
        recommendations = score_and_rank_chargers(
            user_lat=request.user_lat,
            user_lon=request.user_lon,
            connector_type=request.connector_type,
            battery_capacity_kwh=request.battery_capacity_kwh,
            current_soc_percent=request.current_soc_percent,
            target_soc_percent=request.target_soc_percent,
            arrival_datetime=request.arrival_datetime,
            max_search_radius_km=request.max_search_radius_km,
            top_n=request.top_n,
            energy_consumption_kwh_per_km=request.energy_consumption_kwh_per_km,
            reserve_battery_percent=request.reserve_battery_percent,
            debug_mode=False,
        )

        if not recommendations:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No charging stations found within {request.max_search_radius_km} km radius.",
            )

        return recommendations

    except VehicleStrandedException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "VEHICLE_STRANDED",
                "message": str(exc),
                "usable_range_km": round(exc.usable_range_km, 1),
                "current_soc_percent": exc.current_soc_percent,
                "suggestion": "Your remaining battery charge is insufficient to safely reach any station in range. Consider emergency charging options or reducing target distance.",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Recommendation computation error: {exc}",
        )


@app.post(
    "/trip/plan",
    response_model=TripPlanResponse,
    summary="Joint EV Route & Charging Journey Planner",
)
def plan_trip(request: TripPlanRequest) -> TripPlanResponse:
    """
    Jointly evaluates direct route feasibility against en-route charging alternatives
    using the Change 19 Decision Engine, Change 18B Modified A* route search,
    and Change 18A multi-criteria cost optimization.
    """
    try:
        # 1. Initialize Vehicle State
        vehicle_state = VehicleState(
            battery_capacity_kwh=request.battery_capacity_kwh,
            current_soc_percent=request.current_soc_percent,
            connector_type=request.connector_type,
            reserve_battery_percent=request.reserve_battery_percent,
            target_soc_percent=request.target_soc_percent,
            energy_consumption_kwh_per_km=request.energy_consumption_kwh_per_km,
        )

        # 2. Acquire Candidate Chargers
        candidates: List[ChargerCandidate] = []
        if request.candidate_chargers is not None and len(request.candidate_chargers) > 0:
            for c in request.candidate_chargers:
                candidates.append(
                    ChargerCandidate(
                        charger_id=c.charger_id,
                        name=c.name,
                        latitude=c.latitude,
                        longitude=c.longitude,
                        node_id=f"charger_{c.charger_id}",
                        charging_power_kw=c.charging_power_kw,
                        connector_type=c.connector_type,
                        reliability=c.reliability,
                        probability_available=c.probability_available,
                        charging_wait_minutes=c.charging_wait_minutes,
                        estimated_charging_time_minutes=c.estimated_charging_time_minutes,
                        operational_status=c.operational_status or "AVAILABLE",
                        status_confidence=c.status_confidence if c.status_confidence is not None else 1.0,
                        status_last_updated=c.status_last_updated,
                        eligible_for_planning=c.eligible_for_planning if c.eligible_for_planning is not None else True,
                        rejection_reason=c.rejection_reason,
                    )
                )
        elif not request.multi_stop:
            # Attempt to query database recommendations for single-point search
            try:
                recs = score_and_rank_chargers(
                    user_lat=request.origin_latitude,
                    user_lon=request.origin_longitude,
                    connector_type=request.connector_type,
                    battery_capacity_kwh=request.battery_capacity_kwh,
                    current_soc_percent=request.current_soc_percent,
                    target_soc_percent=request.target_soc_percent,
                    max_search_radius_km=request.search_radius_km,
                    top_n=request.top_n,
                    energy_consumption_kwh_per_km=request.energy_consumption_kwh_per_km,
                    reserve_battery_percent=request.reserve_battery_percent,
                    debug_mode=False,
                )
                for r in recs:
                    candidates.append(
                        ChargerCandidate(
                            charger_id=str(r["charger_id"]),
                            name=r["name"],
                            latitude=float(r["latitude"]),
                            longitude=float(r["longitude"]),
                            node_id=f"charger_{r['charger_id']}",
                            charging_power_kw=float(r["charging_power_kw"]),
                            connector_type=str(r.get("connector_type", "CCS2")),
                            reliability=float(r.get("reliability", 0.9)),
                            probability_available=float(r.get("probability_available", 0.8)),
                            estimated_charging_time_minutes=float(r.get("estimated_charging_time_minutes", 30.0)),
                            operational_status=str(r.get("operational_status", "AVAILABLE")),
                            status_confidence=float(r.get("status_confidence", 1.0)),
                            status_last_updated=r.get("status_last_updated"),
                            eligible_for_planning=bool(r.get("eligible_for_planning", True)),
                            rejection_reason=r.get("rejection_reason"),
                        )
                    )
            except Exception:
                candidates = []

        # Check if Multi-Stop Journey Planner is requested (Change 28)
        if request.multi_stop is True:
            def trip_energy_predictor(trip_dict: Dict[str, Any]) -> Dict[str, Any]:
                trip_dict["driving_style"] = request.driving_style or "Normal"
                trip_dict["weather_condition"] = request.weather_condition or "Clear"
                return energy_prediction_service.predict_trip_energy(trip_dict)

            planner = MultiStopTripPlanner(
                energy_rate_kwh_per_km=request.energy_consumption_kwh_per_km,
                operational_policy=OperationalPolicy(),
            )
            cands_for_planner = candidates if len(candidates) > 0 else None
            multi_plan = planner.plan_journey(
                origin_lat=request.origin_latitude,
                origin_lon=request.origin_longitude,
                dest_lat=request.destination_latitude,
                dest_lon=request.destination_longitude,
                vehicle_state=vehicle_state,
                candidate_chargers=cands_for_planner,
                target_soc_percent=request.target_soc_percent,
                ai_energy_predictor=trip_energy_predictor,
            )

            stops_dicts = [s.to_dict() for s in multi_plan.charging_stops]
            legs_list = [l.to_dict() for l in multi_plan.legs]

            first_charger = None
            if len(multi_plan.charging_stops) > 0:
                s0 = multi_plan.charging_stops[0]
                first_charger = SelectedChargerInfo(
                    id=s0.charger_id,
                    name=s0.charger_name,
                    latitude=s0.latitude,
                    longitude=s0.longitude,
                    connector=request.connector_type,
                    reliability=s0.reliability,
                    availability=s0.availability,
                    charging_power_kw=s0.charging_power_kw,
                )

            route_metrics = RouteMetrics(
                total_distance_km=multi_plan.total_distance_km,
                total_energy_kwh=multi_plan.total_energy_kwh,
                total_traffic_delay_minutes=0.0,
                charging_wait_minutes=multi_plan.total_charging_wait_minutes,
                charging_duration_minutes=multi_plan.total_charging_duration_minutes,
                total_cost=multi_plan.total_cost,
                cost_breakdown=None,
            )

            energy_accounting = EnergyAccounting(
                starting_energy_kwh=round(multi_plan.starting_energy_kwh, 3),
                energy_required_kwh=round(multi_plan.total_energy_kwh, 3),
                arrival_energy_kwh=round(multi_plan.final_arrival_energy_kwh, 3),
                energy_added_kwh=round(sum(s.energy_added_kwh for s in multi_plan.charging_stops), 3),
                departure_energy_kwh=round(
                    vehicle_state.battery_capacity_kwh * (request.target_soc_percent / 100.0)
                    if multi_plan.charging_stops else vehicle_state.current_energy_kwh,
                    3,
                ),
                reserve_energy_kwh=round(multi_plan.reserve_energy_kwh, 3),
            )

            ai_info = None
            if multi_plan.ai_energy_prediction:
                ai_data = multi_plan.ai_energy_prediction
                ai_info = AIEnergyPredictionInfo(
                    available=bool(ai_data.get("available", False)),
                    model=str(ai_data.get("model", "XGBoost")),
                    predicted_energy_kwh=ai_data.get("predicted_energy_kwh"),
                    baseline_energy_kwh=float(ai_data.get("baseline_energy_kwh", multi_plan.total_energy_kwh)),
                    delta_kwh=ai_data.get("delta_kwh"),
                    delta_percent=ai_data.get("delta_percent"),
                    used_for_planning=bool(ai_data.get("used_for_planning", True)),
                    used_for_battery_state=False,
                )

            return TripPlanResponse(
                success=multi_plan.success,
                decision_type=multi_plan.decision_type,
                vehicle_id=request.vehicle_id,
                origin={"latitude": request.origin_latitude, "longitude": request.origin_longitude},
                destination={"latitude": request.destination_latitude, "longitude": request.destination_longitude},
                total_distance_km=multi_plan.total_distance_km,
                total_energy_kwh=multi_plan.total_energy_kwh,
                total_travel_time_minutes=multi_plan.total_travel_time_minutes,
                charging_stop_count=multi_plan.charging_stop_count,
                charging_stops=stops_dicts,
                total_charging_wait_minutes=multi_plan.total_charging_wait_minutes,
                total_charging_duration_minutes=multi_plan.total_charging_duration_minutes,
                total_cost=multi_plan.total_cost,
                feasible=multi_plan.feasible,
                algorithm_used=multi_plan.algorithm_used,
                legs=legs_list,
                selected_charger=first_charger,
                route=route_metrics,
                energy=energy_accounting,
                algorithm=AlgorithmInfo(
                    route_search="Modified Energy-Aware A* (Change 18B)",
                    planning_engine="Automatic Multi-Stop Energy-Aware EV Journey Planner (Change 28)",
                    energy_prediction="XGBoost Whole-Trip Predictor (Change 24)",
                ),
                ai_energy_prediction=ai_info,
                explanation=multi_plan.explanation,
                candidate_count_evaluated=multi_plan.candidate_count_evaluated,
            )

        # 3. Direct Route Calculation (Legacy Single-Stop Path)
        direct_route_info = get_road_route(
            user_lat=request.origin_latitude,
            user_lon=request.origin_longitude,
            charger_lat=request.destination_latitude,
            charger_lon=request.destination_longitude,
            include_geometry=True,
        )
        direct_leg = LegDetail(
            distance_km=direct_route_info["distance_km"],
            energy_kwh=round(direct_route_info["distance_km"] * request.energy_consumption_kwh_per_km, 3),
            travel_time_minutes=direct_route_info["travel_time_minutes"],
            geometry=direct_route_info.get("geometry"),
            is_fallback=direct_route_info.get("is_fallback", False),
        )

        # 4. Build RoutingGraph and Road Geometry Cache
        graph = RoutingGraph()
        graph.add_node("origin", x_km=0.0, y_km=0.0)
        graph.add_node("destination", x_km=direct_route_info["distance_km"], y_km=0.0)
        graph.add_edge(
            "origin",
            "destination",
            distance_km=direct_route_info["distance_km"],
            predicted_energy_kwh=round(direct_route_info["distance_km"] * request.energy_consumption_kwh_per_km, 4),
        )

        leg_routes_cache: Dict[str, Dict[str, Any]] = {}

        for c in candidates:
            if c.latitude is not None and c.longitude is not None:
                l1_info = get_road_route(
                    user_lat=request.origin_latitude,
                    user_lon=request.origin_longitude,
                    charger_lat=c.latitude,
                    charger_lon=c.longitude,
                    include_geometry=True,
                )
                l2_info = get_road_route(
                    user_lat=c.latitude,
                    user_lon=c.longitude,
                    charger_lat=request.destination_latitude,
                    charger_lon=request.destination_longitude,
                    include_geometry=True,
                )
                leg_routes_cache[c.charger_id] = {
                    "leg_1": l1_info,
                    "leg_2": l2_info,
                }
                c_node = c.node_id or f"charger_{c.charger_id}"
                graph.add_node(c_node)
                graph.add_edge(
                    "origin",
                    c_node,
                    distance_km=l1_info["distance_km"],
                    predicted_energy_kwh=round(l1_info["distance_km"] * request.energy_consumption_kwh_per_km, 4),
                )
                graph.add_edge(
                    c_node,
                    "destination",
                    distance_km=l2_info["distance_km"],
                    predicted_energy_kwh=round(l2_info["distance_km"] * request.energy_consumption_kwh_per_km, 4),
                )

        # 5. Evaluate Joint Route & Charging using Change 19 Engine with Change 24 AI Energy Prediction
        def trip_energy_predictor(trip_dict: Dict[str, Any]) -> Dict[str, Any]:
            trip_dict["driving_style"] = request.driving_style or "Normal"
            trip_dict["weather_condition"] = request.weather_condition or "Clear"
            return energy_prediction_service.predict_trip_energy(trip_dict)

        plan_res = evaluate_joint_route_and_charging(
            origin="origin",
            destination="destination",
            vehicle_state=vehicle_state,
            charger_candidates=candidates,
            graph=graph,
            ai_energy_predictor=trip_energy_predictor,
        )

        # 6. Assemble Response
        selected_charger_info = None
        legs_map: Dict[str, Optional[LegDetail]] = {
            "direct": direct_leg,
            "origin_to_charger": None,
            "charger_to_destination": None,
        }

        matched = None
        if plan_res.decision_type == "CHARGE" and plan_res.selected_charger_id:
            matched = next((c for c in candidates if c.charger_id == plan_res.selected_charger_id), None)
            if matched and matched.latitude is not None and matched.longitude is not None:
                selected_charger_info = SelectedChargerInfo(
                    id=matched.charger_id,
                    name=matched.name,
                    latitude=matched.latitude,
                    longitude=matched.longitude,
                    connector=matched.connector_type,
                    reliability=matched.reliability,
                    availability=matched.probability_available,
                    charging_power_kw=matched.charging_power_kw,
                )
                cached_legs = leg_routes_cache.get(matched.charger_id)
                if cached_legs:
                    l1 = cached_legs["leg_1"]
                    l2 = cached_legs["leg_2"]
                    legs_map["origin_to_charger"] = LegDetail(
                        distance_km=l1["distance_km"],
                        energy_kwh=round(l1["distance_km"] * request.energy_consumption_kwh_per_km, 3),
                        travel_time_minutes=l1["travel_time_minutes"],
                        geometry=l1.get("geometry"),
                        is_fallback=l1.get("is_fallback", False),
                    )
                    legs_map["charger_to_destination"] = LegDetail(
                        distance_km=l2["distance_km"],
                        energy_kwh=round(l2["distance_km"] * request.energy_consumption_kwh_per_km, 3),
                        travel_time_minutes=l2["travel_time_minutes"],
                        geometry=l2.get("geometry"),
                        is_fallback=l2.get("is_fallback", False),
                    )

        cost_components = (
            plan_res.cost_evaluation.get("components") if plan_res.cost_evaluation else None
        )
        total_traffic = 0.0
        if plan_res.cost_evaluation and "raw_inputs" in plan_res.cost_evaluation:
            total_traffic = float(plan_res.cost_evaluation["raw_inputs"].get("traffic_delay_minutes", 0.0))

        route_metrics = RouteMetrics(
            total_distance_km=plan_res.total_distance_km,
            total_energy_kwh=plan_res.total_energy_kwh,
            total_traffic_delay_minutes=round(total_traffic, 1),
            charging_wait_minutes=plan_res.estimated_charging_wait_minutes,
            charging_duration_minutes=plan_res.estimated_charging_duration_minutes,
            total_cost=plan_res.total_route_cost,
            cost_breakdown=cost_components,
        )

        energy_accounting = EnergyAccounting(
            starting_energy_kwh=round(vehicle_state.current_energy_kwh, 3),
            energy_required_kwh=round(plan_res.total_energy_kwh, 3),
            arrival_energy_kwh=round(plan_res.arrival_energy_before_charging_kwh, 3),
            energy_added_kwh=round(plan_res.energy_added_at_charger_kwh, 3),
            departure_energy_kwh=round(plan_res.post_charge_energy_kwh, 3),
            reserve_energy_kwh=round(vehicle_state.reserve_energy_kwh, 3),
        )

        # Map AI prediction diagnostics
        ai_info: Optional[AIEnergyPredictionInfo] = None
        if plan_res.ai_energy_prediction:
            ai_data = plan_res.ai_energy_prediction
            ai_info = AIEnergyPredictionInfo(
                available=bool(ai_data.get("available", False)),
                model=str(ai_data.get("model", "XGBoost")),
                predicted_energy_kwh=ai_data.get("predicted_energy_kwh"),
                baseline_energy_kwh=float(ai_data.get("baseline_energy_kwh", plan_res.total_energy_kwh)),
                delta_kwh=ai_data.get("delta_kwh"),
                delta_percent=ai_data.get("delta_percent"),
                used_for_planning=bool(ai_data.get("used_for_planning", True)),
                used_for_battery_state=False,
            )

        # Elaborate human-readable explanation with AI vs. Physics traceability
        detailed_explanation = plan_res.reason
        if ai_info and ai_info.available and ai_info.predicted_energy_kwh is not None:
            detailed_explanation += (
                f" AI energy prediction estimated {ai_info.predicted_energy_kwh:.2f} kWh versus the physical baseline of "
                f"{ai_info.baseline_energy_kwh:.2f} kWh. The baseline remains authoritative for battery feasibility; "
                f"the AI estimate contributes to route ranking."
            )

        # Assemble Section 16 charging stops list for legacy path
        legacy_stops: List[Dict[str, Any]] = []
        if plan_res.decision_type == "CHARGE" and selected_charger_info and matched:
            l1_dist = legs_map["origin_to_charger"].distance_km if legs_map.get("origin_to_charger") else 0.0
            l1_egy = legs_map["origin_to_charger"].energy_kwh if legs_map.get("origin_to_charger") else 0.0
            legacy_stops.append({
                "stop_index": 1,
                "charger_id": selected_charger_info.id,
                "charger_name": selected_charger_info.name,
                "latitude": selected_charger_info.latitude,
                "longitude": selected_charger_info.longitude,
                "operational_status": getattr(matched, "operational_status", "AVAILABLE"),
                "status_confidence": getattr(matched, "status_confidence", 1.0),
                "reliability": selected_charger_info.reliability,
                "availability": selected_charger_info.availability,
                "charging_power_kw": selected_charger_info.charging_power_kw,
                "arrival_energy_kwh": round(plan_res.arrival_energy_before_charging_kwh, 3),
                "energy_added_kwh": round(plan_res.energy_added_at_charger_kwh, 3),
                "departure_energy_kwh": round(plan_res.post_charge_energy_kwh, 3),
                "expected_wait_minutes": round(plan_res.estimated_charging_wait_minutes, 1),
                "charging_duration_minutes": round(plan_res.estimated_charging_duration_minutes, 1),
                "diversion_distance_km": round(max(0.0, plan_res.total_distance_km - direct_leg.distance_km), 2),
                "leg_distance_km": round(l1_dist, 1),
                "leg_energy_kwh": round(l1_egy, 3),
            })

        driving_time = direct_leg.travel_time_minutes
        if legs_map.get("origin_to_charger") and legs_map.get("charger_to_destination"):
            driving_time = legs_map["origin_to_charger"].travel_time_minutes + legs_map["charger_to_destination"].travel_time_minutes
        total_trip_time = round(driving_time + plan_res.estimated_charging_wait_minutes + plan_res.estimated_charging_duration_minutes, 1)

        return TripPlanResponse(
            success=(plan_res.decision_type != "INFEASIBLE"),
            decision_type=plan_res.decision_type,
            vehicle_id=request.vehicle_id,
            origin={"latitude": request.origin_latitude, "longitude": request.origin_longitude},
            destination={"latitude": request.destination_latitude, "longitude": request.destination_longitude},
            total_distance_km=plan_res.total_distance_km,
            total_energy_kwh=plan_res.total_energy_kwh,
            total_travel_time_minutes=total_trip_time,
            charging_stop_count=len(legacy_stops),
            charging_stops=legacy_stops,
            total_charging_wait_minutes=plan_res.estimated_charging_wait_minutes,
            total_charging_duration_minutes=plan_res.estimated_charging_duration_minutes,
            total_cost=plan_res.total_route_cost,
            feasible=(plan_res.decision_type != "INFEASIBLE"),
            algorithm_used="Joint Route + Charging Decision Engine (Change 19)",
            selected_charger=selected_charger_info,
            route=route_metrics,
            legs=legs_map,
            energy=energy_accounting,
            algorithm=AlgorithmInfo(
                route_search="Modified Energy-Aware A* (Change 18B)",
                planning_engine="Joint Route + Charging Decision Engine (Change 19)",
                energy_prediction="XGBoost Whole-Trip Predictor (Change 24)",
            ),
            ai_energy_prediction=ai_info,
            explanation=detailed_explanation,
            candidate_count_evaluated=len(candidates),
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal journey planning failure: {exc}",
        )


# ---------------------------------------------------------------------------
# Charger Operational Safety & Feedback Endpoints (Change 27)
# ---------------------------------------------------------------------------
class FeedbackSubmissionRequest(BaseModel):
    result: str = Field(
        ...,
        description="Charging attempt outcome: SUCCESSFUL_CHARGE, STATION_UNAVAILABLE, CHARGER_FAULT, CONNECTOR_PROBLEM, OTHER",
        examples=["SUCCESSFUL_CHARGE"],
    )
    vehicle_id: Optional[str] = Field(default=None, description="Optional vehicle identifier")
    session_id: Optional[str] = Field(default=None, description="Optional charging session ID")
    notes: Optional[str] = Field(default=None, description="Optional user comments or observations")


@app.post(
    "/chargers/{charger_id}/feedback",
    summary="Submit Charging Attempt User Feedback",
)
def submit_charger_feedback(charger_id: str, request: FeedbackSubmissionRequest) -> Dict[str, Any]:
    """
    Submit user feedback for an attempted charging session.
    Recent repeated negative reports decrease station confidence and may trigger temporary safety exclusion.
    """
    res_str = request.result.strip().upper()
    try:
        fb_result = FeedbackResult(res_str)
    except ValueError:
        valid_opts = [e.value for e in FeedbackResult]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid feedback result '{request.result}'. Must be one of: {valid_opts}",
        )

    event = ChargingFeedbackEvent(
        charger_id=charger_id,
        result=fb_result,
        vehicle_id=request.vehicle_id,
        session_id=request.session_id,
        notes=request.notes,
    )
    global_feedback_manager.record_feedback(event)
    summary = global_feedback_manager.get_feedback_summary(charger_id)

    # Re-evaluate operational status given new feedback
    elig = check_operational_eligibility(
        {"charger_id": charger_id},
        feedback_mgr=global_feedback_manager,
    )

    return {
        "status": "success",
        "message": f"Feedback successfully recorded for charger {charger_id}.",
        "event": event.to_dict(),
        "operational_status": elig.operational_status.value,
        "status_confidence": elig.status_confidence,
        "eligible_for_planning": elig.eligible,
        "rejection_reason": elig.rejection_reason,
        "feedback_summary": summary,
        "data_honesty_note": elig.data_honesty_note,
    }


@app.get(
    "/chargers/{charger_id}/operational-status",
    summary="Get Charger Operational Status & Trust Metrics",
)
def get_charger_operational_status(charger_id: str) -> Dict[str, Any]:
    """
    Query normalized operational status, confidence, trust score, and feedback summary.
    Discloses that data represents operational confidence based on latest available data.
    """
    row_data: Dict[str, Any] = {"charger_id": charger_id}
    try:
        df = load_charger_data()
        matching = df[df["id"].astype(str) == str(charger_id)]
        if not matching.empty:
            row_data = dict(matching.iloc[0])
            row_data["charger_id"] = charger_id
    except Exception:
        pass

    elig = check_operational_eligibility(
        row_data,
        feedback_mgr=global_feedback_manager,
    )
    summary = global_feedback_manager.get_feedback_summary(charger_id)

    return {
        "charger_id": charger_id,
        "operational_status": elig.operational_status.value,
        "status_confidence": elig.status_confidence,
        "status_last_updated": elig.status_last_updated_iso,
        "status_age_hours": elig.status_age_hours,
        "trust_score": elig.trust_score,
        "eligible_for_planning": elig.eligible,
        "rejection_reason": elig.rejection_reason,
        "feedback_summary": summary,
        "data_honesty_note": elig.data_honesty_note,
    }


# ---------------------------------------------------------------------------
# Local Dev Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

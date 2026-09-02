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
    estimated_charging_time_minutes: float = Field(..., description="Estimated charging duration in minutes")
    estimated_cost_inr: float = Field(..., description="Estimated charging cost in INR")
    compatible: bool = Field(..., description="Whether the charger matches the requested connector standard")
    usable_range_km: Optional[float] = Field(default=None, description="Estimated vehicle usable range in km")
    normalized_distance: Optional[float] = Field(default=None, description="Normalized distance penalty [0.0 - 1.0]")
    normalized_cost: Optional[float] = Field(default=None, description="Normalized cost penalty [0.0 - 1.0]")
    normalized_charging_time: Optional[float] = Field(default=None, description="Normalized charging time penalty [0.0 - 1.0]")
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


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Service health indicator")
    database: str = Field(default="connected", description="Database connectivity status")
    total_chargers_loaded: int = Field(..., description="Total stations available in memory")


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
    }


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


# ---------------------------------------------------------------------------
# Local Dev Runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

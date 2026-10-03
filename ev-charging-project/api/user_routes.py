"""
User, Vehicle, Trip History & Review Routes Module
===================================================
RESTful endpoints for:
1. Authentication: /auth/signup, /auth/login, /auth/me, /auth/logout
2. Vehicles: GET/POST /vehicles, POST /vehicles/{id}/select, DELETE /vehicles/{id}
3. Trip History: GET/POST /trips/history
4. Reviews: GET/POST /chargers/{charger_id}/reviews
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth_utils import (
    create_access_token,
    get_current_user,
    get_current_user_optional,
    hash_password,
    verify_password,
)
from database.connection import get_db
from database.models import Charger, Review, TripHistory, User, UserVehicle

router = APIRouter(tags=["User & Account"])


# ---------------------------------------------------------------------------
# Pydantic Request / Response Schemas
# ---------------------------------------------------------------------------

class InitialVehicleData(BaseModel):
    vehicle_name: str = "Tata Nexon EV Max"
    battery_capacity_kwh: float = 30.0
    connector_type: str = "CCS2"
    energy_consumption_kwh_per_km: float = 0.15


class SignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=100)
    initial_vehicle: Optional[InitialVehicleData] = None


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1)


class UserProfileResponse(BaseModel):
    id: int
    name: str
    email: str
    created_at: str


class VehicleResponse(BaseModel):
    id: int
    user_id: int
    vehicle_name: str
    battery_capacity_kwh: float
    connector_type: str
    energy_consumption_kwh_per_km: float
    is_selected: bool
    created_at: str


class AuthResponse(BaseModel):
    token: str
    user: UserProfileResponse
    selected_vehicle: Optional[VehicleResponse] = None
    vehicles: List[VehicleResponse] = []


class VehicleCreateRequest(BaseModel):
    vehicle_name: str = Field(..., min_length=1, max_length=100)
    battery_capacity_kwh: float = Field(default=30.0, gt=0, le=200)
    connector_type: str = Field(default="CCS2", max_length=50)
    energy_consumption_kwh_per_km: float = Field(default=0.15, gt=0, le=1.0)
    is_selected: bool = False


class TripHistoryCreateRequest(BaseModel):
    origin_name: str = "Current Location"
    origin_lat: float
    origin_lon: float
    dest_name: str
    dest_lat: float
    dest_lon: float
    total_distance_km: float = Field(..., ge=0)
    total_energy_kwh: float = Field(..., ge=0)
    total_travel_time_minutes: Optional[float] = None
    charging_stop_count: int = 0
    charging_stops_summary: Optional[str] = None
    vehicle_name: Optional[str] = None
    status: str = "COMPLETED"


class TripHistoryItemResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    vehicle_name: Optional[str] = None
    origin_name: str
    origin_lat: float
    origin_lon: float
    dest_name: str
    dest_lat: float
    dest_lon: float
    total_distance_km: float
    total_energy_kwh: float
    total_travel_time_minutes: Optional[float] = None
    charging_stop_count: int
    charging_stops_summary: Optional[str] = None
    status: str
    created_at: str


class ReviewCreateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review_text: Optional[str] = Field(default="", max_length=2000)


class ReviewItemResponse(BaseModel):
    id: int
    charger_id: int
    user_id: Optional[int] = None
    user_name: str
    rating: int
    review_text: Optional[str] = None
    sentiment_score: Optional[float] = None
    created_at: str


class ChargerReviewsSummaryResponse(BaseModel):
    charger_id: int
    avg_rating: float
    review_count: int
    rating_distribution: Dict[str, int]
    recent_reviews: List[ReviewItemResponse]


# ---------------------------------------------------------------------------
# Helper Serialization Functions
# ---------------------------------------------------------------------------

def _serialize_user(user: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        created_at=user.created_at.isoformat() if user.created_at else datetime.now(timezone.utc).isoformat(),
    )


def _serialize_vehicle(vehicle: UserVehicle) -> VehicleResponse:
    return VehicleResponse(
        id=vehicle.id,
        user_id=vehicle.user_id,
        vehicle_name=vehicle.vehicle_name,
        battery_capacity_kwh=float(vehicle.battery_capacity_kwh),
        connector_type=vehicle.connector_type,
        energy_consumption_kwh_per_km=float(vehicle.energy_consumption_kwh_per_km),
        is_selected=bool(vehicle.is_selected),
        created_at=vehicle.created_at.isoformat() if vehicle.created_at else datetime.now(timezone.utc).isoformat(),
    )


def _serialize_trip(trip: TripHistory) -> TripHistoryItemResponse:
    return TripHistoryItemResponse(
        id=trip.id,
        user_id=trip.user_id,
        vehicle_name=trip.vehicle_name,
        origin_name=trip.origin_name,
        origin_lat=float(trip.origin_lat),
        origin_lon=float(trip.origin_lon),
        dest_name=trip.dest_name,
        dest_lat=float(trip.dest_lat),
        dest_lon=float(trip.dest_lon),
        total_distance_km=round(float(trip.total_distance_km), 2),
        total_energy_kwh=round(float(trip.total_energy_kwh), 2),
        total_travel_time_minutes=round(float(trip.total_travel_time_minutes), 1) if trip.total_travel_time_minutes is not None else None,
        charging_stop_count=int(trip.charging_stop_count or 0),
        charging_stops_summary=trip.charging_stops_summary,
        status=trip.status,
        created_at=trip.created_at.isoformat() if trip.created_at else datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 1. Authentication Endpoints
# ---------------------------------------------------------------------------

@router.post("/auth/signup", response_model=AuthResponse, summary="Register New User")
def signup(request: SignupRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Creates a new user account with initial default vehicle profile."""
    email_clean = request.email.lower().strip()
    existing = db.query(User).filter(func.lower(User.email) == email_clean).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    pwd_hash = hash_password(request.password)
    new_user = User(
        name=request.name.strip(),
        email=email_clean,
        password_hash=pwd_hash,
    )
    db.add(new_user)
    db.flush()

    # Initial vehicle creation
    init_v = request.initial_vehicle or InitialVehicleData()
    first_vehicle = UserVehicle(
        user_id=new_user.id,
        vehicle_name=init_v.vehicle_name,
        battery_capacity_kwh=init_v.battery_capacity_kwh,
        connector_type=init_v.connector_type,
        energy_consumption_kwh_per_km=init_v.energy_consumption_kwh_per_km,
        is_selected=True,
    )
    db.add(first_vehicle)
    db.commit()
    db.refresh(new_user)
    db.refresh(first_vehicle)

    token = create_access_token(user_id=new_user.id, email=new_user.email)
    return AuthResponse(
        token=token,
        user=_serialize_user(new_user),
        selected_vehicle=_serialize_vehicle(first_vehicle),
        vehicles=[_serialize_vehicle(first_vehicle)],
    )


@router.post("/auth/login", response_model=AuthResponse, summary="User Login")
def login(request: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Authenticates user with email and password, returning JWT token and vehicle profile."""
    email_clean = request.email.lower().strip()
    user = db.query(User).filter(func.lower(User.email) == email_clean).first()
    if not user or not user.password_hash or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).order_by(UserVehicle.id.asc()).all()
    selected_vehicle = next((v for v in vehicles if v.is_selected), None)
    if not selected_vehicle and vehicles:
        vehicles[0].is_selected = True
        db.commit()
        selected_vehicle = vehicles[0]
    elif not vehicles:
        # Create default vehicle for legacy user
        first_vehicle = UserVehicle(
            user_id=user.id,
            vehicle_name=user.vehicle_model or "Tata Nexon EV Max",
            battery_capacity_kwh=user.battery_capacity_kwh or 30.0,
            connector_type=user.preferred_connector_type or "CCS2",
            is_selected=True,
        )
        db.add(first_vehicle)
        db.commit()
        db.refresh(first_vehicle)
        vehicles = [first_vehicle]
        selected_vehicle = first_vehicle

    token = create_access_token(user_id=user.id, email=user.email)
    return AuthResponse(
        token=token,
        user=_serialize_user(user),
        selected_vehicle=_serialize_vehicle(selected_vehicle) if selected_vehicle else None,
        vehicles=[_serialize_vehicle(v) for v in vehicles],
    )


@router.get("/auth/me", response_model=AuthResponse, summary="Get Current Authenticated User")
def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AuthResponse:
    """Returns profile and vehicle list of currently logged-in user."""
    vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).order_by(UserVehicle.id.asc()).all()
    selected_vehicle = next((v for v in vehicles if v.is_selected), None)
    if not selected_vehicle and vehicles:
        vehicles[0].is_selected = True
        db.commit()
        selected_vehicle = vehicles[0]

    token = create_access_token(user_id=user.id, email=user.email)
    return AuthResponse(
        token=token,
        user=_serialize_user(user),
        selected_vehicle=_serialize_vehicle(selected_vehicle) if selected_vehicle else None,
        vehicles=[_serialize_vehicle(v) for v in vehicles],
    )


@router.post("/auth/logout", summary="Logout User")
def logout() -> Dict[str, str]:
    """Signals successful logout session."""
    return {"status": "ok", "message": "Logged out successfully"}


# ---------------------------------------------------------------------------
# 2. Vehicle Management Endpoints
# ---------------------------------------------------------------------------

@router.get("/vehicles", response_model=List[VehicleResponse], summary="List User Vehicles")
def list_vehicles(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> List[VehicleResponse]:
    """List all vehicle profiles belonging to the current user."""
    vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).order_by(UserVehicle.id.asc()).all()
    return [_serialize_vehicle(v) for v in vehicles]


@router.post("/vehicles", response_model=VehicleResponse, summary="Add New Vehicle Profile")
def create_vehicle(
    request: VehicleCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VehicleResponse:
    """Add a new vehicle profile and optionally set it as active."""
    existing_vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).all()
    should_select = request.is_selected or len(existing_vehicles) == 0

    if should_select:
        for v in existing_vehicles:
            v.is_selected = False

    new_v = UserVehicle(
        user_id=user.id,
        vehicle_name=request.vehicle_name.strip(),
        battery_capacity_kwh=request.battery_capacity_kwh,
        connector_type=request.connector_type.strip(),
        energy_consumption_kwh_per_km=request.energy_consumption_kwh_per_km,
        is_selected=should_select,
    )
    db.add(new_v)
    db.commit()
    db.refresh(new_v)
    return _serialize_vehicle(new_v)


@router.post("/vehicles/{vehicle_id}/select", response_model=VehicleResponse, summary="Select Active Vehicle")
def select_vehicle(
    vehicle_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> VehicleResponse:
    """Sets a vehicle profile as the currently active vehicle for planning."""
    vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).all()
    target_vehicle = next((v for v in vehicles if v.id == vehicle_id), None)
    if not target_vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found.",
        )

    for v in vehicles:
        v.is_selected = (v.id == vehicle_id)

    db.commit()
    db.refresh(target_vehicle)
    return _serialize_vehicle(target_vehicle)


@router.delete("/vehicles/{vehicle_id}", summary="Delete Vehicle Profile")
def delete_vehicle(
    vehicle_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Deletes a vehicle profile. If selected, automatically activates another available vehicle."""
    vehicles = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).all()
    target_vehicle = next((v for v in vehicles if v.id == vehicle_id), None)
    if not target_vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found.",
        )

    was_selected = target_vehicle.is_selected
    db.delete(target_vehicle)
    db.commit()

    # Re-evaluate remaining vehicles
    remaining = db.query(UserVehicle).filter(UserVehicle.user_id == user.id).all()
    if was_selected and remaining:
        remaining[0].is_selected = True
        db.commit()

    return {
        "status": "ok",
        "message": "Vehicle profile deleted successfully",
        "remaining_count": len(remaining),
    }


# ---------------------------------------------------------------------------
# 3. Trip History Endpoints
# ---------------------------------------------------------------------------

@router.post("/trips/history", response_model=TripHistoryItemResponse, summary="Save Completed Trip")
def save_trip_history(
    request: TripHistoryCreateRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> TripHistoryItemResponse:
    """Persists a completed journey record for the user or demo session."""
    trip = TripHistory(
        user_id=user.id if user else None,
        vehicle_name=request.vehicle_name,
        origin_name=request.origin_name,
        origin_lat=request.origin_lat,
        origin_lon=request.origin_lon,
        dest_name=request.dest_name,
        dest_lat=request.dest_lat,
        dest_lon=request.dest_lon,
        total_distance_km=request.total_distance_km,
        total_energy_kwh=request.total_energy_kwh,
        total_travel_time_minutes=request.total_travel_time_minutes,
        charging_stop_count=request.charging_stop_count,
        charging_stops_summary=request.charging_stops_summary,
        status=request.status,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return _serialize_trip(trip)


@router.get("/trips/history", response_model=List[TripHistoryItemResponse], summary="Get Trip History")
def get_trip_history(
    user: Optional[User] = Depends(get_current_user_optional),
    limit: int = 50,
    db: Session = Depends(get_db),
) -> List[TripHistoryItemResponse]:
    """Retrieves logged trips for current user, or recent global trips if unauthenticated."""
    query = db.query(TripHistory)
    if user:
        query = query.filter(TripHistory.user_id == user.id)
    trips = query.order_by(TripHistory.created_at.desc()).limit(limit).all()
    return [_serialize_trip(t) for t in trips]


# ---------------------------------------------------------------------------
# 4. Charger Review Endpoints
# ---------------------------------------------------------------------------

@router.get("/chargers/{charger_id}/reviews", response_model=ChargerReviewsSummaryResponse, summary="Get Charger Reviews")
def get_charger_reviews(charger_id: int, db: Session = Depends(get_db)) -> ChargerReviewsSummaryResponse:
    """Returns aggregated ratings and recent user reviews for a specific station."""
    reviews = db.query(Review, User).outerjoin(User, Review.user_id == User.id)\
        .filter(Review.charger_id == charger_id)\
        .order_by(Review.created_at.desc()).limit(20).all()

    rating_dist = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    total_rating = 0
    count = len(reviews)

    serialized_reviews: List[ReviewItemResponse] = []
    for r, u in reviews:
        stars_key = str(max(1, min(5, r.rating)))
        rating_dist[stars_key] = rating_dist.get(stars_key, 0) + 1
        total_rating += r.rating
        user_display = u.name if u else "EV Driver"
        serialized_reviews.append(
            ReviewItemResponse(
                id=r.id,
                charger_id=r.charger_id,
                user_id=r.user_id,
                user_name=user_display,
                rating=r.rating,
                review_text=r.review_text,
                sentiment_score=r.sentiment_score,
                created_at=r.created_at.isoformat() if r.created_at else datetime.now(timezone.utc).isoformat(),
            )
        )

    avg_rating = round(total_rating / count, 1) if count > 0 else 4.5

    return ChargerReviewsSummaryResponse(
        charger_id=charger_id,
        avg_rating=avg_rating,
        review_count=count,
        rating_distribution=rating_dist,
        recent_reviews=serialized_reviews,
    )


@router.post("/chargers/{charger_id}/reviews", response_model=ReviewItemResponse, summary="Submit Charger Review")
def submit_charger_review(
    charger_id: int,
    request: ReviewCreateRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> ReviewItemResponse:
    """Submits a rating (1-5) and comment for a charging station."""
    # Ensure charger exists
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        # If charger is loaded from CSV/cache, create mock or allow review
        pass

    # Simple normalized sentiment heuristic [-1.0, 1.0]
    sentiment_val = round((request.rating - 3.0) / 2.0, 2)

    new_rev = Review(
        charger_id=charger_id,
        user_id=user.id if user else None,
        rating=request.rating,
        review_text=request.review_text.strip() if request.review_text else None,
        sentiment_score=sentiment_val,
    )
    db.add(new_rev)
    db.commit()
    db.refresh(new_rev)

    user_name = user.name if user else "Verified EV Driver"
    return ReviewItemResponse(
        id=new_rev.id,
        charger_id=new_rev.charger_id,
        user_id=new_rev.user_id,
        user_name=user_name,
        rating=new_rev.rating,
        review_text=new_rev.review_text,
        sentiment_score=new_rev.sentiment_score,
        created_at=new_rev.created_at.isoformat() if new_rev.created_at else datetime.now(timezone.utc).isoformat(),
    )

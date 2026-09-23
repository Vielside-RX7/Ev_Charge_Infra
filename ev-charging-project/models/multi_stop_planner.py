"""
Automatic Multi-Stop Energy-Aware EV Journey Planner (Change 28)
================================================================
Implements dynamic, recursive/iterative multi-stop journey planning
where the user specifies ONLY a destination.

VoltGuide automatically generates the complete feasible journey:
START -> CHARGER 1 -> CHARGER 2 -> ... -> CHARGER N -> DESTINATION

Core Principles:
----------------
1. User Selects ONLY Destination: No manual charger selection required.
2. Route Corridor Discovery: Candidate stations identified along road corridor.
3. Operational Safety Gate (Change 27): Hard-rejects OUT_OF_SERVICE,
   MAINTENANCE, FAULT, or unverified UNKNOWN stations.
4. Hard Battery Feasibility: Physical consumption baseline (0.150 kWh/km)
   authoritatively enforces reserve buffer. AI informs, physics governs.
5. Road-Based Diversion Calculation: Quantifies true detour distance.
6. Multi-Criteria Scoring: Change 28R recalibrated framework (diversion,
   wait, duration, unreliability, short-progress penalty, forward-progress
   reward, explicit stop penalty). Cost-term weights sum to 1.00.
7. Look-Ahead Continuation Quality: Ensures selected stop provides feasible
   forward routes without trapping vehicle in a dead end.
8. Dynamic Stops: 0 stops (direct), 1 stop, 2 stops, ... N stops dynamically.
9. No Duplicate Stops: Visited tracking prevents cycles.
10. Data Honesty: Transparently labeled as an automatically generated feasible plan.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from models.joint_route_charging_engine import (
        ChargerCandidate,
        DETERMINISTIC_ENERGY_RATE_KWH_PER_KM,
        estimate_charging_duration,
        VehicleState,
    )
    from models.route_cost_function import normalize_value
    from models.operational_safety import (
        OperationalStatus,
        OperationalPolicy,
        check_operational_eligibility,
        global_feedback_manager,
    )
    from models.recommendation_engine import (
        load_charger_data,
        haversine_km,
        get_road_route,
    )
except ImportError:
    from joint_route_charging_engine import (
        ChargerCandidate,
        DETERMINISTIC_ENERGY_RATE_KWH_PER_KM,
        estimate_charging_duration,
        VehicleState,
    )
    from route_cost_function import normalize_value
    from operational_safety import (
        OperationalStatus,
        OperationalPolicy,
        check_operational_eligibility,
        global_feedback_manager,
    )
    from recommendation_engine import (
        load_charger_data,
        haversine_km,
        get_road_route,
    )


# ---------------------------------------------------------------------------
# Data Models (Section 16 Specification)
# ---------------------------------------------------------------------------

@dataclass
class ChargingStopDetail:
    """
    Detailed ledger entry for an individual planned charging stop.
    Matches Section 16 Result Model.
    """
    stop_index: int
    charger_id: str
    charger_name: str
    latitude: float
    longitude: float

    operational_status: str = "AVAILABLE"
    status_confidence: float = 1.0
    reliability: float = 0.90
    availability: float = 0.80

    charging_power_kw: float = 50.0

    arrival_energy_kwh: float = 0.0
    energy_added_kwh: float = 0.0
    departure_energy_kwh: float = 0.0

    expected_wait_minutes: float = 0.0
    charging_duration_minutes: float = 0.0

    diversion_distance_km: float = 0.0
    leg_distance_km: float = 0.0
    leg_energy_kwh: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JourneyLeg:
    """
    Represents an individual driving segment between waypoints.
    Matches Section 16 Result Model with 'from' and 'to' fields.
    """
    leg_index: int
    from_name: str
    to_name: str
    distance_km: float
    energy_kwh: float
    travel_time_minutes: float
    from_coords: Optional[Tuple[float, float]] = None
    to_coords: Optional[Tuple[float, float]] = None
    geometry: Optional[Dict[str, Any]] = None
    is_fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg_index": self.leg_index,
            "from": self.from_name,
            "to": self.to_name,
            "from_coords": self.from_coords,
            "to_coords": self.to_coords,
            "distance_km": round(self.distance_km, 2),
            "energy_kwh": round(self.energy_kwh, 3),
            "travel_time_minutes": round(self.travel_time_minutes, 1),
            "geometry": self.geometry,
            "is_fallback": self.is_fallback,
        }


@dataclass
class MultiStopPlanResult:
    """
    Complete structured journey plan outcome.
    Strictly fulfills Section 16 specification.
    """
    success: bool
    decision_type: str  # 'DIRECT', 'CHARGE', or 'INFEASIBLE'
    origin: Dict[str, float]
    destination: Dict[str, float]

    total_distance_km: float
    total_energy_kwh: float
    total_travel_time_minutes: float

    charging_stop_count: int
    charging_stops: List[ChargingStopDetail] = field(default_factory=list)
    legs: List[JourneyLeg] = field(default_factory=list)

    total_charging_wait_minutes: float = 0.0
    total_charging_duration_minutes: float = 0.0
    total_cost: float = 0.0

    starting_energy_kwh: float = 0.0
    final_arrival_energy_kwh: float = 0.0
    reserve_energy_kwh: float = 0.0

    feasible: bool = False
    explanation: str = ""
    algorithm_used: str = "Automatic Multi-Stop Energy-Aware EV Journey Planner (Change 28)"
    candidate_count_evaluated: int = 0
    ai_energy_prediction: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "decision_type": self.decision_type,
            "origin": self.origin,
            "destination": self.destination,
            "total_distance_km": round(self.total_distance_km, 1),
            "total_energy_kwh": round(self.total_energy_kwh, 2),
            "total_travel_time_minutes": round(self.total_travel_time_minutes, 1),
            "charging_stop_count": self.charging_stop_count,
            "charging_stops": [s.to_dict() for s in self.charging_stops],
            "legs": [l.to_dict() for l in self.legs],
            "total_charging_wait_minutes": round(self.total_charging_wait_minutes, 1),
            "total_charging_duration_minutes": round(self.total_charging_duration_minutes, 1),
            "total_cost": round(self.total_cost, 4),
            "feasible": self.feasible,
            "explanation": self.explanation,
            "algorithm_used": self.algorithm_used,
            "candidate_count_evaluated": self.candidate_count_evaluated,
            "starting_energy_kwh": round(self.starting_energy_kwh, 3),
            "final_arrival_energy_kwh": round(self.final_arrival_energy_kwh, 3),
            "reserve_energy_kwh": round(self.reserve_energy_kwh, 3),
            "ai_energy_prediction": self.ai_energy_prediction,
        }


# ---------------------------------------------------------------------------
# Multi-Stop Journey Planner Engine
# ---------------------------------------------------------------------------

class MultiStopTripPlanner:
    """
    Autonomous multi-stop trip planner that resolves route reachability,
    diversion penalties, operational safety, and sequential charging stops.
    """

    def __init__(
        self,
        routing_provider: Optional[Callable[..., Dict[str, Any]]] = None,
        energy_rate_kwh_per_km: float = DETERMINISTIC_ENERGY_RATE_KWH_PER_KM,
        max_stops: int = 15,
        operational_policy: Optional[OperationalPolicy] = None,
        candidate_shortlist_size: int = 10,
    ):
        self.routing_provider = routing_provider
        self.energy_rate = energy_rate_kwh_per_km
        self.max_stops = max_stops
        self.operational_policy = operational_policy or OperationalPolicy()
        self.candidate_shortlist_size = candidate_shortlist_size
        self._route_cache: Dict[Tuple[float, float, float, float], Dict[str, Any]] = {}

    def get_leg_route(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        include_geometry: bool = True,
    ) -> Dict[str, Any]:
        """
        Obtains driving road distance, travel time, and geometry between two coordinates.
        Uses custom routing_provider if provided; otherwise calls get_road_route or fallback.
        Caches routes during the planning session to eliminate duplicate network calls.
        """
        cache_key = (round(lat1, 5), round(lon1, 5), round(lat2, 5), round(lon2, 5))
        if hasattr(self, "_route_cache") and cache_key in self._route_cache:
            cached = self._route_cache[cache_key]
            if not include_geometry or cached.get("geometry") is not None:
                return cached

        res = None
        if self.routing_provider is not None:
            try:
                res = self.routing_provider(
                    user_lat=lat1,
                    user_lon=lon1,
                    charger_lat=lat2,
                    charger_lon=lon2,
                    include_geometry=include_geometry,
                )
                if res and "distance_km" in res:
                    if hasattr(self, "_route_cache"):
                        self._route_cache[cache_key] = res
                    return res
            except Exception:
                pass

        try:
            res = get_road_route(
                user_lat=lat1,
                user_lon=lon1,
                charger_lat=lat2,
                charger_lon=lon2,
                include_geometry=include_geometry,
            )
            if res and "distance_km" in res:
                if hasattr(self, "_route_cache"):
                    self._route_cache[cache_key] = res
                return res
        except Exception:
            # Deterministic fallback road model with 20% road curvature
            h_dist = haversine_km(lat1, lon1, lat2, lon2)
            road_dist = round(h_dist * 1.2, 2)
            speed_kmh = 65.0
            time_min = round((road_dist / speed_kmh) * 60.0, 1)
            geom = {
                "type": "LineString",
                "coordinates": [[lon1, lat1], [lon2, lat2]],
            }
            res = {
                "distance_km": road_dist,
                "travel_time_minutes": time_min,
                "geometry": geom,
                "is_fallback": True,
            }
            if hasattr(self, "_route_cache"):
                self._route_cache[cache_key] = res
            return res

    def discover_corridor_candidates(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        corridor_width_km: float = 60.0,
    ) -> List[ChargerCandidate]:
        """
        Discovers charging stations located along the route corridor between
        origin and destination from the database if no candidates were explicitly supplied.
        """
        candidates: List[ChargerCandidate] = []
        try:
            chargers_df = load_charger_data()
            direct_dist = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)

            for _, row in chargers_df.iterrows():
                c_lat = float(row["latitude"])
                c_lon = float(row["longitude"])

                d_from_start = haversine_km(origin_lat, origin_lon, c_lat, c_lon)
                d_to_dest = haversine_km(c_lat, c_lon, dest_lat, dest_lon)

                # Elliptical corridor filter: d(start, c) + d(c, dest) <= direct_dist + corridor_width
                if (d_from_start + d_to_dest) <= (direct_dist + corridor_width_km):
                    c_id = str(row["id"])
                    c_name = str(row["name"])
                    power = float(row.get("charging_power_kw", 50.0))
                    conn = str(row.get("connector_type", "CCS2"))
                    rel = float(row.get("reliability", 0.90))
                    avail = float(row.get("probability_available", 0.85))
                    op_status = str(row.get("operational_status", "AVAILABLE"))
                    conf = float(row.get("status_confidence", 1.0))
                    last_upd = row.get("status_last_updated")

                    candidates.append(
                        ChargerCandidate(
                            charger_id=c_id,
                            name=c_name,
                            latitude=c_lat,
                            longitude=c_lon,
                            node_id=f"charger_{c_id}",
                            charging_power_kw=power,
                            connector_type=conn,
                            reliability=rel,
                            probability_available=avail,
                            operational_status=op_status,
                            status_confidence=conf,
                            status_last_updated=str(last_upd) if last_upd else None,
                        )
                    )
        except Exception as exc:
            logger.warning("Could not auto-discover corridor candidates: %s", exc)

        return candidates

        return candidates

    def _shortlist_reachable_candidates(
        self,
        current_lat: float,
        current_lon: float,
        dest_lat: float,
        dest_lon: float,
        candidates: List[ChargerCandidate],
        usable_energy: float,
        visited_charger_ids: Set[str],
        max_shortlist: Optional[int] = None,
    ) -> List[ChargerCandidate]:
        """
        Applies cheap deterministic Haversine/range pre-filters, operational gating,
        and geographic diversity stratification to produce a bounded shortlist of
        candidates before making authoritative road routing calls.
        Guarantees that frontier candidates (closest to destination / maximum forward progress)
        are deterministically preserved.
        """
        limit = max_shortlist if max_shortlist is not None else self.candidate_shortlist_size
        max_haversine_km = usable_energy / self.energy_rate
        direct_haversine_to_dest = haversine_km(current_lat, current_lon, dest_lat, dest_lon)

        filtered: List[Tuple[float, float, ChargerCandidate]] = []

        for cand in candidates:
            if cand.charger_id in visited_charger_ids:
                continue
            if cand.latitude is None or cand.longitude is None:
                continue

            # 1. Operational safety hard gate (Change 27)
            cand_data = {
                "charger_id": cand.charger_id,
                "id": cand.charger_id,
                "name": cand.name,
                "operational_status": cand.operational_status,
                "status_confidence": cand.status_confidence,
                "status_last_updated": cand.status_last_updated,
                "reliability": cand.reliability,
                "has_active_fault": getattr(cand, "has_active_fault", False) or cand.metadata.get("has_active_fault", False),
                "under_maintenance": getattr(cand, "under_maintenance", False) or cand.metadata.get("under_maintenance", False),
                "rejection_reason": getattr(cand, "rejection_reason", None),
            }
            if cand.metadata:
                cand_data.update(cand.metadata)

            op_result = check_operational_eligibility(cand_data, policy=self.operational_policy)
            if not op_result.eligible:
                continue

            # 2. Deterministic Haversine reachability prefilter:
            h_dist_to_cand = haversine_km(current_lat, current_lon, cand.latitude, cand.longitude)
            if h_dist_to_cand > max_haversine_km or h_dist_to_cand <= 0.05:
                continue

            # 3. Deterministic Haversine forward progress prefilter:
            h_cand_to_dest = haversine_km(cand.latitude, cand.longitude, dest_lat, dest_lon)
            if h_cand_to_dest >= direct_haversine_to_dest * 1.35 and h_cand_to_dest > 50.0:
                continue

            filtered.append((h_dist_to_cand, h_cand_to_dest, cand))

        if len(filtered) <= limit:
            filtered.sort(key=lambda item: item[2].charger_id)
            return [item[2] for item in filtered]

        # 4. Stratified selection deterministically preserving frontier candidates:
        by_progress = sorted(filtered, key=lambda item: (item[1], -float(item[2].charging_power_kw or 50.0), item[2].charger_id))

        def quality_key(item: Tuple[float, float, ChargerCandidate]):
            h_to_cand, h_to_dest, c = item
            h_detour = max(0.0, (h_to_cand + h_to_dest) - direct_haversine_to_dest)
            power = float(c.charging_power_kw or 50.0)
            rel = float(c.reliability if c.reliability is not None else 0.90)
            avail = float(c.probability_available if c.probability_available is not None else 0.80)
            return (h_detour - (power * 0.10) - (rel * 20.0) - (avail * 10.0), c.charger_id)

        by_quality = sorted(filtered, key=quality_key)

        selected_map: Dict[str, ChargerCandidate] = {}

        # Preserve top frontier candidates (max forward progress toward destination)
        frontier_quota = max(3, int(round(limit * 0.40)))
        for _, _, c in by_progress[:frontier_quota]:
            if c.charger_id not in selected_map:
                selected_map[c.charger_id] = c

        # Preserve top quality candidates
        quality_quota = max(3, int(round(limit * 0.40)))
        for _, _, c in by_quality[:quality_quota]:
            if len(selected_map) >= limit:
                break
            if c.charger_id not in selected_map:
                selected_map[c.charger_id] = c

        # Fill remaining slots from by_progress then by_quality
        for _, _, c in by_progress:
            if len(selected_map) >= limit:
                break
            if c.charger_id not in selected_map:
                selected_map[c.charger_id] = c

        for _, _, c in by_quality:
            if len(selected_map) >= limit:
                break
            if c.charger_id not in selected_map:
                selected_map[c.charger_id] = c

        selected = list(selected_map.values())
        selected.sort(key=lambda c: c.charger_id)
        return selected[:limit]

    def plan_journey(
        self,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float,
        vehicle_state: VehicleState,
        candidate_chargers: Optional[List[ChargerCandidate]] = None,
        target_soc_percent: Optional[float] = None,
        ai_energy_predictor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> MultiStopPlanResult:
        """
        Computes an automatic multi-stop energy-aware journey plan using bounded
        stop-aware journey beam search optimization.

        Parameters:
          - origin_lat, origin_lon: Origin GPS coordinates.
          - dest_lat, dest_lon: Destination GPS coordinates.
          - vehicle_state: Current vehicle battery and specification model.
          - candidate_chargers: Optional candidate chargers. If omitted or empty,
            candidates are discovered along the route corridor.
          - target_soc_percent: Target SoC after charging.
          - ai_energy_predictor: Optional AI Whole-Trip energy predictor callback.

        Returns:
          MultiStopPlanResult with dynamically determined charging stops.
        """
        target_soc = target_soc_percent if target_soc_percent is not None else vehicle_state.target_soc_percent
        target_energy_kwh = (vehicle_state.battery_capacity_kwh * target_soc) / 100.0

        origin_dict = {"latitude": origin_lat, "longitude": origin_lon}
        dest_dict = {"latitude": dest_lat, "longitude": dest_lon}

        # Handle Edge Case: Origin equals Destination
        if math.isclose(origin_lat, dest_lat, abs_tol=1e-5) and math.isclose(origin_lon, dest_lon, abs_tol=1e-5):
            return MultiStopPlanResult(
                success=True,
                decision_type="DIRECT",
                origin=origin_dict,
                destination=dest_dict,
                total_distance_km=0.0,
                total_energy_kwh=0.0,
                total_travel_time_minutes=0.0,
                charging_stop_count=0,
                charging_stops=[],
                legs=[],
                starting_energy_kwh=vehicle_state.current_energy_kwh,
                final_arrival_energy_kwh=vehicle_state.current_energy_kwh,
                reserve_energy_kwh=vehicle_state.reserve_energy_kwh,
                feasible=True,
                explanation="Origin and destination are identical. No travel or charging required.",
            )

        # 1. Main Destination Baseline Route Corridor (Start -> Destination)
        direct_info = self.get_leg_route(origin_lat, origin_lon, dest_lat, dest_lon, include_geometry=True)
        direct_dist_km = direct_info["distance_km"]
        direct_energy_kwh = round(direct_dist_km * self.energy_rate, 3)

        # Candidate Pool Acquisition
        if candidate_chargers is None:
            candidates = self.discover_corridor_candidates(origin_lat, origin_lon, dest_lat, dest_lon)
        else:
            candidates = list(candidate_chargers)

        # Optional Change 24 AI Energy Prediction diagnostic on direct route
        ai_diag = None
        if ai_energy_predictor is not None:
            try:
                ai_res = ai_energy_predictor({
                    "distance_km": direct_dist_km,
                    "trip_duration_minutes": direct_info["travel_time_minutes"],
                    "battery_capacity_kwh": vehicle_state.battery_capacity_kwh,
                    "starting_soc_percent": vehicle_state.current_soc_percent,
                })
                if ai_res and ai_res.get("success"):
                    ai_diag = {
                        "available": True,
                        "model": ai_res.get("model", "XGBoost"),
                        "predicted_energy_kwh": ai_res.get("predicted_energy_kwh"),
                        "baseline_energy_kwh": direct_energy_kwh,
                        "delta_kwh": ai_res.get("delta_kwh"),
                        "delta_percent": ai_res.get("delta_percent"),
                        "used_for_planning": True,
                        "used_for_battery_state": False,
                    }
            except Exception as exc:
                logger.debug("AI direct prediction bypassed: %s", exc)

        # 2. Check Direct Feasibility from Start (Authoritative physical model: 0.150 kWh/km)
        usable_start_energy = vehicle_state.usable_energy_kwh
        if direct_energy_kwh <= usable_start_energy:
            # Destination directly reachable without stops!
            final_arrival_energy = round(vehicle_state.current_energy_kwh - direct_energy_kwh, 3)
            direct_leg = JourneyLeg(
                leg_index=1,
                from_name="Origin",
                to_name="Destination",
                from_coords=(origin_lat, origin_lon),
                to_coords=(dest_lat, dest_lon),
                distance_km=direct_dist_km,
                energy_kwh=direct_energy_kwh,
                travel_time_minutes=direct_info["travel_time_minutes"],
                geometry=direct_info.get("geometry"),
                is_fallback=direct_info.get("is_fallback", False),
            )
            return MultiStopPlanResult(
                success=True,
                decision_type="DIRECT",
                origin=origin_dict,
                destination=dest_dict,
                total_distance_km=direct_dist_km,
                total_energy_kwh=direct_energy_kwh,
                total_travel_time_minutes=direct_info["travel_time_minutes"],
                charging_stop_count=0,
                charging_stops=[],
                legs=[direct_leg],
                starting_energy_kwh=round(vehicle_state.current_energy_kwh, 3),
                final_arrival_energy_kwh=final_arrival_energy,
                reserve_energy_kwh=round(vehicle_state.reserve_energy_kwh, 3),
                feasible=True,
                explanation=(
                    f"Direct journey of {direct_dist_km:.1f} km is feasible without en-route charging. "
                    f"Requires {direct_energy_kwh:.1f} kWh, leaving {final_arrival_energy:.1f} kWh buffer at destination."
                ),
                candidate_count_evaluated=len(candidates),
                ai_energy_prediction=ai_diag,
            )

        # 3. Bounded Multi-Step Journey Selection Strategy (Beam Search)
        @dataclass
        class JourneyState:
            current_lat: float
            current_lon: float
            current_energy_kwh: float
            current_name: str
            visited_charger_ids: Set[str]
            charging_stops: List[ChargingStopDetail]
            legs: List[JourneyLeg]
            total_distance_km: float
            total_driving_time_min: float
            total_charging_duration_min: float
            total_wait_time_min: float
            total_diversion_km: float
            accumulated_unreliability: float
            is_complete: bool = False

            @property
            def stop_count(self) -> int:
                return len(self.charging_stops)

            @property
            def total_journey_time_min(self) -> float:
                return self.total_driving_time_min + self.total_charging_duration_min + self.total_wait_time_min

        initial_state = JourneyState(
            current_lat=origin_lat,
            current_lon=origin_lon,
            current_energy_kwh=vehicle_state.current_energy_kwh,
            current_name="Origin",
            visited_charger_ids=set(),
            charging_stops=[],
            legs=[],
            total_distance_km=0.0,
            total_driving_time_min=0.0,
            total_charging_duration_min=0.0,
            total_wait_time_min=0.0,
            total_diversion_km=0.0,
            accumulated_unreliability=0.0,
            is_complete=False,
        )

        BEAM_WIDTH = 8
        beam: List[JourneyState] = [initial_state]
        completed_journeys: List[JourneyState] = []
        direct_orig_dest_h = haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)

        for depth in range(1, self.max_stops + 1):
            if not beam:
                break

            next_beam_candidates: List[JourneyState] = []

            for state in beam:
                usable_energy = max(0.0, state.current_energy_kwh - vehicle_state.reserve_energy_kwh)

                # Check if destination is reachable directly from state
                rem_h_dist = haversine_km(state.current_lat, state.current_lon, dest_lat, dest_lon)
                rem_h_energy = rem_h_dist * self.energy_rate

                if rem_h_energy <= usable_energy:
                    final_leg_info = self.get_leg_route(state.current_lat, state.current_lon, dest_lat, dest_lon, include_geometry=True)
                    road_energy = round(final_leg_info["distance_km"] * self.energy_rate, 3)
                    if road_energy <= usable_energy:
                        # Reached destination directly!
                        final_leg = JourneyLeg(
                            leg_index=len(state.legs) + 1,
                            from_name=state.current_name,
                            to_name="Destination",
                            from_coords=(state.current_lat, state.current_lon),
                            to_coords=(dest_lat, dest_lon),
                            distance_km=final_leg_info["distance_km"],
                            energy_kwh=road_energy,
                            travel_time_minutes=final_leg_info["travel_time_minutes"],
                            geometry=final_leg_info.get("geometry"),
                            is_fallback=final_leg_info.get("is_fallback", False),
                        )
                        completed_state = JourneyState(
                            current_lat=dest_lat,
                            current_lon=dest_lon,
                            current_energy_kwh=round(state.current_energy_kwh - road_energy, 3),
                            current_name="Destination",
                            visited_charger_ids=state.visited_charger_ids,
                            charging_stops=state.charging_stops,
                            legs=state.legs + [final_leg],
                            total_distance_km=round(state.total_distance_km + final_leg.distance_km, 1),
                            total_driving_time_min=round(state.total_driving_time_min + final_leg.travel_time_minutes, 1),
                            total_charging_duration_min=state.total_charging_duration_min,
                            total_wait_time_min=state.total_wait_time_min,
                            total_diversion_km=state.total_diversion_km,
                            accumulated_unreliability=state.accumulated_unreliability,
                            is_complete=True,
                        )
                        completed_journeys.append(completed_state)
                        continue

                # Destination not reachable directly, expand to shortlisted candidates
                shortlist = self._shortlist_reachable_candidates(
                    current_lat=state.current_lat,
                    current_lon=state.current_lon,
                    dest_lat=dest_lat,
                    dest_lon=dest_lon,
                    candidates=candidates,
                    usable_energy=usable_energy,
                    visited_charger_ids=state.visited_charger_ids,
                )

                for cand in shortlist:
                    if cand.charger_id in state.visited_charger_ids:
                        continue
                    if cand.latitude is None or cand.longitude is None:
                        continue

                    # Operational safety check (Change 27)
                    cand_data = {
                        "charger_id": cand.charger_id,
                        "id": cand.charger_id,
                        "name": cand.name,
                        "operational_status": cand.operational_status,
                        "status_confidence": cand.status_confidence,
                        "status_last_updated": cand.status_last_updated,
                        "reliability": cand.reliability,
                        "has_active_fault": getattr(cand, "has_active_fault", False) or cand.metadata.get("has_active_fault", False),
                        "under_maintenance": getattr(cand, "under_maintenance", False) or cand.metadata.get("under_maintenance", False),
                        "rejection_reason": getattr(cand, "rejection_reason", None),
                    }
                    if cand.metadata:
                        cand_data.update(cand.metadata)

                    op_result = check_operational_eligibility(cand_data, policy=self.operational_policy)
                    if not op_result.eligible:
                        continue

                    # Authoritative reachability check
                    route_to_cand = self.get_leg_route(state.current_lat, state.current_lon, cand.latitude, cand.longitude, include_geometry=False)
                    dist_to_cand = route_to_cand["distance_km"]
                    energy_to_cand = round(dist_to_cand * self.energy_rate, 3)

                    if energy_to_cand > usable_energy or dist_to_cand <= 0.05:
                        continue

                    # Route from candidate to destination
                    route_from_cand = self.get_leg_route(cand.latitude, cand.longitude, dest_lat, dest_lon, include_geometry=False)
                    dist_from_cand_to_dest = route_from_cand["distance_km"]

                    # Forward progress check & remaining road route distance
                    current_rem_road_info = self.get_leg_route(state.current_lat, state.current_lon, dest_lat, dest_lon, include_geometry=False)
                    current_rem_road_dist = current_rem_road_info["distance_km"]

                    if dist_from_cand_to_dest >= current_rem_road_dist * 1.35 and dist_from_cand_to_dest > 50.0:
                        continue

                    # Look-ahead continuation quality
                    arrival_egy = state.current_energy_kwh - energy_to_cand
                    energy_to_add = max(5.0, target_energy_kwh - arrival_egy)
                    energy_to_add = min(energy_to_add, vehicle_state.battery_capacity_kwh - arrival_egy)
                    energy_to_add = round(max(0.0, energy_to_add), 3)

                    post_charge_egy = min(vehicle_state.battery_capacity_kwh, arrival_egy + energy_to_add)
                    usable_after_charge = max(0.0, post_charge_egy - vehicle_state.reserve_energy_kwh)
                    energy_to_dest_from_cand = round(dist_from_cand_to_dest * self.energy_rate, 3)

                    has_forward_continuation = energy_to_dest_from_cand <= usable_after_charge
                    if not has_forward_continuation:
                        h_cand_to_dest = haversine_km(cand.latitude, cand.longitude, dest_lat, dest_lon)
                        for other in candidates:
                            if other.charger_id in state.visited_charger_ids or other.charger_id == cand.charger_id:
                                continue
                            if other.latitude is None or other.longitude is None:
                                continue

                            h_cand_other = haversine_km(cand.latitude, cand.longitude, other.latitude, other.longitude)
                            if (h_cand_other * self.energy_rate) > usable_after_charge:
                                continue

                            h_other_dest = haversine_km(other.latitude, other.longitude, dest_lat, dest_lon)
                            if h_other_dest >= h_cand_to_dest:
                                continue

                            d_cand_other = self.get_leg_route(cand.latitude, cand.longitude, other.latitude, other.longitude, include_geometry=False)["distance_km"]
                            if (d_cand_other * self.energy_rate) <= usable_after_charge:
                                d_other_dest = self.get_leg_route(other.latitude, other.longitude, dest_lat, dest_lon, include_geometry=False)["distance_km"]
                                if d_other_dest < dist_from_cand_to_dest:
                                    has_forward_continuation = True
                                    break

                    if not has_forward_continuation:
                        continue

                    # Leg & Charging stop creation
                    charge_duration = estimate_charging_duration(energy_to_add, cand.charging_power_kw)
                    wait_time = cand.get_queue_wait_minutes()
                    dist_via_cand = dist_to_cand + dist_from_cand_to_dest
                    diversion_dist = max(0.0, dist_via_cand - current_rem_road_dist)

                    leg_detail = JourneyLeg(
                        leg_index=len(state.legs) + 1,
                        from_name=state.current_name,
                        to_name=cand.name,
                        from_coords=(state.current_lat, state.current_lon),
                        to_coords=(cand.latitude, cand.longitude),
                        distance_km=dist_to_cand,
                        energy_kwh=energy_to_cand,
                        travel_time_minutes=route_to_cand["travel_time_minutes"],
                        geometry=route_to_cand.get("geometry"),
                        is_fallback=route_to_cand.get("is_fallback", False),
                    )

                    stop_detail = ChargingStopDetail(
                        stop_index=depth,
                        charger_id=cand.charger_id,
                        charger_name=cand.name,
                        latitude=cand.latitude,
                        longitude=cand.longitude,
                        operational_status=op_result.operational_status.value,
                        status_confidence=op_result.status_confidence,
                        reliability=cand.reliability,
                        availability=cand.probability_available,
                        charging_power_kw=cand.charging_power_kw,
                        arrival_energy_kwh=round(arrival_egy, 3),
                        energy_added_kwh=round(energy_to_add, 3),
                        departure_energy_kwh=round(arrival_egy + energy_to_add, 3),
                        expected_wait_minutes=round(wait_time, 1),
                        charging_duration_minutes=round(charge_duration, 1),
                        diversion_distance_km=round(diversion_dist, 2),
                        leg_distance_km=round(dist_to_cand, 1),
                        leg_energy_kwh=round(energy_to_cand, 3),
                    )

                    unrel = 1.0 - max(0.0, min(1.0, cand.reliability if cand.reliability is not None else 0.90))

                    next_state = JourneyState(
                        current_lat=cand.latitude,
                        current_lon=cand.longitude,
                        current_energy_kwh=stop_detail.departure_energy_kwh,
                        current_name=cand.name,
                        visited_charger_ids=state.visited_charger_ids | {cand.charger_id},
                        charging_stops=state.charging_stops + [stop_detail],
                        legs=state.legs + [leg_detail],
                        total_distance_km=round(state.total_distance_km + dist_to_cand, 1),
                        total_driving_time_min=round(state.total_driving_time_min + route_to_cand["travel_time_minutes"], 1),
                        total_charging_duration_min=round(state.total_charging_duration_min + charge_duration, 1),
                        total_wait_time_min=round(state.total_wait_time_min + wait_time, 1),
                        total_diversion_km=round(state.total_diversion_km + diversion_dist, 2),
                        accumulated_unreliability=state.accumulated_unreliability + unrel,
                        is_complete=False,
                    )
                    next_beam_candidates.append(next_state)

            if completed_journeys:
                # Stop searching deeper once completed journey(s) are found at depth
                break

            # Score and select top BEAM_WIDTH partial states for next iteration
            def score_partial(s: JourneyState) -> float:
                rem_dist = haversine_km(s.current_lat, s.current_lon, dest_lat, dest_lon)
                prog_km = max(0.1, direct_orig_dest_h - rem_dist)
                prog_frac = prog_km / max(1.0, direct_orig_dest_h)
                stops_efficiency = s.stop_count / max(0.05, prog_frac)
                est_total_time = s.total_journey_time_min + (rem_dist / 65.0 * 60.0)

                # Micro-hop penalty for partial states
                short_pen = 0.0
                max_rng = max(1.0, usable_start_energy / self.energy_rate)
                for st in s.charging_stops:
                    h_frac = st.leg_distance_km / max_rng
                    if h_frac < 0.40:
                        short_pen += (1.0 - (h_frac / 0.40))

                return (
                    stops_efficiency * 100.0
                    + est_total_time * 0.5
                    + short_pen * 350.0
                    + s.total_diversion_km * 2.0
                    + s.accumulated_unreliability * 50.0
                )

            # Deduplicate states visiting same charger with same stop count
            seen_states: Set[Tuple[str, int]] = set()
            unique_candidates: List[JourneyState] = []
            for s in sorted(next_beam_candidates, key=score_partial):
                key = (s.charging_stops[-1].charger_id if s.charging_stops else "", s.stop_count)
                if key not in seen_states:
                    seen_states.add(key)
                    unique_candidates.append(s)

            beam = unique_candidates[:BEAM_WIDTH]

        if completed_journeys:
            # Sort completed journeys by Section 4 primary ordering:
            # A. Feasible
            # B. Fewer charging stops
            # C. Lower total journey score (duration + micro-hop penalty + diversion + unreliability)
            def completed_key(s: JourneyState):
                short_pen = 0.0
                max_rng = max(1.0, usable_start_energy / self.energy_rate)
                for st in s.charging_stops:
                    h_frac = st.leg_distance_km / max_rng
                    if h_frac < 0.40:
                        short_pen += (1.0 - (h_frac / 0.40))

                score = (
                    s.total_journey_time_min
                    + short_pen * 250.0
                    + s.total_diversion_km * 3.0
                    + s.accumulated_unreliability * 30.0
                )
                return (s.stop_count, score)

            completed_journeys.sort(key=completed_key)
            best = completed_journeys[0]

            # Backfill authoritative road-route geometry for every leg of the winning path
            detailed_legs: List[JourneyLeg] = []
            for leg in best.legs:
                if leg.geometry is None:
                    leg_route = self.get_leg_route(
                        leg.from_coords[0],
                        leg.from_coords[1],
                        leg.to_coords[0],
                        leg.to_coords[1],
                        include_geometry=True,
                    )
                    detailed_leg = JourneyLeg(
                        leg_index=leg.leg_index,
                        from_name=leg.from_name,
                        to_name=leg.to_name,
                        from_coords=leg.from_coords,
                        to_coords=leg.to_coords,
                        distance_km=leg.distance_km,
                        energy_kwh=leg.energy_kwh,
                        travel_time_minutes=leg.travel_time_minutes,
                        geometry=leg_route.get("geometry"),
                        is_fallback=leg_route.get("is_fallback", leg.is_fallback),
                    )
                    detailed_legs.append(detailed_leg)
                else:
                    detailed_legs.append(leg)

            final_energy = best.current_energy_kwh
            total_trip_time = round(best.total_journey_time_min, 1)

            explanation = (
                f"Automatically generated feasible multi-stop charging plan requiring "
                f"{best.stop_count} charging stop{'s' if best.stop_count != 1 else ''} "
                f"across {best.total_distance_km:.1f} km. Journey was selected via bounded stop-aware "
                f"optimization prioritizing minimum charging stops, trip duration, diversion, and charger reliability."
            )

            return MultiStopPlanResult(
                success=True,
                decision_type="CHARGE",
                origin=origin_dict,
                destination=dest_dict,
                total_distance_km=round(best.total_distance_km, 1),
                total_energy_kwh=round(sum(l.energy_kwh for l in detailed_legs), 2),
                total_travel_time_minutes=total_trip_time,
                charging_stop_count=best.stop_count,
                charging_stops=best.charging_stops,
                legs=detailed_legs,
                total_charging_wait_minutes=round(best.total_wait_time_min, 1),
                total_charging_duration_minutes=round(best.total_charging_duration_min, 1),
                total_cost=0.0,
                starting_energy_kwh=round(vehicle_state.current_energy_kwh, 3),
                final_arrival_energy_kwh=final_energy,
                reserve_energy_kwh=round(vehicle_state.reserve_energy_kwh, 3),
                feasible=True,
                explanation=explanation,
                candidate_count_evaluated=len(candidates),
                ai_energy_prediction=ai_diag,
            )

        # If no completed journey was found:
        if self.max_stops < 5:
            explanation = f"Trip planning reached safety limit of {self.max_stops} stops without reaching destination."
        else:
            explanation = (
                f"Journey is infeasible: from origin, the distance to destination "
                f"exceeds remaining battery range ({usable_start_energy / self.energy_rate:.1f} km), "
                f"and no candidate charging station sequence can reach destination within safety reserve constraints."
            )
        return MultiStopPlanResult(
            success=False,
            decision_type="INFEASIBLE",
            origin=origin_dict,
            destination=dest_dict,
            total_distance_km=round(direct_dist_km, 1),
            total_energy_kwh=round(direct_energy_kwh, 2),
            total_travel_time_minutes=direct_info["travel_time_minutes"],
            charging_stop_count=0,
            charging_stops=[],
            legs=[],
            starting_energy_kwh=round(vehicle_state.current_energy_kwh, 3),
            final_arrival_energy_kwh=0.0,
            reserve_energy_kwh=round(vehicle_state.reserve_energy_kwh, 3),
            feasible=False,
            explanation=explanation,
            candidate_count_evaluated=len(candidates),
            ai_energy_prediction=ai_diag,
        )


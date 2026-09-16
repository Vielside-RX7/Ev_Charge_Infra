"""
Joint Route + Charging Decision Engine
======================================
Task 19: Orchestration layer jointly evaluating route alternatives and
candidate charging stops using the Change 18B Energy-Aware Modified A* framework
and Change 18A multi-criteria cost function.

Objective:
----------
Determine whether the target destination is reachable directly without charging (DIRECT),
or whether a charging stop is required (CHARGE), and when required, identify the best
feasible charging stop and complete two-leg journey plan.

Scope: Phase 1 supports ZERO or ONE charging stop.

Research Traceability & Source Attribution:
--------------------------------------------
- Vehicle State: Virtual EV telemetry (current location, battery capacity, SoC, connector).
- Charger Candidates: Charger recommendation engine outputs (power, availability, reliability).
- Physical Energy Baseline: 0.150 kWh/km deterministic consumption model.
- Path Search: Energy-Aware Modified A* from models/modified_astar.py.
- Multi-Criteria Cost: Change 18A cost formula from models/route_cost_function.py.
- Future Enhancements: Real-world ML traffic predictions, live OCPP station queues.
"""

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

# Reusable Change 18A and 18B interfaces
from route_cost_function import (
    CostWeights,
    NormalizationBounds,
    RouteCostResult,
    compute_route_cost,
    DEFAULT_WEIGHTS,
    DEFAULT_BOUNDS,
)
from modified_astar import (
    GraphNode,
    GraphEdge,
    RoutingGraph,
    SearchResult,
    search_energy_aware_path,
    search_shortest_path,
)

# Authoritative vehicle energy consumption baseline
DETERMINISTIC_ENERGY_RATE_KWH_PER_KM = 0.150


@dataclass
class VehicleState:
    """
    Snapshot of EV battery and physical specifications.
    """
    battery_capacity_kwh: float = 30.0
    current_soc_percent: float = 80.0
    connector_type: str = "CCS2"
    reserve_battery_percent: float = 5.0
    target_soc_percent: float = 80.0
    energy_consumption_kwh_per_km: float = DETERMINISTIC_ENERGY_RATE_KWH_PER_KM

    @property
    def current_energy_kwh(self) -> float:
        return (self.battery_capacity_kwh * self.current_soc_percent) / 100.0

    @property
    def reserve_energy_kwh(self) -> float:
        return (self.battery_capacity_kwh * self.reserve_battery_percent) / 100.0

    @property
    def usable_energy_kwh(self) -> float:
        return max(0.0, self.current_energy_kwh - self.reserve_energy_kwh)


@dataclass
class ChargerCandidate:
    """
    Representation of an individual candidate charging station.
    """
    charger_id: str
    name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    node_id: Optional[str] = None  # graph node identifier if routing via graph
    charging_power_kw: float = 50.0
    connector_type: str = "CCS2"
    reliability: float = 0.90
    probability_available: float = 0.80
    charging_wait_minutes: Optional[float] = None
    estimated_charging_time_minutes: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_queue_wait_minutes(self) -> float:
        """
        Derive deterministic queue waiting time.
        If explicit wait is provided, use it; otherwise infer from probability_available.
        """
        if self.charging_wait_minutes is not None:
            return max(0.0, float(self.charging_wait_minutes))
        # Deterministic queue model: 0% available -> 30 min queue; 100% available -> 0 min queue
        inferred = (1.0 - max(0.0, min(1.0, self.probability_available))) * 30.0
        return round(inferred, 1)


@dataclass
class JointPlanResult:
    """
    Complete structured decision result returned by the Joint Engine.
    """
    decision_type: str  # 'DIRECT', 'CHARGE', or 'INFEASIBLE'
    selected_charger_id: Optional[str]
    selected_charger_name: Optional[str]

    origin: str
    destination: str

    leg_1_distance_km: float
    leg_1_energy_kwh: float

    leg_2_distance_km: float
    leg_2_energy_kwh: float

    total_distance_km: float
    total_energy_kwh: float

    arrival_energy_before_charging_kwh: float
    energy_added_at_charger_kwh: float
    post_charge_energy_kwh: float

    estimated_charging_duration_minutes: float
    estimated_charging_wait_minutes: float

    charger_reliability: Optional[float]
    charger_availability: Optional[float]

    total_route_cost: float
    cost_evaluation: Optional[Dict[str, Any]]

    feasible: bool
    reason: str

    route_paths: Dict[str, List[str]]
    algorithm_used: str

    candidate_evaluations: List[Dict[str, Any]] = field(default_factory=list)
    ai_energy_prediction: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_charging_duration(
    energy_to_add_kwh: float,
    charging_power_kw: float,
    taper_factor: float = 1.175,
) -> float:
    """
    Deterministic charging duration model including CC-CV curve tapering.
    """
    if energy_to_add_kwh <= 0.0 or charging_power_kw <= 0.0:
        return 0.0
    hours = (energy_to_add_kwh / charging_power_kw) * taper_factor
    duration_min = max(5.0, hours * 60.0)
    return round(duration_min, 1)


def evaluate_joint_route_and_charging(
    origin: str,
    destination: str,
    vehicle_state: VehicleState,
    charger_candidates: List[ChargerCandidate],
    graph: Optional[RoutingGraph] = None,
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
    distance_fn: Optional[Any] = None,
    ai_energy_predictor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> JointPlanResult:
    """
    Main entrypoint for Joint Route + Charging Decision Engine.

    Parameters:
      - origin: identifier of starting location (e.g. node_id)
      - destination: identifier of destination location
      - vehicle_state: VehicleState dataclass
      - charger_candidates: list of ChargerCandidate instances
      - graph: optional RoutingGraph instance from Change 18B
      - weights: optional CostWeights instance (Change 18A)
      - bounds: optional NormalizationBounds instance (Change 18A)
      - distance_fn: optional callable (loc1, loc2) -> float distance in km
      - ai_energy_predictor: optional callable taking trip dict and returning
        prediction dict with 'predicted_trip_energy_kwh' (Change 24)

    Returns:
      JointPlanResult with decision_type ('DIRECT', 'CHARGE', 'INFEASIBLE')
      and full journey diagnostics including AI energy prediction metadata.
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    b = bounds if bounds is not None else DEFAULT_BOUNDS

    # Helper function to compute route metrics between two locations
    def get_route_metrics(start_loc: str, end_loc: str) -> Tuple[bool, float, float, float, List[str]]:
        """Returns (success, distance_km, energy_kwh, traffic_delay_min, path)"""
        if start_loc == end_loc:
            return True, 0.0, 0.0, 0.0, [start_loc]

        if graph is not None and start_loc in graph.nodes and end_loc in graph.nodes:
            search_res = search_energy_aware_path(graph, start_loc, end_loc, weights=w, bounds=b)
            if search_res.success:
                return (
                    True,
                    search_res.total_distance_km,
                    search_res.total_energy_kwh,
                    search_res.total_traffic_delay_minutes,
                    search_res.path,
                )
            return False, float("inf"), float("inf"), 0.0, []

        if distance_fn is not None:
            dist = float(distance_fn(start_loc, end_loc))
            if dist < 0.0 or math.isinf(dist):
                return False, float("inf"), float("inf"), 0.0, []
            egy = round(dist * vehicle_state.energy_consumption_kwh_per_km, 4)
            return True, dist, egy, 0.0, [start_loc, end_loc]

        # Abstract default fallback: attempt graph nodes or return unreachable
        return False, float("inf"), float("inf"), 0.0, []

    # Helper function to query whole-trip AI energy prediction
    def get_ai_prediction(dist_km: float, duration_min: float = 0.0) -> Dict[str, Any]:
        baseline_kwh = round(dist_km * vehicle_state.energy_consumption_kwh_per_km, 3)
        if ai_energy_predictor is None or dist_km <= 0.0:
            return {
                "available": False,
                "model": "XGBoost",
                "predicted_energy_kwh": None,
                "baseline_energy_kwh": baseline_kwh,
                "delta_kwh": 0.0,
                "delta_percent": 0.0,
                "used_for_planning": False,
                "used_for_battery_state": False,
                "message": "AI predictor not configured or distance is zero; using physical baseline.",
            }
        try:
            req_data = {
                "distance_km": dist_km,
                "trip_duration_minutes": duration_min if duration_min > 0 else (dist_km / 45.0) * 60.0,
                "battery_capacity_kwh": vehicle_state.battery_capacity_kwh,
                "starting_soc_percent": vehicle_state.current_soc_percent,
            }
            pred_res = ai_energy_predictor(req_data)
            if pred_res and pred_res.get("status") == "success" and pred_res.get("predicted_trip_energy_kwh") is not None:
                pred_val = float(pred_res["predicted_trip_energy_kwh"])
                delta_kwh = round(pred_val - baseline_kwh, 3)
                delta_pct = round((delta_kwh / baseline_kwh) * 100.0, 2) if baseline_kwh > 0 else 0.0
                return {
                    "available": True,
                    "model": "XGBoost",
                    "predicted_energy_kwh": pred_val,
                    "baseline_energy_kwh": baseline_kwh,
                    "delta_kwh": delta_kwh,
                    "delta_percent": delta_pct,
                    "used_for_planning": True,
                    "used_for_battery_state": False,
                    "message": "AI energy prediction integrated into route planning ranking.",
                }
        except Exception:
            pass
        return {
            "available": False,
            "model": "XGBoost",
            "predicted_energy_kwh": None,
            "baseline_energy_kwh": baseline_kwh,
            "delta_kwh": 0.0,
            "delta_percent": 0.0,
            "used_for_planning": False,
            "used_for_battery_state": False,
            "message": "AI prediction unavailable; fallback to deterministic physical baseline.",
        }

    # Edge Case: Start equals Destination
    if origin == destination:
        cost_res = compute_route_cost(0.0, 0.0, weights=w, bounds=b)
        ai_zero = {
            "available": False,
            "model": "XGBoost",
            "predicted_energy_kwh": 0.0,
            "baseline_energy_kwh": 0.0,
            "delta_kwh": 0.0,
            "delta_percent": 0.0,
            "used_for_planning": False,
            "used_for_battery_state": False,
        }
        return JointPlanResult(
            decision_type="DIRECT",
            selected_charger_id=None,
            selected_charger_name=None,
            origin=origin,
            destination=destination,
            leg_1_distance_km=0.0,
            leg_1_energy_kwh=0.0,
            leg_2_distance_km=0.0,
            leg_2_energy_kwh=0.0,
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            arrival_energy_before_charging_kwh=vehicle_state.current_energy_kwh,
            energy_added_at_charger_kwh=0.0,
            post_charge_energy_kwh=vehicle_state.current_energy_kwh,
            estimated_charging_duration_minutes=0.0,
            estimated_charging_wait_minutes=0.0,
            charger_reliability=None,
            charger_availability=None,
            total_route_cost=cost_res.total_cost,
            cost_evaluation=cost_res.to_dict(),
            feasible=True,
            reason="Start location is identical to destination. No travel or charging required.",
            route_paths={"direct": [origin]},
            algorithm_used="joint_route_charging_engine_zero_displacement",
            ai_energy_prediction=ai_zero,
        )

    # 1. EVALUATE DIRECT ROUTE BASELINE
    direct_ok, direct_dist, direct_energy, direct_traffic, direct_path = get_route_metrics(origin, destination)
    direct_feasible = False
    direct_cost_result: Optional[RouteCostResult] = None
    direct_infeasible_reason = ""
    direct_ai_pred: Dict[str, Any] = get_ai_prediction(direct_dist if direct_ok else 0.0)

    if not direct_ok:
        direct_infeasible_reason = "No navigable path found from origin to destination."
    elif direct_energy > vehicle_state.usable_energy_kwh:
        direct_infeasible_reason = (
            f"Insufficient battery for direct route: requires {direct_energy:.2f} kWh (physical baseline), "
            f"but vehicle usable energy is {vehicle_state.usable_energy_kwh:.2f} kWh "
            f"(Current SoC: {vehicle_state.current_soc_percent:.1f}%, Reserve: {vehicle_state.reserve_battery_percent:.1f}%)."
        )
    else:
        direct_feasible = True
        # AI-informed planning cost: use AI prediction if available, else physical baseline
        direct_cost_energy = (
            direct_ai_pred["predicted_energy_kwh"]
            if direct_ai_pred["available"] and direct_ai_pred["predicted_energy_kwh"] is not None
            else direct_energy
        )
        direct_cost_result = compute_route_cost(
            distance_km=direct_dist,
            predicted_energy_kwh=direct_cost_energy,
            traffic_delay_minutes=direct_traffic,
            charging_wait_minutes=0.0,
            charging_duration_minutes=0.0,
            weights=w,
            bounds=b,
        )

    # 2. EVALUATE CHARGER CANDIDATES
    evaluated_candidates: List[Dict[str, Any]] = []

    for c in charger_candidates:
        c_node = c.node_id or c.charger_id

        # Leg 1: Origin -> Charger
        l1_ok, l1_dist, l1_energy, l1_traffic, l1_path = get_route_metrics(origin, c_node)
        if not l1_ok:
            evaluated_candidates.append({
                "charger_id": c.charger_id,
                "name": c.name,
                "feasible": False,
                "rejection_stage": "leg_1_path_not_found",
                "reason": f"No valid route found from origin to charger {c.name}.",
            })
            continue

        # Feasibility check: Can we reach the charger with safety reserve?
        # Authoritative physical baseline check
        if l1_energy > vehicle_state.usable_energy_kwh:
            evaluated_candidates.append({
                "charger_id": c.charger_id,
                "name": c.name,
                "feasible": False,
                "rejection_stage": "leg_1_insufficient_energy",
                "leg_1_energy_kwh": l1_energy,
                "usable_energy_kwh": vehicle_state.usable_energy_kwh,
                "reason": f"Charger {c.name} cannot be reached: requires {l1_energy:.2f} kWh (physical baseline), usable {vehicle_state.usable_energy_kwh:.2f} kWh.",
            })
            continue

        # Arrival energy and SoC at charger
        arrival_energy = max(vehicle_state.reserve_energy_kwh, vehicle_state.current_energy_kwh - l1_energy)

        # Leg 2: Charger -> Destination
        l2_ok, l2_dist, l2_energy, l2_traffic, l2_path = get_route_metrics(c_node, destination)
        if not l2_ok:
            evaluated_candidates.append({
                "charger_id": c.charger_id,
                "name": c.name,
                "feasible": False,
                "rejection_stage": "leg_2_path_not_found",
                "reason": f"No valid route found from charger {c.name} to destination.",
            })
            continue

        # Maximum energy that can physically be in the battery after charging
        max_possible_battery = vehicle_state.battery_capacity_kwh
        usable_after_full_charge = max_possible_battery - vehicle_state.reserve_energy_kwh

        # Feasibility check: Can the vehicle reach destination even after a 100% full charge?
        # Authoritative physical baseline check
        if l2_energy > usable_after_full_charge:
            evaluated_candidates.append({
                "charger_id": c.charger_id,
                "name": c.name,
                "feasible": False,
                "rejection_stage": "leg_2_exceeds_battery_capacity",
                "leg_2_energy_kwh": l2_energy,
                "max_usable_battery_kwh": usable_after_full_charge,
                "reason": (
                    f"Destination is unreachable from charger {c.name} within battery capacity: "
                    f"requires {l2_energy:.2f} kWh (physical baseline), maximum usable full battery is {usable_after_full_charge:.2f} kWh."
                ),
            })
            continue

        # Calculate energy deficit and target charge based on authoritative physical baseline
        departure_energy_needed = l2_energy + vehicle_state.reserve_energy_kwh
        energy_deficit = max(0.0, departure_energy_needed - arrival_energy)

        # Target post-charge energy
        target_soc_energy = (vehicle_state.battery_capacity_kwh * vehicle_state.target_soc_percent) / 100.0
        desired_post_charge = max(departure_energy_needed, target_soc_energy)
        desired_post_charge = min(desired_post_charge, vehicle_state.battery_capacity_kwh)

        energy_to_add = max(energy_deficit, desired_post_charge - arrival_energy)
        energy_to_add = min(energy_to_add, vehicle_state.battery_capacity_kwh - arrival_energy)
        energy_to_add = round(max(0.0, energy_to_add), 3)

        post_charge_energy = arrival_energy + energy_to_add

        # Charging duration
        if c.estimated_charging_time_minutes is not None:
            chg_duration_min = float(c.estimated_charging_time_minutes)
        else:
            chg_duration_min = estimate_charging_duration(energy_to_add, c.charging_power_kw)

        # Queue wait time
        queue_wait_min = c.get_queue_wait_minutes()

        # Joint Metrics for complete journey
        tot_dist = round(l1_dist + l2_dist, 3)
        tot_energy = round(l1_energy + l2_energy, 3)
        tot_traffic = round(l1_traffic + l2_traffic, 1)

        # Whole-trip AI energy prediction for this candidate journey
        cand_ai_pred = get_ai_prediction(tot_dist)

        # AI-informed planning cost: use AI prediction if available, else physical baseline
        cand_cost_energy = (
            cand_ai_pred["predicted_energy_kwh"]
            if cand_ai_pred["available"] and cand_ai_pred["predicted_energy_kwh"] is not None
            else tot_energy
        )

        joint_cost_res = compute_route_cost(
            distance_km=tot_dist,
            predicted_energy_kwh=cand_cost_energy,
            traffic_delay_minutes=tot_traffic,
            charging_wait_minutes=queue_wait_min,
            charging_duration_minutes=chg_duration_min,
            weights=w,
            bounds=b,
        )

        cand_eval = {
            "charger_id": c.charger_id,
            "name": c.name,
            "feasible": True,
            "leg_1_distance_km": l1_dist,
            "leg_1_energy_kwh": l1_energy,
            "leg_1_path": l1_path,
            "arrival_energy_kwh": round(arrival_energy, 3),
            "leg_2_distance_km": l2_dist,
            "leg_2_energy_kwh": l2_energy,
            "leg_2_path": l2_path,
            "energy_deficit_kwh": round(energy_deficit, 3),
            "energy_to_add_kwh": energy_to_add,
            "post_charge_energy_kwh": round(post_charge_energy, 3),
            "charging_power_kw": c.charging_power_kw,
            "estimated_charging_duration_minutes": chg_duration_min,
            "estimated_charging_wait_minutes": queue_wait_min,
            "reliability": c.reliability,
            "probability_available": c.probability_available,
            "total_distance_km": tot_dist,
            "total_energy_kwh": tot_energy,
            "total_route_cost": joint_cost_res.total_cost,
            "cost_evaluation": joint_cost_res.to_dict(),
            "ai_energy_prediction": cand_ai_pred,
            "baseline_energy_kwh": cand_ai_pred["baseline_energy_kwh"],
            "ai_predicted_energy_kwh": cand_ai_pred["predicted_energy_kwh"],
            "ai_energy_delta_kwh": cand_ai_pred["delta_kwh"],
            "ai_energy_delta_percent": cand_ai_pred["delta_percent"],
        }
        evaluated_candidates.append(cand_eval)

    # Filter only feasible candidates and sort by total_route_cost ascending
    feasible_candidates = [cand for cand in evaluated_candidates if cand.get("feasible")]
    feasible_candidates.sort(key=lambda x: x["total_route_cost"])

    # 3. DECISION RULE LOGIC
    # Case A: Direct Route is Feasible
    if direct_feasible:
        # Check if the best charging plan somehow produces a lower cost (e.g. extreme scenario)
        # By default, avoiding a charging stop saves charging wait and duration penalties
        best_cand = feasible_candidates[0] if feasible_candidates else None

        if best_cand and best_cand["total_route_cost"] < direct_cost_result.total_cost:
            # Charging route is somehow superior (e.g., direct route has massive traffic delay)
            return JointPlanResult(
                decision_type="CHARGE",
                selected_charger_id=best_cand["charger_id"],
                selected_charger_name=best_cand["name"],
                origin=origin,
                destination=destination,
                leg_1_distance_km=best_cand["leg_1_distance_km"],
                leg_1_energy_kwh=best_cand["leg_1_energy_kwh"],
                leg_2_distance_km=best_cand["leg_2_distance_km"],
                leg_2_energy_kwh=best_cand["leg_2_energy_kwh"],
                total_distance_km=best_cand["total_distance_km"],
                total_energy_kwh=best_cand["total_energy_kwh"],
                arrival_energy_before_charging_kwh=best_cand["arrival_energy_kwh"],
                energy_added_at_charger_kwh=best_cand["energy_to_add_kwh"],
                post_charge_energy_kwh=best_cand["post_charge_energy_kwh"],
                estimated_charging_duration_minutes=best_cand["estimated_charging_duration_minutes"],
                estimated_charging_wait_minutes=best_cand["estimated_charging_wait_minutes"],
                charger_reliability=best_cand["reliability"],
                charger_availability=best_cand["probability_available"],
                total_route_cost=best_cand["total_route_cost"],
                cost_evaluation=best_cand["cost_evaluation"],
                feasible=True,
                reason=f"Direct route was feasible, but charging stop at {best_cand['name']} provides lower overall journey cost.",
                route_paths={"leg_1": best_cand["leg_1_path"], "leg_2": best_cand["leg_2_path"]},
                algorithm_used="joint_route_charging_engine_modified_astar",
                candidate_evaluations=evaluated_candidates,
                ai_energy_prediction=best_cand.get("ai_energy_prediction"),
            )

        # Direct route is feasible and preferred
        return JointPlanResult(
            decision_type="DIRECT",
            selected_charger_id=None,
            selected_charger_name=None,
            origin=origin,
            destination=destination,
            leg_1_distance_km=direct_dist,
            leg_1_energy_kwh=direct_energy,
            leg_2_distance_km=0.0,
            leg_2_energy_kwh=0.0,
            total_distance_km=direct_dist,
            total_energy_kwh=direct_energy,
            arrival_energy_before_charging_kwh=vehicle_state.current_energy_kwh - direct_energy,
            energy_added_at_charger_kwh=0.0,
            post_charge_energy_kwh=vehicle_state.current_energy_kwh - direct_energy,
            estimated_charging_duration_minutes=0.0,
            estimated_charging_wait_minutes=0.0,
            charger_reliability=None,
            charger_availability=None,
            total_route_cost=direct_cost_result.total_cost,
            cost_evaluation=direct_cost_result.to_dict(),
            feasible=True,
            reason="Destination is reachable directly within current battery usable energy. No charging stop required.",
            route_paths={"direct": direct_path},
            algorithm_used="joint_route_charging_engine_direct_baseline",
            candidate_evaluations=evaluated_candidates,
            ai_energy_prediction=direct_ai_pred,
        )

    # Case B: Direct Route is INFEASIBLE -> Charging Required
    if feasible_candidates:
        best_cand = feasible_candidates[0]
        return JointPlanResult(
            decision_type="CHARGE",
            selected_charger_id=best_cand["charger_id"],
            selected_charger_name=best_cand["name"],
            origin=origin,
            destination=destination,
            leg_1_distance_km=best_cand["leg_1_distance_km"],
            leg_1_energy_kwh=best_cand["leg_1_energy_kwh"],
            leg_2_distance_km=best_cand["leg_2_distance_km"],
            leg_2_energy_kwh=best_cand["leg_2_energy_kwh"],
            total_distance_km=best_cand["total_distance_km"],
            total_energy_kwh=best_cand["total_energy_kwh"],
            arrival_energy_before_charging_kwh=best_cand["arrival_energy_kwh"],
            energy_added_at_charger_kwh=best_cand["energy_to_add_kwh"],
            post_charge_energy_kwh=best_cand["post_charge_energy_kwh"],
            estimated_charging_duration_minutes=best_cand["estimated_charging_duration_minutes"],
            estimated_charging_wait_minutes=best_cand["estimated_charging_wait_minutes"],
            charger_reliability=best_cand["reliability"],
            charger_availability=best_cand["probability_available"],
            total_route_cost=best_cand["total_route_cost"],
            cost_evaluation=best_cand["cost_evaluation"],
            feasible=True,
            reason=(
                f"Direct route infeasible ({direct_infeasible_reason}). "
                f"Recommended charging stop at {best_cand['name']} optimizes total journey cost."
            ),
            route_paths={"leg_1": best_cand["leg_1_path"], "leg_2": best_cand["leg_2_path"]},
            algorithm_used="joint_route_charging_engine_modified_astar",
            candidate_evaluations=evaluated_candidates,
            ai_energy_prediction=best_cand.get("ai_energy_prediction"),
        )

    # Case C: Neither Direct nor any Charging Candidate is Feasible
    failure_details = [
        f"{c.get('name', 'Charger')}: {c.get('reason', 'Infeasible')}"
        for c in evaluated_candidates
    ]
    summary_reason = (
        f"Trip infeasible. Direct route impossible: {direct_infeasible_reason}. "
        f"All {len(charger_candidates)} candidate chargers were infeasible or unreachable: "
        + "; ".join(failure_details[:3])
    )

    return JointPlanResult(
        decision_type="INFEASIBLE",
        selected_charger_id=None,
        selected_charger_name=None,
        origin=origin,
        destination=destination,
        leg_1_distance_km=0.0,
        leg_1_energy_kwh=0.0,
        leg_2_distance_km=0.0,
        leg_2_energy_kwh=0.0,
        total_distance_km=0.0,
        total_energy_kwh=0.0,
        arrival_energy_before_charging_kwh=0.0,
        energy_added_at_charger_kwh=0.0,
        post_charge_energy_kwh=0.0,
        estimated_charging_duration_minutes=0.0,
        estimated_charging_wait_minutes=0.0,
        charger_reliability=None,
        charger_availability=None,
        total_route_cost=1.0,
        cost_evaluation=None,
        feasible=False,
        reason=summary_reason,
        route_paths={},
        algorithm_used="joint_route_charging_engine_infeasible",
        candidate_evaluations=evaluated_candidates,
        ai_energy_prediction=direct_ai_pred if direct_ok else None,
    )

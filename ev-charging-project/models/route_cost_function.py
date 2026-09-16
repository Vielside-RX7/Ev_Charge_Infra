"""
Energy-Aware Multi-Criteria Route Cost Function
================================================
Task 18A: Reusable mathematical cost function for evaluating and comparing
EV route and charging alternatives across heterogeneous physical units.

Objective Formula:
------------------
C = w_d * D_norm + w_e * E_norm + w_t * T_norm + w_w * W_norm + w_c * C_norm

where:
  - D_norm : Normalized driving road distance [0, 1]
  - E_norm : Normalized predicted energy consumption [0, 1]
  - T_norm : Normalized traffic congestion delay [0, 1]
  - W_norm : Normalized charging station queue waiting time [0, 1]
  - C_norm : Normalized charging duration [0, 1]
  - w_i    : Factor weights, with sum(w_i) == 1.0 and w_i >= 0.0
  - C      : Resulting total normalized route cost [0, 1]

Important Research Design Note:
--------------------------------
The default weights defined herein are initial engineering defaults established
for structural evaluation and system integration. They are NOT claimed to be
statistically or scientifically optimal. They provide a sound baseline for subsequent
sensitivity analysis, multi-objective Pareto optimization, and ablation experiments.
"""

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CostWeights:
    """
    Configurable weights for the multi-criteria route cost function.

    Constraint:
      All weights must be non-negative and sum to 1.0 within a numerical tolerance of 1e-6.
    """
    distance: float = 0.20
    energy: float = 0.30
    traffic_delay: float = 0.15
    charging_wait: float = 0.20
    charging_duration: float = 0.15

    def __post_init__(self):
        for name, val in [
            ("distance", self.distance),
            ("energy", self.energy),
            ("traffic_delay", self.traffic_delay),
            ("charging_wait", self.charging_wait),
            ("charging_duration", self.charging_duration),
        ]:
            if val < 0.0:
                raise ValueError(f"Weight '{name}' cannot be negative: {val}")

        total = (
            self.distance
            + self.energy
            + self.traffic_delay
            + self.charging_wait
            + self.charging_duration
        )
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"Cost weights must sum to 1.0 (got {total:.6f}). "
                f"Values: distance={self.distance}, energy={self.energy}, "
                f"traffic_delay={self.traffic_delay}, charging_wait={self.charging_wait}, "
                f"charging_duration={self.charging_duration}"
            )

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


# Default initial engineering weights
DEFAULT_WEIGHTS = CostWeights(
    distance=0.20,
    energy=0.30,
    traffic_delay=0.15,
    charging_wait=0.20,
    charging_duration=0.15,
)


@dataclass(frozen=True)
class NormalizationBounds:
    """
    Global reference bounds for each dimension.

    Using stable reference bounds prevents candidate-set instability
    (where adding or removing an inferior alternative shifts the relative
    costs of all other candidates).

    Defaults are chosen based on typical urban/suburban EV trip profiles:
      - distance: [0, 100] km
      - energy: [0, 20] kWh (approx 0-100km consumption for 30kWh EV)
      - traffic_delay: [0, 60] minutes
      - charging_wait: [0, 60] minutes
      - charging_duration: [0, 90] minutes
    """
    distance_km: Tuple[float, float] = (0.0, 100.0)
    energy_kwh: Tuple[float, float] = (0.0, 20.0)
    traffic_delay_minutes: Tuple[float, float] = (0.0, 60.0)
    charging_wait_minutes: Tuple[float, float] = (0.0, 60.0)
    charging_duration_minutes: Tuple[float, float] = (0.0, 90.0)

    def to_dict(self) -> Dict[str, Tuple[float, float]]:
        return asdict(self)


DEFAULT_BOUNDS = NormalizationBounds()


def normalize_value(value: float, min_bound: float, max_bound: float) -> float:
    """
    Normalize a scalar value to [0.0, 1.0] using min-max scaling with strict clamping.

    Clamping Behavior:
      - If value <= min_bound, returns 0.0.
      - If value >= max_bound, returns 1.0.
      - If min_bound == max_bound (zero-range), returns 0.0 to prevent division by zero.
      - If max_bound < min_bound, raises ValueError.
      - If value is NaN, raises ValueError.

    Returns:
      float strictly in range [0.0, 1.0].
    """
    if math.isnan(value):
        raise ValueError("Cannot normalize NaN value.")
    if max_bound < min_bound:
        raise ValueError(
            f"Invalid bounds: max_bound ({max_bound}) cannot be less than min_bound ({min_bound})"
        )
    if math.isclose(min_bound, max_bound, abs_tol=1e-9):
        # Zero-range edge case: no distinction can be made, default to 0.0
        return 0.0

    raw_norm = (value - min_bound) / (max_bound - min_bound)
    return max(0.0, min(1.0, float(raw_norm)))


@dataclass
class RouteCostResult:
    """
    Complete output decomposition of a computed route cost.
    """
    total_cost: float
    distance_component: float
    energy_component: float
    traffic_component: float
    charging_wait_component: float
    charging_duration_component: float
    normalized_factors: Dict[str, float]
    raw_inputs: Dict[str, float]
    weights: Dict[str, float]
    bounds: Dict[str, Tuple[float, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_cost": self.total_cost,
            "components": {
                "distance": self.distance_component,
                "energy": self.energy_component,
                "traffic_delay": self.traffic_component,
                "charging_wait": self.charging_wait_component,
                "charging_duration": self.charging_duration_component,
            },
            "normalized_factors": self.normalized_factors,
            "raw_inputs": self.raw_inputs,
            "weights": self.weights,
            "bounds": {k: list(v) for k, v in self.bounds.items()},
        }


def compute_route_cost(
    distance_km: float,
    predicted_energy_kwh: float,
    traffic_delay_minutes: float = 0.0,
    charging_wait_minutes: float = 0.0,
    charging_duration_minutes: float = 0.0,
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
) -> RouteCostResult:
    """
    Compute the multi-criteria energy-aware route cost.

    Parameters:
      - distance_km: Driving road distance in km (>= 0.0)
      - predicted_energy_kwh: Predicted energy consumption in kWh (>= 0.0)
      - traffic_delay_minutes: Estimated delay due to congestion in minutes (>= 0.0)
      - charging_wait_minutes: Expected waiting/queue time at charger in minutes (>= 0.0)
      - charging_duration_minutes: Duration required to recharge in minutes (>= 0.0)
      - weights: CostWeights instance (defaults to DEFAULT_WEIGHTS)
      - bounds: NormalizationBounds instance (defaults to DEFAULT_BOUNDS)

    Returns:
      RouteCostResult with total_cost in [0, 1] and full component breakdown.
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    b = bounds if bounds is not None else DEFAULT_BOUNDS

    # Input validation
    for name, val in [
        ("distance_km", distance_km),
        ("predicted_energy_kwh", predicted_energy_kwh),
        ("traffic_delay_minutes", traffic_delay_minutes),
        ("charging_wait_minutes", charging_wait_minutes),
        ("charging_duration_minutes", charging_duration_minutes),
    ]:
        if val < 0.0:
            raise ValueError(f"Route cost input '{name}' cannot be negative (got {val}).")

    # 1. Normalize each factor into [0.0, 1.0]
    d_norm = normalize_value(distance_km, b.distance_km[0], b.distance_km[1])
    e_norm = normalize_value(predicted_energy_kwh, b.energy_kwh[0], b.energy_kwh[1])
    t_norm = normalize_value(traffic_delay_minutes, b.traffic_delay_minutes[0], b.traffic_delay_minutes[1])
    w_norm = normalize_value(charging_wait_minutes, b.charging_wait_minutes[0], b.charging_wait_minutes[1])
    c_norm = normalize_value(charging_duration_minutes, b.charging_duration_minutes[0], b.charging_duration_minutes[1])

    # 2. Weighted component contributions
    comp_dist = w.distance * d_norm
    comp_energy = w.energy * e_norm
    comp_traffic = w.traffic_delay * t_norm
    comp_wait = w.charging_wait * w_norm
    comp_duration = w.charging_duration * c_norm

    # 3. Total cost summation
    total = comp_dist + comp_energy + comp_traffic + comp_wait + comp_duration

    # Guarantee floating-point precision bounds [0.0, 1.0]
    total_cost = round(max(0.0, min(1.0, total)), 6)

    return RouteCostResult(
        total_cost=total_cost,
        distance_component=round(comp_dist, 6),
        energy_component=round(comp_energy, 6),
        traffic_component=round(comp_traffic, 6),
        charging_wait_component=round(comp_wait, 6),
        charging_duration_component=round(comp_duration, 6),
        normalized_factors={
            "distance_norm": round(d_norm, 6),
            "energy_norm": round(e_norm, 6),
            "traffic_norm": round(t_norm, 6),
            "charging_wait_norm": round(w_norm, 6),
            "charging_duration_norm": round(c_norm, 6),
        },
        raw_inputs={
            "distance_km": float(distance_km),
            "predicted_energy_kwh": float(predicted_energy_kwh),
            "traffic_delay_minutes": float(traffic_delay_minutes),
            "charging_wait_minutes": float(charging_wait_minutes),
            "charging_duration_minutes": float(charging_duration_minutes),
        },
        weights=w.to_dict(),
        bounds=b.to_dict(),
    )


def compare_routes(
    routes: List[Dict[str, Any]],
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
) -> List[Dict[str, Any]]:
    """
    Score and rank multiple route alternatives from lowest cost (best) to highest cost.

    Each dict in `routes` should have:
      - 'id': str or int
      - 'distance_km': float
      - 'predicted_energy_kwh': float
      - optional 'traffic_delay_minutes': float
      - optional 'charging_wait_minutes': float
      - optional 'charging_duration_minutes': float

    Returns:
      Ranked list of route dicts, with 'cost_evaluation' appended, ordered by total_cost ascending.
    """
    evaluated = []
    for r in routes:
        cost_res = compute_route_cost(
            distance_km=float(r.get("distance_km", 0.0)),
            predicted_energy_kwh=float(r.get("predicted_energy_kwh", 0.0)),
            traffic_delay_minutes=float(r.get("traffic_delay_minutes", 0.0)),
            charging_wait_minutes=float(r.get("charging_wait_minutes", 0.0)),
            charging_duration_minutes=float(r.get("charging_duration_minutes", 0.0)),
            weights=weights,
            bounds=bounds,
        )
        entry = dict(r)
        entry["cost_evaluation"] = cost_res.to_dict()
        entry["total_cost"] = cost_res.total_cost
        evaluated.append(entry)

    evaluated.sort(key=lambda x: x["total_cost"])
    return evaluated


def perform_sensitivity_analysis(
    base_route: Dict[str, float],
    weight_variations: List[Dict[str, float]],
    bounds: Optional[NormalizationBounds] = None,
) -> List[Dict[str, Any]]:
    """
    Helper for future research experiments to test how route evaluation varies
    across different weighting hypotheses (e.g. eco-centric vs time-centric).
    """
    results = []
    for i, w_dict in enumerate(weight_variations):
        weights = CostWeights(
            distance=w_dict["distance"],
            energy=w_dict["energy"],
            traffic_delay=w_dict["traffic_delay"],
            charging_wait=w_dict["charging_wait"],
            charging_duration=w_dict["charging_duration"],
        )
        cost_res = compute_route_cost(
            distance_km=base_route.get("distance_km", 0.0),
            predicted_energy_kwh=base_route.get("predicted_energy_kwh", 0.0),
            traffic_delay_minutes=base_route.get("traffic_delay_minutes", 0.0),
            charging_wait_minutes=base_route.get("charging_wait_minutes", 0.0),
            charging_duration_minutes=base_route.get("charging_duration_minutes", 0.0),
            weights=weights,
            bounds=bounds,
        )
        results.append({
            "experiment_index": i,
            "weights": weights.to_dict(),
            "total_cost": cost_res.total_cost,
            "components": cost_res.to_dict()["components"],
        })
    return results

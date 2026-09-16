"""
Energy-Aware Modified A* Route Search Algorithm
================================================
Task 18B: Multi-criteria path search integrating the Change 18A mathematical
energy-aware route cost function.

Research Intent:
----------------
Conventional navigation systems optimize solely for the shortest geographical
distance or fastest travel time. For Electric Vehicles, path selection must
balance:
  1. Road distance (battery range constraint)
  2. Predicted energy consumption (topography, speed, regenerative braking)
  3. Traffic congestion delay (stop-and-go energy dissipation)
  4. Charging station queue waiting time
  5. Charging duration (charger kW rating)

This module provides:
  - Baseline Shortest-Distance A* (pure distance minimization)
  - Modified Energy-Aware A* (Change 18A multi-criteria cost minimization)

Cost & Heuristic Formulation:
------------------------------
Let the reference bounds from Change 18A be (D_max, E_max, T_max, W_max, C_max)
with all lower bounds = 0.0.
The multi-criteria cost function C is strictly linear and additive across edges:

  c(e) = (w_d / D_max) * D(e)
       + (w_e / E_max) * E(e)
       + (w_t / T_max) * T(e)
       + (w_w / W_max) * W(e)
       + (w_c / C_max) * C(e)

where:
  - D(e) : edge distance in km
  - E(e) : predicted edge energy in kWh (defaults to D(e) * 0.150 kWh/km baseline)
  - T(e) : traffic delay in minutes
  - W(e) : charging wait time in minutes
  - C(e) : charging duration in minutes

Heuristic Admissibility:
------------------------
For any node n and goal node g, let d_straight(n, g) be the straight-line (Euclidean)
distance lower bound to the destination:

  h(n) = (w_d / D_max) * d_straight(n, g)

Proof of Admissibility (h(n) <= h*(n)):
  - Since physical road distance D(P) >= d_straight(n, g) for any path P from n to g,
    and since all other cost components (energy, traffic, wait, duration) and weights
    are non-negative (>= 0), the actual remaining cost h*(n) satisfies:
      h*(n) >= (w_d / D_max) * D(P) >= (w_d / D_max) * d_straight(n, g) = h(n).
  - Hence, h(n) never overestimates the true remaining cost, preserving A* optimality.

Proof of Consistency (Monotonicity):
  - By triangle inequality: d_straight(u, g) <= d_straight(u, v) + d_straight(v, g)
                           <= D(u, v) + d_straight(v, g).
  - Therefore: h(u) <= (w_d / D_max) * D(u, v) + h(v) <= c(u, v) + h(v).
"""

from dataclasses import asdict, dataclass, field
import heapq
import math
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# Import Change 18A mathematical models
from route_cost_function import (
    CostWeights,
    NormalizationBounds,
    RouteCostResult,
    compute_route_cost,
    DEFAULT_WEIGHTS,
    DEFAULT_BOUNDS,
)

# Baseline vehicle consumption rate (0.150 kWh/km)
DEFAULT_ENERGY_RATE_KWH_PER_KM = 0.150


@dataclass
class GraphNode:
    """
    Representation of a node in the routing network.
    Coordinates (x, y) are in kilometers or arbitrary Cartesian units for heuristic estimation.
    """
    node_id: str
    x_km: Optional[float] = None
    y_km: Optional[float] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """
    Directed edge between two nodes containing multi-criteria EV routing attributes.
    """
    target_node_id: str
    distance_km: float
    predicted_energy_kwh: Optional[float] = None
    traffic_delay_minutes: float = 0.0
    charging_wait_minutes: float = 0.0
    charging_duration_minutes: float = 0.0
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.distance_km < 0.0:
            raise ValueError(f"Edge distance cannot be negative (got {self.distance_km})")
        if self.predicted_energy_kwh is None:
            # Authoritative deterministic baseline: 0.150 kWh/km
            self.predicted_energy_kwh = round(self.distance_km * DEFAULT_ENERGY_RATE_KWH_PER_KM, 4)
        elif self.predicted_energy_kwh < 0.0:
            raise ValueError(f"Predicted energy cannot be negative (got {self.predicted_energy_kwh})")
        if self.traffic_delay_minutes < 0.0:
            raise ValueError("Traffic delay cannot be negative")
        if self.charging_wait_minutes < 0.0:
            raise ValueError("Charging wait cannot be negative")
        if self.charging_duration_minutes < 0.0:
            raise ValueError("Charging duration cannot be negative")


class RoutingGraph:
    """
    Directed network graph for multi-criteria EV path search.
    """

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.adjacency: Dict[str, List[GraphEdge]] = {}

    def add_node(
        self,
        node_id: str,
        x_km: Optional[float] = None,
        y_km: Optional[float] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GraphNode:
        node = GraphNode(
            node_id=str(node_id),
            x_km=x_km,
            y_km=y_km,
            name=name,
            metadata=metadata or {},
        )
        self.nodes[node.node_id] = node
        if node.node_id not in self.adjacency:
            self.adjacency[node.node_id] = []
        return node

    def add_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        distance_km: float,
        predicted_energy_kwh: Optional[float] = None,
        traffic_delay_minutes: float = 0.0,
        charging_wait_minutes: float = 0.0,
        charging_duration_minutes: float = 0.0,
        name: Optional[str] = None,
        bidirectional: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GraphEdge:
        if from_node_id not in self.nodes:
            self.add_node(from_node_id)
        if to_node_id not in self.nodes:
            self.add_node(to_node_id)

        edge = GraphEdge(
            target_node_id=str(to_node_id),
            distance_km=float(distance_km),
            predicted_energy_kwh=predicted_energy_kwh,
            traffic_delay_minutes=float(traffic_delay_minutes),
            charging_wait_minutes=float(charging_wait_minutes),
            charging_duration_minutes=float(charging_duration_minutes),
            name=name,
            metadata=metadata or {},
        )
        self.adjacency[str(from_node_id)].append(edge)

        if bidirectional:
            reverse_edge = GraphEdge(
                target_node_id=str(from_node_id),
                distance_km=float(distance_km),
                predicted_energy_kwh=predicted_energy_kwh,
                traffic_delay_minutes=float(traffic_delay_minutes),
                charging_wait_minutes=float(charging_wait_minutes),
                charging_duration_minutes=float(charging_duration_minutes),
                name=name,
                metadata=metadata or {},
            )
            self.adjacency[str(to_node_id)].append(reverse_edge)

        return edge

    def get_neighbors(self, node_id: str) -> List[GraphEdge]:
        return self.adjacency.get(str(node_id), [])

    def straight_line_distance_km(self, from_node_id: str, to_node_id: str) -> float:
        """
        Calculates straight-line Euclidean distance between nodes in km if coordinates exist.
        Returns 0.0 if either node lacks coordinates (admissible fallback).
        """
        n1 = self.nodes.get(str(from_node_id))
        n2 = self.nodes.get(str(to_node_id))
        if not n1 or not n2:
            return 0.0
        if n1.x_km is None or n1.y_km is None or n2.x_km is None or n2.y_km is None:
            return 0.0
        return math.hypot(n1.x_km - n2.x_km, n1.y_km - n2.y_km)


@dataclass
class SearchResult:
    """
    Comprehensive structured output from an A* route search.
    """
    success: bool
    algorithm: str
    path: List[str]
    total_distance_km: float
    total_energy_kwh: float
    total_traffic_delay_minutes: float
    total_charging_wait_minutes: float
    total_charging_duration_minutes: float
    final_cost: float
    cost_evaluation: Optional[Dict[str, Any]]
    nodes_expanded: int
    execution_time_ms: float
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        return res


def compute_edge_energy_aware_cost(
    edge: GraphEdge,
    weights: CostWeights,
    bounds: NormalizationBounds,
) -> float:
    """
    Computes the additive unitless edge cost c(e) according to Change 18A formula.
    """
    d_max = bounds.distance_km[1]
    e_max = bounds.energy_kwh[1]
    t_max = bounds.traffic_delay_minutes[1]
    w_max = bounds.charging_wait_minutes[1]
    c_max = bounds.charging_duration_minutes[1]

    cost = (
        (weights.distance * edge.distance_km / d_max)
        + (weights.energy * (edge.predicted_energy_kwh or 0.0) / e_max)
        + (weights.traffic_delay * edge.traffic_delay_minutes / t_max)
        + (weights.charging_wait * edge.charging_wait_minutes / w_max)
        + (weights.charging_duration * edge.charging_duration_minutes / c_max)
    )
    return cost


def search_shortest_path(
    graph: RoutingGraph,
    start_node_id: str,
    goal_node_id: str,
) -> SearchResult:
    """
    Baseline shortest-distance A* search.
    Minimizes pure road distance (km).
    """
    t_start = time.perf_counter()

    start_id = str(start_node_id)
    goal_id = str(goal_node_id)

    if start_id not in graph.nodes:
        return SearchResult(
            success=False,
            algorithm="baseline_shortest_distance_astar",
            path=[],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=None,
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message=f"Start node '{start_id}' does not exist in graph.",
        )

    if goal_id not in graph.nodes:
        return SearchResult(
            success=False,
            algorithm="baseline_shortest_distance_astar",
            path=[],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=None,
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message=f"Goal node '{goal_id}' does not exist in graph.",
        )

    # Edge case: start == goal
    if start_id == goal_id:
        return SearchResult(
            success=True,
            algorithm="baseline_shortest_distance_astar",
            path=[start_id],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=compute_route_cost(0.0, 0.0).to_dict(),
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message="Start node is identical to goal node.",
        )

    # Priority queue: (f_score, tie_breaker_counter, current_node_id)
    counter = 0
    open_heap: List[Tuple[float, int, str]] = []

    # g_score maps node_id to lowest distance (km) from start
    g_score: Dict[str, float] = {start_id: 0.0}

    # Predecessors for path and attribute reconstruction
    # maps current_node -> (prev_node, edge_used)
    came_from: Dict[str, Tuple[str, GraphEdge]] = {}

    h_start = graph.straight_line_distance_km(start_id, goal_id)
    heapq.heappush(open_heap, (h_start, counter, start_id))

    closed_set: Set[str] = set()
    nodes_expanded = 0

    while open_heap:
        f_curr, _, curr_id = heapq.heappop(open_heap)

        if curr_id in closed_set:
            continue
        closed_set.add(curr_id)
        nodes_expanded += 1

        if curr_id == goal_id:
            # Goal reached; reconstruct path
            return _reconstruct_path(
                came_from=came_from,
                goal_id=goal_id,
                start_id=start_id,
                algorithm="baseline_shortest_distance_astar",
                nodes_expanded=nodes_expanded,
                start_time=t_start,
            )

        curr_g = g_score[curr_id]

        for edge in graph.get_neighbors(curr_id):
            neighbor_id = edge.target_node_id
            tentative_g = curr_g + edge.distance_km

            if tentative_g < g_score.get(neighbor_id, float("inf")):
                g_score[neighbor_id] = tentative_g
                came_from[neighbor_id] = (curr_id, edge)
                h_val = graph.straight_line_distance_km(neighbor_id, goal_id)
                f_val = tentative_g + h_val
                counter += 1
                heapq.heappush(open_heap, (f_val, counter, neighbor_id))

    # Goal unreachable
    return SearchResult(
        success=False,
        algorithm="baseline_shortest_distance_astar",
        path=[],
        total_distance_km=0.0,
        total_energy_kwh=0.0,
        total_traffic_delay_minutes=0.0,
        total_charging_wait_minutes=0.0,
        total_charging_duration_minutes=0.0,
        final_cost=float("inf"),
        cost_evaluation=None,
        nodes_expanded=nodes_expanded,
        execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
        message="No valid path exists between start and goal nodes.",
    )


def search_energy_aware_path(
    graph: RoutingGraph,
    start_node_id: str,
    goal_node_id: str,
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
) -> SearchResult:
    """
    Modified Energy-Aware A* route search.
    Minimizes the Change 18A multi-criteria normalized cost function C.
    """
    t_start = time.perf_counter()

    w = weights if weights is not None else DEFAULT_WEIGHTS
    b = bounds if bounds is not None else DEFAULT_BOUNDS

    start_id = str(start_node_id)
    goal_id = str(goal_node_id)

    if start_id not in graph.nodes:
        return SearchResult(
            success=False,
            algorithm="modified_energy_aware_astar",
            path=[],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=None,
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message=f"Start node '{start_id}' does not exist in graph.",
        )

    if goal_id not in graph.nodes:
        return SearchResult(
            success=False,
            algorithm="modified_energy_aware_astar",
            path=[],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=None,
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message=f"Goal node '{goal_id}' does not exist in graph.",
        )

    # Edge case: start == goal
    if start_id == goal_id:
        eval_dict = compute_route_cost(0.0, 0.0, weights=w, bounds=b).to_dict()
        return SearchResult(
            success=True,
            algorithm="modified_energy_aware_astar",
            path=[start_id],
            total_distance_km=0.0,
            total_energy_kwh=0.0,
            total_traffic_delay_minutes=0.0,
            total_charging_wait_minutes=0.0,
            total_charging_duration_minutes=0.0,
            final_cost=0.0,
            cost_evaluation=eval_dict,
            nodes_expanded=0,
            execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
            message="Start node is identical to goal node.",
        )

    def heuristic(node_id: str) -> float:
        """
        Admissible & consistent heuristic:
          h(n) = (w_d / D_max) * d_straight(n, goal)
        """
        d_straight = graph.straight_line_distance_km(node_id, goal_id)
        return (w.distance / b.distance_km[1]) * d_straight

    counter = 0
    open_heap: List[Tuple[float, int, str]] = []

    # g_score maps node_id to lowest accumulated cost from start
    g_score: Dict[str, float] = {start_id: 0.0}

    # Predecessors for path reconstruction
    came_from: Dict[str, Tuple[str, GraphEdge]] = {}

    h_start = heuristic(start_id)
    heapq.heappush(open_heap, (h_start, counter, start_id))

    closed_set: Set[str] = set()
    nodes_expanded = 0

    while open_heap:
        f_curr, _, curr_id = heapq.heappop(open_heap)

        if curr_id in closed_set:
            continue
        closed_set.add(curr_id)
        nodes_expanded += 1

        if curr_id == goal_id:
            return _reconstruct_path(
                came_from=came_from,
                goal_id=goal_id,
                start_id=start_id,
                algorithm="modified_energy_aware_astar",
                nodes_expanded=nodes_expanded,
                start_time=t_start,
                weights=w,
                bounds=b,
            )

        curr_g = g_score[curr_id]

        for edge in graph.get_neighbors(curr_id):
            neighbor_id = edge.target_node_id
            edge_cost = compute_edge_energy_aware_cost(edge, w, b)
            tentative_g = curr_g + edge_cost

            if tentative_g < g_score.get(neighbor_id, float("inf")):
                g_score[neighbor_id] = tentative_g
                came_from[neighbor_id] = (curr_id, edge)
                h_val = heuristic(neighbor_id)
                f_val = tentative_g + h_val
                counter += 1
                heapq.heappush(open_heap, (f_val, counter, neighbor_id))

    # Goal unreachable
    return SearchResult(
        success=False,
        algorithm="modified_energy_aware_astar",
        path=[],
        total_distance_km=0.0,
        total_energy_kwh=0.0,
        total_traffic_delay_minutes=0.0,
        total_charging_wait_minutes=0.0,
        total_charging_duration_minutes=0.0,
        final_cost=float("inf"),
        cost_evaluation=None,
        nodes_expanded=nodes_expanded,
        execution_time_ms=(time.perf_counter() - t_start) * 1000.0,
        message="No valid path exists between start and goal nodes.",
    )


def _reconstruct_path(
    came_from: Dict[str, Tuple[str, GraphEdge]],
    goal_id: str,
    start_id: str,
    algorithm: str,
    nodes_expanded: int,
    start_time: float,
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
) -> SearchResult:
    """
    Reconstructs the node sequence and sums physical metrics along the path.
    Computes Change 18A cost evaluation on the full aggregate route.
    """
    curr = goal_id
    path = [curr]
    edges_traversed: List[GraphEdge] = []

    while curr != start_id:
        prev, edge = came_from[curr]
        edges_traversed.append(edge)
        curr = prev
        path.append(curr)

    path.reverse()
    edges_traversed.reverse()

    tot_dist = sum(e.distance_km for e in edges_traversed)
    tot_energy = sum(e.predicted_energy_kwh or 0.0 for e in edges_traversed)
    tot_traffic = sum(e.traffic_delay_minutes for e in edges_traversed)
    tot_wait = sum(e.charging_wait_minutes for e in edges_traversed)
    tot_duration = sum(e.charging_duration_minutes for e in edges_traversed)

    cost_res = compute_route_cost(
        distance_km=tot_dist,
        predicted_energy_kwh=tot_energy,
        traffic_delay_minutes=tot_traffic,
        charging_wait_minutes=tot_wait,
        charging_duration_minutes=tot_duration,
        weights=weights,
        bounds=bounds,
    )

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    return SearchResult(
        success=True,
        algorithm=algorithm,
        path=path,
        total_distance_km=round(tot_dist, 4),
        total_energy_kwh=round(tot_energy, 4),
        total_traffic_delay_minutes=round(tot_traffic, 2),
        total_charging_wait_minutes=round(tot_wait, 2),
        total_charging_duration_minutes=round(tot_duration, 2),
        final_cost=cost_res.total_cost,
        cost_evaluation=cost_res.to_dict(),
        nodes_expanded=nodes_expanded,
        execution_time_ms=round(elapsed_ms, 3),
        message=f"Path of {len(path)} nodes successfully found.",
    )


def compare_search_algorithms(
    graph: RoutingGraph,
    start_node_id: str,
    goal_node_id: str,
    weights: Optional[CostWeights] = None,
    bounds: Optional[NormalizationBounds] = None,
) -> Dict[str, Any]:
    """
    Runs both Baseline Shortest-Distance A* and Modified Energy-Aware A*
    on the exact same graph and returns a structured comparison.
    """
    baseline_res = search_shortest_path(graph, start_node_id, goal_node_id)
    energy_res = search_energy_aware_path(graph, start_node_id, goal_node_id, weights, bounds)

    paths_match = (baseline_res.path == energy_res.path) if (baseline_res.success and energy_res.success) else False

    dist_diff = round(energy_res.total_distance_km - baseline_res.total_distance_km, 4) if (baseline_res.success and energy_res.success) else 0.0
    energy_diff = round(energy_res.total_energy_kwh - baseline_res.total_energy_kwh, 4) if (baseline_res.success and energy_res.success) else 0.0
    cost_diff = round(energy_res.final_cost - baseline_res.final_cost, 6) if (baseline_res.success and energy_res.success) else 0.0

    return {
        "start_node": start_node_id,
        "goal_node": goal_node_id,
        "paths_identical": paths_match,
        "distance_delta_km": dist_diff,
        "energy_delta_kwh": energy_diff,
        "cost_delta": cost_diff,
        "baseline_shortest_distance": baseline_res.to_dict(),
        "modified_energy_aware": energy_res.to_dict(),
    }

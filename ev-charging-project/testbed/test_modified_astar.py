"""
Comprehensive Unit Tests for Energy-Aware Modified A* Route Search
===================================================================
Validation suite covering all 10 required test scenarios:
  1. Simple single-path graph
  2. Multiple alternative paths
  3. Shortest-distance route selection (baseline behavior)
  4. Energy-aware route selection (divergence from shortest distance)
  5. Traffic penalty changing route choice
  6. Charging penalty changing route choice
  7. Unreachable destination
  8. Start equals goal
  9. Path reconstruction correctness
  10. Deterministic repeated execution
  + Side-by-side comparison function test
"""

import os
import sys
import unittest

# Ensure models directory is accessible
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MODELS_DIR = os.path.join(_PROJECT_ROOT, "models")
if _MODELS_DIR not in sys.path:
    sys.path.insert(0, _MODELS_DIR)

from modified_astar import (
    GraphNode,
    GraphEdge,
    RoutingGraph,
    SearchResult,
    search_shortest_path,
    search_energy_aware_path,
    compare_search_algorithms,
)
from route_cost_function import DEFAULT_WEIGHTS, DEFAULT_BOUNDS


class TestModifiedAStar(unittest.TestCase):

    def setUp(self):
        self.weights = DEFAULT_WEIGHTS
        self.bounds = DEFAULT_BOUNDS

    # Scenario 1: Simple single-path graph
    def test_scenario_1_single_path(self):
        graph = RoutingGraph()
        graph.add_node("A", x_km=0.0, y_km=0.0)
        graph.add_node("B", x_km=10.0, y_km=0.0)
        graph.add_node("C", x_km=25.0, y_km=0.0)

        graph.add_edge("A", "B", distance_km=10.0, predicted_energy_kwh=1.5)
        graph.add_edge("B", "C", distance_km=15.0, predicted_energy_kwh=2.25)

        res = search_energy_aware_path(graph, "A", "C")
        self.assertTrue(res.success)
        self.assertEqual(res.path, ["A", "B", "C"])
        self.assertEqual(res.total_distance_km, 25.0)
        self.assertEqual(res.total_energy_kwh, 3.75)
        self.assertGreater(res.final_cost, 0.0)
        self.assertLess(res.final_cost, 1.0)

    # Scenario 2: Multiple alternative paths (finding best path)
    def test_scenario_2_multiple_alternative_paths(self):
        graph = RoutingGraph()
        # Diamond topology: Start S -> Path1 (via N1) or Path2 (via N2) -> Goal G
        graph.add_node("S", x_km=0.0, y_km=0.0)
        graph.add_node("N1", x_km=10.0, y_km=5.0)
        graph.add_node("N2", x_km=10.0, y_km=-5.0)
        graph.add_node("G", x_km=20.0, y_km=0.0)

        # Path 1: distance 10 + 10 = 20, energy 3.0 + 3.0 = 6.0
        graph.add_edge("S", "N1", distance_km=10.0, predicted_energy_kwh=3.0)
        graph.add_edge("N1", "G", distance_km=10.0, predicted_energy_kwh=3.0)

        # Path 2: distance 10 + 10 = 20, energy 1.5 + 1.5 = 3.0 (much lower energy)
        graph.add_edge("S", "N2", distance_km=10.0, predicted_energy_kwh=1.5)
        graph.add_edge("N2", "G", distance_km=10.0, predicted_energy_kwh=1.5)

        res = search_energy_aware_path(graph, "S", "G")
        self.assertTrue(res.success)
        self.assertEqual(res.path, ["S", "N2", "G"])
        self.assertEqual(res.total_energy_kwh, 3.0)

    # Scenario 3: Shortest-distance route selection
    def test_scenario_3_shortest_distance_route_selection(self):
        graph = RoutingGraph()
        graph.add_node("S", x_km=0.0, y_km=0.0)
        graph.add_node("D_short", x_km=5.0, y_km=0.0)
        graph.add_node("D_long", x_km=15.0, y_km=10.0)
        graph.add_node("G", x_km=20.0, y_km=0.0)

        # Short route: 5 + 15 = 20 km (baseline energy rate)
        graph.add_edge("S", "D_short", distance_km=5.0)
        graph.add_edge("D_short", "G", distance_km=15.0)

        # Long route: 18 + 18 = 36 km
        graph.add_edge("S", "D_long", distance_km=18.0)
        graph.add_edge("D_long", "G", distance_km=18.0)

        # Baseline shortest path selects D_short
        base_res = search_shortest_path(graph, "S", "G")
        self.assertEqual(base_res.path, ["S", "D_short", "G"])
        self.assertEqual(base_res.total_distance_km, 20.0)

        # Energy aware also selects D_short because both have identical default consumption rate
        energy_res = search_energy_aware_path(graph, "S", "G")
        self.assertEqual(energy_res.path, ["S", "D_short", "G"])

    # Scenario 4: Energy-aware route selection (diverges from shortest distance)
    def test_scenario_4_energy_aware_route_divergence(self):
        graph = RoutingGraph()
        graph.add_node("S", x_km=0.0, y_km=0.0)
        graph.add_node("HighSpeedHighway", x_km=10.0, y_km=2.0)
        graph.add_node("EcoBypass", x_km=12.0, y_km=-3.0)
        graph.add_node("G", x_km=25.0, y_km=0.0)

        # Highway: Shorter distance (25 km), but steep uphill/high speed -> 8.0 kWh energy
        # Distance: 12 + 13 = 25 km
        # Energy: 4.0 + 4.0 = 8.0 kWh
        graph.add_edge("S", "HighSpeedHighway", distance_km=12.0, predicted_energy_kwh=4.0)
        graph.add_edge("HighSpeedHighway", "G", distance_km=13.0, predicted_energy_kwh=4.0)

        # EcoBypass: Slightly longer distance (28 km), but gentle grade & optimal speed -> only 3.0 kWh energy
        # Distance: 14 + 14 = 28 km
        # Energy: 1.5 + 1.5 = 3.0 kWh
        graph.add_edge("S", "EcoBypass", distance_km=14.0, predicted_energy_kwh=1.5)
        graph.add_edge("EcoBypass", "G", distance_km=14.0, predicted_energy_kwh=1.5)

        # Baseline shortest distance MUST choose Highway
        base_res = search_shortest_path(graph, "S", "G")
        self.assertEqual(base_res.path, ["S", "HighSpeedHighway", "G"])
        self.assertEqual(base_res.total_distance_km, 25.0)

        # Energy-aware A* MUST choose EcoBypass because energy savings outweigh the 3 km distance delta
        energy_res = search_energy_aware_path(graph, "S", "G")
        self.assertEqual(energy_res.path, ["S", "EcoBypass", "G"])
        self.assertEqual(energy_res.total_distance_km, 28.0)
        self.assertEqual(energy_res.total_energy_kwh, 3.0)
        self.assertLess(energy_res.final_cost, base_res.final_cost)

    # Scenario 5: Traffic penalty changing route choice
    def test_scenario_5_traffic_penalty_impact(self):
        graph = RoutingGraph()
        graph.add_node("S", x_km=0.0, y_km=0.0)
        graph.add_node("CityCenter", x_km=8.0, y_km=0.0)
        graph.add_node("RingRoad", x_km=8.0, y_km=6.0)
        graph.add_node("G", x_km=16.0, y_km=0.0)

        # City Center: Short distance (16 km), identical energy (2.4 kWh), but 40 minutes traffic gridlock
        graph.add_edge("S", "CityCenter", distance_km=8.0, predicted_energy_kwh=1.2, traffic_delay_minutes=20.0)
        graph.add_edge("CityCenter", "G", distance_km=8.0, predicted_energy_kwh=1.2, traffic_delay_minutes=20.0)

        # Ring Road: Longer distance (20 km), energy 3.0 kWh, but 0 traffic delay
        graph.add_edge("S", "RingRoad", distance_km=10.0, predicted_energy_kwh=1.5, traffic_delay_minutes=0.0)
        graph.add_edge("RingRoad", "G", distance_km=10.0, predicted_energy_kwh=1.5, traffic_delay_minutes=0.0)

        # Baseline distance chooses CityCenter
        base_res = search_shortest_path(graph, "S", "G")
        self.assertEqual(base_res.path, ["S", "CityCenter", "G"])

        # Energy-aware Modified A* chooses RingRoad due to the heavy traffic penalty
        energy_res = search_energy_aware_path(graph, "S", "G")
        self.assertEqual(energy_res.path, ["S", "RingRoad", "G"])
        self.assertLess(energy_res.final_cost, base_res.final_cost)

    # Scenario 6: Charging penalty changing route choice
    def test_scenario_6_charging_penalty_impact(self):
        graph = RoutingGraph()
        graph.add_node("Origin", x_km=0.0, y_km=0.0)
        graph.add_node("Charger_A_Congested", x_km=15.0, y_km=2.0)
        graph.add_node("Charger_B_FastOpen", x_km=18.0, y_km=-3.0)
        graph.add_node("Destination", x_km=35.0, y_km=0.0)

        # Route via Charger A: Shorter distance (30 km), but 45 min queue wait + 50 min slow charge
        graph.add_edge(
            "Origin", "Charger_A_Congested",
            distance_km=15.0, predicted_energy_kwh=2.25,
            charging_wait_minutes=45.0, charging_duration_minutes=50.0,
        )
        graph.add_edge("Charger_A_Congested", "Destination", distance_km=15.0, predicted_energy_kwh=2.25)

        # Route via Charger B: 5 km longer (35 km), but 0 wait + 20 min high-power DC fast charge
        graph.add_edge(
            "Origin", "Charger_B_FastOpen",
            distance_km=18.0, predicted_energy_kwh=2.7,
            charging_wait_minutes=0.0, charging_duration_minutes=20.0,
        )
        graph.add_edge("Charger_B_FastOpen", "Destination", distance_km=17.0, predicted_energy_kwh=2.55)

        # Baseline shortest distance chooses congested charger A
        base_res = search_shortest_path(graph, "Origin", "Destination")
        self.assertEqual(base_res.path, ["Origin", "Charger_A_Congested", "Destination"])

        # Modified Energy-Aware A* chooses Charger B due to queue and charging duration advantages
        energy_res = search_energy_aware_path(graph, "Origin", "Destination")
        self.assertEqual(energy_res.path, ["Origin", "Charger_B_FastOpen", "Destination"])
        self.assertLess(energy_res.final_cost, base_res.final_cost)

    # Scenario 7: Unreachable destination
    def test_scenario_7_unreachable_destination(self):
        graph = RoutingGraph()
        graph.add_node("Island_A", x_km=0.0, y_km=0.0)
        graph.add_node("Island_B", x_km=50.0, y_km=50.0)
        # No edges connecting A and B

        res_base = search_shortest_path(graph, "Island_A", "Island_B")
        self.assertFalse(res_base.success)
        self.assertEqual(res_base.path, [])

        res_energy = search_energy_aware_path(graph, "Island_A", "Island_B")
        self.assertFalse(res_energy.success)
        self.assertEqual(res_energy.path, [])
        self.assertEqual(res_energy.final_cost, float("inf"))

    # Scenario 8: Start equals goal
    def test_scenario_8_start_equals_goal(self):
        graph = RoutingGraph()
        graph.add_node("SingleNode", x_km=10.0, y_km=10.0)

        res = search_energy_aware_path(graph, "SingleNode", "SingleNode")
        self.assertTrue(res.success)
        self.assertEqual(res.path, ["SingleNode"])
        self.assertEqual(res.total_distance_km, 0.0)
        self.assertEqual(res.total_energy_kwh, 0.0)
        self.assertEqual(res.final_cost, 0.0)
        self.assertEqual(res.nodes_expanded, 0)

    # Scenario 9: Path reconstruction correctness
    def test_scenario_9_path_reconstruction_correctness(self):
        graph = RoutingGraph()
        nodes = ["P1", "P2", "P3", "P4", "P5"]
        for i, n in enumerate(nodes):
            graph.add_node(n, x_km=float(i * 5), y_km=0.0)

        for i in range(len(nodes) - 1):
            graph.add_edge(nodes[i], nodes[i + 1], distance_km=5.0, predicted_energy_kwh=0.75)

        res = search_energy_aware_path(graph, "P1", "P5")
        self.assertTrue(res.success)
        self.assertEqual(res.path, nodes)
        self.assertEqual(res.total_distance_km, 20.0)
        self.assertEqual(res.total_energy_kwh, 3.0)

    # Scenario 10: Deterministic repeated execution
    def test_scenario_10_deterministic_repeated_execution(self):
        graph = RoutingGraph()
        for i in range(10):
            graph.add_node(f"N{i}", x_km=float(i), y_km=float(i % 2))
        for i in range(9):
            graph.add_edge(f"N{i}", f"N{i+1}", distance_km=2.0)
            if i + 2 < 10:
                graph.add_edge(f"N{i}", f"N{i+2}", distance_km=3.5, predicted_energy_kwh=0.4)

        first_run = search_energy_aware_path(graph, "N0", "N9")
        for _ in range(5):
            subsequent_run = search_energy_aware_path(graph, "N0", "N9")
            self.assertEqual(first_run.path, subsequent_run.path)
            self.assertEqual(first_run.total_distance_km, subsequent_run.total_distance_km)
            self.assertEqual(first_run.final_cost, subsequent_run.final_cost)

    # Comparison helper verification
    def test_compare_search_algorithms_helper(self):
        graph = RoutingGraph()
        graph.add_node("A", x_km=0.0, y_km=0.0)
        graph.add_node("B", x_km=10.0, y_km=0.0)
        graph.add_edge("A", "B", distance_km=10.0)

        comparison = compare_search_algorithms(graph, "A", "B")
        self.assertEqual(comparison["start_node"], "A")
        self.assertEqual(comparison["goal_node"], "B")
        self.assertTrue(comparison["paths_identical"])
        self.assertIn("baseline_shortest_distance", comparison)
        self.assertIn("modified_energy_aware", comparison)


if __name__ == "__main__":
    unittest.main()

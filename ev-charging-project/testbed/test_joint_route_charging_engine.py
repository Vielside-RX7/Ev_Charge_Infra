"""
Comprehensive Unit Tests for Joint Route + Charging Decision Engine
====================================================================
Task 19 Validation Suite covering all 10 required scenarios:
  1. Destination directly reachable -> DIRECT selected
  2. Destination unreachable, but one charger makes destination reachable -> CHARGE selected
  3. Multiple feasible chargers -> lower overall joint cost selected
  4. Closest charger is NOT the best charger (due to wait/duration/power)
  5. Charger cannot be reached -> candidate rejected
  6. Charger can be reached, but destination unreachable after charging -> candidate rejected
  7. Charging plan has higher aggregate cost than feasible direct route -> DIRECT selected
  8. No feasible charging station -> explicit failure reason
  9. Start equals destination -> DIRECT with 0 displacement
  10. Repeated execution with identical inputs -> deterministic identical output
"""

import os
import sys
import unittest

# Ensure models directory is accessible
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MODELS_DIR = os.path.join(_PROJECT_ROOT, "models")
if _MODELS_DIR not in sys.path:
    sys.path.insert(0, _MODELS_DIR)

from modified_astar import RoutingGraph
from joint_route_charging_engine import (
    VehicleState,
    ChargerCandidate,
    JointPlanResult,
    evaluate_joint_route_and_charging,
    estimate_charging_duration,
)


class TestJointRouteChargingEngine(unittest.TestCase):

    # Scenario 1: Destination directly reachable -> DIRECT selected
    def test_scenario_1_destination_directly_reachable(self):
        # 30 kWh battery, 80% SoC -> 24 kWh total, 5% reserve (1.5 kWh) -> 22.5 kWh usable
        # 30 km trip requires 30 * 0.150 = 4.5 kWh << 22.5 kWh usable
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=80.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=30.0, y_km=0.0)
        graph.add_node("ChargerStation", x_km=15.0, y_km=5.0)

        graph.add_edge("Start", "Goal", distance_km=30.0)
        graph.add_edge("Start", "ChargerStation", distance_km=18.0)
        graph.add_edge("ChargerStation", "Goal", distance_km=18.0)

        charger = ChargerCandidate(
            charger_id="CH_01",
            name="Midway Fast Charger",
            node_id="ChargerStation",
            charging_power_kw=50.0,
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "DIRECT")
        self.assertIsNone(res.selected_charger_id)
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_distance_km, 30.0)
        self.assertEqual(res.total_energy_kwh, 4.5)
        self.assertEqual(res.estimated_charging_duration_minutes, 0.0)

    # Scenario 2: Destination unreachable, but one charger makes destination reachable -> CHARGE selected
    def test_scenario_2_destination_unreachable_charger_enables(self):
        # 30 kWh battery, 20% SoC -> 6.0 kWh total, 5% reserve (1.5 kWh) -> 4.5 kWh usable (max ~30 km)
        # Destination is 60 km away (requires 9.0 kWh > 4.5 kWh usable -> Direct Infeasible)
        # Charger is 20 km away (requires 3.0 kWh <= 4.5 kWh usable -> Reached with 3.0 kWh remaining)
        # Charger -> Destination is 40 km away (requires 6.0 kWh)
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=20.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=60.0, y_km=0.0)
        graph.add_node("EnRouteCharger", x_km=20.0, y_km=0.0)

        graph.add_edge("Start", "Goal", distance_km=60.0)
        graph.add_edge("Start", "EnRouteCharger", distance_km=20.0)
        graph.add_edge("EnRouteCharger", "Goal", distance_km=40.0)

        charger = ChargerCandidate(
            charger_id="CH_ENROUTE",
            name="En-Route 100kW Charger",
            node_id="EnRouteCharger",
            charging_power_kw=100.0,
            probability_available=0.9,
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "CHARGE")
        self.assertEqual(res.selected_charger_id, "CH_ENROUTE")
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_distance_km, 60.0)
        self.assertEqual(res.total_energy_kwh, 9.0)
        self.assertGreater(res.energy_added_at_charger_kwh, 0.0)
        self.assertGreater(res.estimated_charging_duration_minutes, 0.0)

    # Scenario 3: Multiple feasible chargers -> lower overall joint cost selected
    def test_scenario_3_multiple_chargers_lowest_cost_selected(self):
        # Destination is 70 km away, battery 20% SoC (4.5 kWh usable)
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=20.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=70.0, y_km=0.0)
        graph.add_node("Charger_A", x_km=20.0, y_km=5.0)
        graph.add_node("Charger_B", x_km=22.0, y_km=-2.0)

        graph.add_edge("Start", "Goal", distance_km=70.0)

        # Charger A: distance 22 + 50 = 72 km, but 25 kW slow charger + 40 min queue wait
        graph.add_edge("Start", "Charger_A", distance_km=22.0)
        graph.add_edge("Charger_A", "Goal", distance_km=50.0)

        # Charger B: distance 23 + 49 = 72 km, 150 kW ultra-fast charger + 0 min queue wait
        graph.add_edge("Start", "Charger_B", distance_km=23.0)
        graph.add_edge("Charger_B", "Goal", distance_km=49.0)

        cand_a = ChargerCandidate(
            charger_id="C_SLOW",
            name="Slow Busy Charger",
            node_id="Charger_A",
            charging_power_kw=25.0,
            charging_wait_minutes=40.0,
        )
        cand_b = ChargerCandidate(
            charger_id="C_FAST",
            name="Ultra-Fast Open Charger",
            node_id="Charger_B",
            charging_power_kw=150.0,
            charging_wait_minutes=0.0,
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [cand_a, cand_b], graph=graph)
        self.assertEqual(res.decision_type, "CHARGE")
        self.assertEqual(res.selected_charger_id, "C_FAST")
        self.assertLess(res.estimated_charging_duration_minutes, 30.0)

    # Scenario 4: Closest charger is NOT the best charger
    def test_scenario_4_closest_charger_not_best(self):
        # Start -> Goal: 60 km, battery 20% SoC (usable 4.5 kWh)
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=20.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=60.0, y_km=0.0)
        graph.add_node("CloseCharger", x_km=10.0, y_km=0.0)
        graph.add_node("BetterFarCharger", x_km=22.0, y_km=0.0)

        graph.add_edge("Start", "Goal", distance_km=60.0)

        # Close Charger: 10 km away (Leg 1 = 10, Leg 2 = 50), but 60 min queue wait, 20 kW slow charger
        graph.add_edge("Start", "CloseCharger", distance_km=10.0)
        graph.add_edge("CloseCharger", "Goal", distance_km=50.0)

        # Better Far Charger: 22 km away (Leg 1 = 22, Leg 2 = 38), 0 queue wait, 150 kW fast charger
        graph.add_edge("Start", "BetterFarCharger", distance_km=22.0)
        graph.add_edge("BetterFarCharger", "Goal", distance_km=38.0)

        close_cand = ChargerCandidate(
            charger_id="CLOSE_BUSY",
            name="Closest Charger But Terrible Queue",
            node_id="CloseCharger",
            charging_power_kw=20.0,
            charging_wait_minutes=60.0,
        )
        better_cand = ChargerCandidate(
            charger_id="FAR_FAST",
            name="Farther Charger But Fast & Available",
            node_id="BetterFarCharger",
            charging_power_kw=150.0,
            charging_wait_minutes=0.0,
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [close_cand, better_cand], graph=graph)
        self.assertEqual(res.decision_type, "CHARGE")
        # FAR_FAST is chosen despite CloseCharger having shorter leg 1 distance
        self.assertEqual(res.selected_charger_id, "FAR_FAST")
        self.assertLess(res.total_route_cost, res.candidate_evaluations[0]["total_route_cost"] if res.candidate_evaluations[0]["charger_id"] == "CLOSE_BUSY" else 1.0)

    # Scenario 5: Charger cannot be reached -> candidate rejected
    def test_scenario_5_unreachable_charger_rejected(self):
        # Battery has only 1.5 kWh usable (SoC = 10%, capacity = 30 kWh, reserve = 5%)
        # Usable range is (1.5 / 0.150) = 10 km
        # Charger is 30 km away -> completely unreachable
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=10.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=50.0, y_km=0.0)
        graph.add_node("FarCharger", x_km=30.0, y_km=0.0)

        graph.add_edge("Start", "Goal", distance_km=50.0)
        graph.add_edge("Start", "FarCharger", distance_km=30.0)
        graph.add_edge("FarCharger", "Goal", distance_km=20.0)

        charger = ChargerCandidate(
            charger_id="OUT_OF_RANGE",
            name="Out of Range Station",
            node_id="FarCharger",
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "INFEASIBLE")
        self.assertFalse(res.feasible)
        self.assertIn("cannot be reached", res.reason)

    # Scenario 6: Charger can be reached, but destination cannot be reached after charging
    def test_scenario_6_destination_unreachable_after_charging(self):
        # Battery capacity is 30 kWh (maximum full usable = 30 - 1.5 = 28.5 kWh -> max 190 km)
        # Leg 2 from Charger to Goal is 250 km (requires 37.5 kWh > 28.5 kWh full battery!)
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=50.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("SuperFarGoal", x_km=260.0, y_km=0.0)
        graph.add_node("LocalCharger", x_km=10.0, y_km=0.0)

        graph.add_edge("Start", "SuperFarGoal", distance_km=260.0)
        graph.add_edge("Start", "LocalCharger", distance_km=10.0)
        graph.add_edge("LocalCharger", "SuperFarGoal", distance_km=250.0)

        charger = ChargerCandidate(
            charger_id="C_LOCAL",
            name="Local Charger",
            node_id="LocalCharger",
        )

        res = evaluate_joint_route_and_charging("Start", "SuperFarGoal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "INFEASIBLE")
        self.assertFalse(res.feasible)
        self.assertIn("Destination is unreachable from charger", res.reason)

    # Scenario 7: Charging plan has higher aggregate cost than direct route, when direct route is feasible -> DIRECT selected
    def test_scenario_7_charging_cost_higher_than_direct(self):
        # 30 kWh battery, 90% SoC (usable 25.5 kWh -> up to 170 km)
        # Destination is 25 km away (requires 3.75 kWh) -> Direct is easily feasible
        # En-route charging detour adds distance, wait, and duration
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=90.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=25.0, y_km=0.0)
        graph.add_node("DetourCharger", x_km=15.0, y_km=15.0)

        graph.add_edge("Start", "Goal", distance_km=25.0)
        graph.add_edge("Start", "DetourCharger", distance_km=22.0)
        graph.add_edge("DetourCharger", "Goal", distance_km=22.0)

        charger = ChargerCandidate(
            charger_id="DETOUR",
            name="Detour Charger",
            node_id="DetourCharger",
            charging_power_kw=50.0,
            charging_wait_minutes=15.0,
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "DIRECT")
        self.assertTrue(res.feasible)
        self.assertIn("No charging stop required", res.reason)

    # Scenario 8: No feasible charging station -> return explicit failure reason
    def test_scenario_8_no_feasible_charging_station(self):
        # Vehicle empty (5% SoC == reserve, 0.0 kWh usable)
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=5.0)

        graph = RoutingGraph()
        graph.add_node("Start", x_km=0.0, y_km=0.0)
        graph.add_node("Goal", x_km=40.0, y_km=0.0)
        graph.add_node("Station1", x_km=15.0, y_km=0.0)

        graph.add_edge("Start", "Goal", distance_km=40.0)
        graph.add_edge("Start", "Station1", distance_km=15.0)
        graph.add_edge("Station1", "Goal", distance_km=25.0)

        charger = ChargerCandidate(
            charger_id="S1",
            name="Station 1",
            node_id="Station1",
        )

        res = evaluate_joint_route_and_charging("Start", "Goal", vehicle, [charger], graph=graph)
        self.assertEqual(res.decision_type, "INFEASIBLE")
        self.assertFalse(res.feasible)
        self.assertIn("Trip infeasible", res.reason)

    # Scenario 9: Start equals destination -> DIRECT with 0 displacement
    def test_scenario_9_start_equals_destination(self):
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=50.0)
        res = evaluate_joint_route_and_charging("SameLoc", "SameLoc", vehicle, [])
        self.assertEqual(res.decision_type, "DIRECT")
        self.assertTrue(res.feasible)
        self.assertEqual(res.total_distance_km, 0.0)
        self.assertEqual(res.total_energy_kwh, 0.0)
        self.assertEqual(res.total_route_cost, 0.0)

    # Scenario 10: Repeated execution with identical inputs -> deterministic identical output
    def test_scenario_10_deterministic_reproducibility(self):
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=25.0)

        graph = RoutingGraph()
        graph.add_node("S", x_km=0.0, y_km=0.0)
        graph.add_node("G", x_km=55.0, y_km=0.0)
        graph.add_node("C1", x_km=20.0, y_km=3.0)
        graph.add_node("C2", x_km=25.0, y_km=-2.0)

        graph.add_edge("S", "G", distance_km=55.0)
        graph.add_edge("S", "C1", distance_km=20.0)
        graph.add_edge("C1", "G", distance_km=37.0)
        graph.add_edge("S", "C2", distance_km=25.0)
        graph.add_edge("C2", "G", distance_km=32.0)

        candidates = [
            ChargerCandidate("C1", "Charger 1", node_id="C1", charging_power_kw=50.0),
            ChargerCandidate("C2", "Charger 2", node_id="C2", charging_power_kw=100.0),
        ]

        first_res = evaluate_joint_route_and_charging("S", "G", vehicle, candidates, graph=graph)

        for _ in range(5):
            subsequent_res = evaluate_joint_route_and_charging("S", "G", vehicle, candidates, graph=graph)
            self.assertEqual(first_res.decision_type, subsequent_res.decision_type)
            self.assertEqual(first_res.selected_charger_id, subsequent_res.selected_charger_id)
            self.assertEqual(first_res.total_distance_km, subsequent_res.total_distance_km)
            self.assertEqual(first_res.total_energy_kwh, subsequent_res.total_energy_kwh)
            self.assertEqual(first_res.total_route_cost, subsequent_res.total_route_cost)


if __name__ == "__main__":
    unittest.main()

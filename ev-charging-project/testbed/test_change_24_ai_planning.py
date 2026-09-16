"""
Comprehensive Unit Tests for Change 24: AI-Informed Journey Planning
====================================================================
Validates all requirements for integrating XGBoost Energy Prediction into
the joint route and charging decision engine while maintaining physical
battery feasibility and safety constraints.

Test Scenarios Covered:
  1. XGBoost prediction successfully available via EnergyPredictionService.
  2. AI prediction incorporated into candidate planning metadata.
  3. Physical baseline remains authoritative for feasibility checks (0.150 kWh/km).
  4. AI cannot incorrectly make an infeasible route feasible (Safety Hard Constraint).
  5. AI can alter preference between two otherwise feasible candidates.
  6. Missing or failing AI prediction gracefully falls back to baseline planning.
  7. Deterministic repeated planning yields identical results.
  8. DIRECT workflow with AI prediction metadata.
  9. CHARGE workflow with AI prediction metadata.
  10. INFEASIBLE workflow with clean diagnostics and safe fallback.
"""

import os
import sys
import unittest
from typing import Dict, Any

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

import pandas as pd
from fastapi.testclient import TestClient
from api.main import app
import recommendation_engine
from energy_prediction_service import EnergyPredictionService
from modified_astar import RoutingGraph
from joint_route_charging_engine import (
    VehicleState,
    ChargerCandidate,
    JointPlanResult,
    evaluate_joint_route_and_charging,
)


class TestChange24AIEnergyPlanning(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Setup mock chargers for FastAPI TestClient
        mock_df = pd.DataFrame([
            {
                "id": 1,
                "name": "Midway Express DC Charger",
                "operator": "Tata Power",
                "address": "Electronic City Highway",
                "city": "Bangalore",
                "latitude": 12.9000,
                "longitude": 77.5000,
                "charging_power_kw": 120.0,
                "num_ports": 4,
                "connector_type": "CCS2",
                "tariff_inr_per_kwh": 18.0,
                "reliability": 0.98,
                "probability_available": 0.90,
            }
        ])
        recommendation_engine._CACHED_CHARGERS_DF = mock_df
        cls.client = TestClient(app)
        cls.energy_service = EnergyPredictionService()

    # --------------------------------------------------------------------------
    # 1. XGBoost prediction successfully available
    # --------------------------------------------------------------------------
    def test_1_xgboost_service_available(self):
        result = self.energy_service.predict_trip_energy({
            "distance_km": 25.0,
            "trip_duration_minutes": 30.0,
            "battery_capacity_kwh": 30.0,
            "starting_soc_percent": 80.0,
            "average_speed_kmph": 50.0,
        })
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("model"), "XGBoost")
        self.assertIn("predicted_trip_energy_kwh", result)
        self.assertGreater(result["predicted_trip_energy_kwh"], 0.0)
        self.assertIn("baseline_trip_energy_kwh", result)
        self.assertAlmostEqual(result["baseline_trip_energy_kwh"], 25.0 * 0.150, places=3)

    # --------------------------------------------------------------------------
    # 2. AI prediction incorporated into candidate planning metadata
    # --------------------------------------------------------------------------
    def test_2_ai_prediction_incorporated_into_planning(self):
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=80.0)
        graph = RoutingGraph()
        graph.add_node("A", 0.0, 0.0)
        graph.add_node("B", 20.0, 0.0)
        graph.add_edge("A", "B", distance_km=20.0)

        result = evaluate_joint_route_and_charging(
            origin="A",
            destination="B",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=self.energy_service.predict_trip_energy,
        )

        self.assertEqual(result.decision_type, "DIRECT")
        self.assertIsNotNone(result.ai_energy_prediction)
        ai_info = result.ai_energy_prediction
        self.assertTrue(ai_info["available"])
        self.assertEqual(ai_info["model"], "XGBoost")
        self.assertGreater(ai_info["predicted_energy_kwh"], 0.0)
        self.assertAlmostEqual(ai_info["baseline_energy_kwh"], 20.0 * 0.150, places=3)
        self.assertTrue(ai_info["used_for_planning"])
        self.assertFalse(ai_info["used_for_battery_state"])
        self.assertIn("delta_kwh", ai_info)
        self.assertIn("delta_percent", ai_info)

    # --------------------------------------------------------------------------
    # 3. Physical baseline remains authoritative for feasibility
    # --------------------------------------------------------------------------
    def test_3_physical_baseline_authoritative_for_feasibility(self):
        # Battery has 10.0 kWh capacity, 50% SoC = 5.0 kWh total, 5% reserve (0.5 kWh) -> 4.5 kWh usable
        # 30.0 km route requires 30.0 * 0.150 = 4.500 kWh.
        # Exactly at the margin of physical feasibility.
        vehicle = VehicleState(battery_capacity_kwh=10.0, current_soc_percent=50.0, reserve_battery_percent=5.0)
        graph = RoutingGraph()
        graph.add_node("Origin", 0.0, 0.0)
        graph.add_node("Dest", 30.0, 0.0)
        graph.add_edge("Origin", "Dest", distance_km=30.0)

        # Usable energy is exactly 4.5 kWh, baseline energy required is 4.5 kWh -> feasible
        result = evaluate_joint_route_and_charging(
            origin="Origin",
            destination="Dest",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=self.energy_service.predict_trip_energy,
        )
        self.assertEqual(result.decision_type, "DIRECT")
        self.assertAlmostEqual(result.total_energy_kwh, 4.5, places=3)

    # --------------------------------------------------------------------------
    # 4. AI cannot incorrectly make an infeasible route feasible (Safety Rule)
    # --------------------------------------------------------------------------
    def test_4_ai_cannot_override_physical_infeasibility(self):
        # Battery available = 4.0 kWh usable
        # (Capacity 10 kWh, 45% SoC = 4.5 kWh total, 5% reserve = 0.5 kWh -> usable 4.0 kWh)
        # Physical baseline requirement = 30.0 km * 0.150 = 4.5 kWh > 4.0 kWh -> physically INFEASIBLE.
        # Optimistic AI predictor predicts 3.8 kWh (< 4.0 kWh usable).
        vehicle = VehicleState(battery_capacity_kwh=10.0, current_soc_percent=45.0, reserve_battery_percent=5.0)
        self.assertAlmostEqual(vehicle.usable_energy_kwh, 4.0, places=3)

        graph = RoutingGraph()
        graph.add_node("O", 0.0, 0.0)
        graph.add_node("D", 30.0, 0.0)
        graph.add_edge("O", "D", distance_km=30.0)

        # Mock optimistic AI predictor that underpredicts energy consumption
        def optimistic_ai_predictor(context: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "status": "success",
                "model": "XGBoost",
                "predicted_trip_energy_kwh": 3.8,  # Optimistic!
                "baseline_trip_energy_kwh": 4.5,
                "difference_from_baseline_kwh": -0.7,
                "difference_percent": -15.55,
            }

        result = evaluate_joint_route_and_charging(
            origin="O",
            destination="D",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=optimistic_ai_predictor,
        )

        # Must strictly be rejected as INFEASIBLE by the physical safety guard!
        self.assertEqual(result.decision_type, "INFEASIBLE")
        self.assertIn("insufficient battery", result.reason.lower())
        self.assertIsNotNone(result.ai_energy_prediction)
        # Even though AI predicted 3.8 kWh, physical baseline of 4.5 kWh prevented false safety pass
        self.assertAlmostEqual(result.ai_energy_prediction["predicted_energy_kwh"], 3.8, places=3)
        self.assertAlmostEqual(result.ai_energy_prediction["baseline_energy_kwh"], 4.5, places=3)

    # --------------------------------------------------------------------------
    # 5. AI can alter preference between two otherwise feasible candidates
    # --------------------------------------------------------------------------
    def test_5_ai_can_alter_candidate_preference(self):
        # Controlled test:
        # Destination is 70 km away. Battery at 20% SoC on 30 kWh (4.5 kWh usable = max 30 km).
        # Direct route (70 km) requires 10.5 kWh > 4.5 kWh -> strictly INFEASIBLE direct.
        # Two candidate chargers:
        # Charger A: 20 km from Origin (requires 3.0 kWh), 50 km from Charger to Dest (total 70 km).
        # Charger B: 22 km from Origin (requires 3.3 kWh), 50 km from Charger to Dest (total 72 km).
        # Both chargers have identical 50 kW power, 95% reliability, 90% availability.
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=20.0, target_soc_percent=80.0)

        graph = RoutingGraph()
        graph.add_node("Origin", 0.0, 0.0)
        graph.add_node("Dest", 70.0, 0.0)
        graph.add_node("ChargerA", 20.0, 5.0)
        graph.add_node("ChargerB", 22.0, -2.0)

        # Direct edge (physically infeasible: 70 km * 0.15 = 10.5 kWh > 4.5 kWh)
        graph.add_edge("Origin", "Dest", distance_km=70.0)

        # Charger A legs (total 70 km)
        graph.add_edge("Origin", "ChargerA", distance_km=20.0)
        graph.add_edge("ChargerA", "Dest", distance_km=50.0)

        # Charger B legs (total 72 km)
        graph.add_edge("Origin", "ChargerB", distance_km=22.0)
        graph.add_edge("ChargerB", "Dest", distance_km=50.0)

        cand_A = ChargerCandidate(
            charger_id="CH_A",
            name="Charger A (Shorter, High Energy)",
            node_id="ChargerA",
            charging_power_kw=50.0,
            reliability=0.95,
            probability_available=0.90,
        )
        cand_B = ChargerCandidate(
            charger_id="CH_B",
            name="Charger B (Slightly Longer, Eco Corridor)",
            node_id="ChargerB",
            charging_power_kw=50.0,
            reliability=0.95,
            probability_available=0.90,
        )

        # Baseline-only planning (without AI): Charger A has shorter distance (70 km vs 72 km), so lower cost
        result_baseline = evaluate_joint_route_and_charging(
            origin="Origin",
            destination="Dest",
            vehicle_state=vehicle,
            charger_candidates=[cand_A, cand_B],
            graph=graph,
            ai_energy_predictor=None,
        )
        self.assertEqual(result_baseline.decision_type, "CHARGE")
        self.assertEqual(result_baseline.selected_charger_id, "CH_A")

        # Now evaluate with AI energy predictor:
        # Route A (70 km): predicts heavy consumption (15.0 kWh vs baseline 10.5 kWh)
        # Route B (72 km): predicts efficient consumption (7.5 kWh vs baseline 10.8 kWh)
        def route_aware_ai_predictor(context: Dict[str, Any]) -> Dict[str, Any]:
            dist = context.get("distance_km", 0.0)
            if dist < 71.0:  # Charger A route (70 km)
                return {
                    "status": "success",
                    "model": "XGBoost",
                    "predicted_trip_energy_kwh": 15.0,
                    "baseline_trip_energy_kwh": dist * 0.150,
                    "difference_from_baseline_kwh": 15.0 - (dist * 0.150),
                    "difference_percent": 42.86,
                }
            else:  # Charger B route (72 km)
                return {
                    "status": "success",
                    "model": "XGBoost",
                    "predicted_trip_energy_kwh": 7.5,
                    "baseline_trip_energy_kwh": dist * 0.150,
                    "difference_from_baseline_kwh": 7.5 - (dist * 0.150),
                    "difference_percent": -30.56,
                }

        result_ai = evaluate_joint_route_and_charging(
            origin="Origin",
            destination="Dest",
            vehicle_state=vehicle,
            charger_candidates=[cand_A, cand_B],
            graph=graph,
            ai_energy_predictor=route_aware_ai_predictor,
        )

        self.assertEqual(result_ai.decision_type, "CHARGE")
        # AI prediction successfully altered candidate preference to Charger B!
        self.assertEqual(result_ai.selected_charger_id, "CH_B")
        self.assertTrue(result_ai.ai_energy_prediction["used_for_planning"])

    # --------------------------------------------------------------------------
    # 6. Missing AI prediction gracefully falls back to baseline planning
    # --------------------------------------------------------------------------
    def test_6_missing_ai_prediction_graceful_fallback(self):
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=80.0)
        graph = RoutingGraph()
        graph.add_node("A", 0.0, 0.0)
        graph.add_node("B", 15.0, 0.0)
        graph.add_edge("A", "B", distance_km=15.0)

        # Test with predictor raising an exception
        def failing_predictor(ctx):
            raise RuntimeError("XGBoost artifact load failure")

        result = evaluate_joint_route_and_charging(
            origin="A",
            destination="B",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=failing_predictor,
        )

        self.assertEqual(result.decision_type, "DIRECT")
        self.assertIsNotNone(result.ai_energy_prediction)
        self.assertFalse(result.ai_energy_prediction["available"])
        self.assertFalse(result.ai_energy_prediction["used_for_planning"])
        self.assertIn("fallback", result.ai_energy_prediction["message"].lower())
        self.assertAlmostEqual(result.total_energy_kwh, 15.0 * 0.150, places=3)

    # --------------------------------------------------------------------------
    # 7. Deterministic repeated planning
    # --------------------------------------------------------------------------
    def test_7_deterministic_repeated_planning(self):
        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=80.0)
        graph = RoutingGraph()
        graph.add_node("A", 0.0, 0.0)
        graph.add_node("B", 25.0, 0.0)
        graph.add_edge("A", "B", distance_km=25.0)

        res1 = evaluate_joint_route_and_charging(
            origin="A",
            destination="B",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=self.energy_service.predict_trip_energy,
        )
        res2 = evaluate_joint_route_and_charging(
            origin="A",
            destination="B",
            vehicle_state=vehicle,
            charger_candidates=[],
            graph=graph,
            ai_energy_predictor=self.energy_service.predict_trip_energy,
        )

        self.assertEqual(res1.decision_type, res2.decision_type)
        self.assertAlmostEqual(res1.total_route_cost, res2.total_route_cost, places=5)
        self.assertAlmostEqual(
            res1.ai_energy_prediction["predicted_energy_kwh"],
            res2.ai_energy_prediction["predicted_energy_kwh"],
            places=5,
        )

    # --------------------------------------------------------------------------
    # 8. DIRECT workflow with AI prediction metadata via API
    # --------------------------------------------------------------------------
    def test_8_api_direct_workflow_with_ai_metadata(self):
        payload = {
            "vehicle_id": "EV_TEST_24",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.9784,
            "destination_longitude": 77.6408,
            "connector_type": "CCS2",
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "DIRECT")
        self.assertIn("ai_energy_prediction", data)
        ai_data = data["ai_energy_prediction"]
        self.assertIsNotNone(ai_data)
        self.assertTrue(ai_data["available"])
        self.assertEqual(ai_data["model"], "XGBoost")
        self.assertGreater(ai_data["predicted_energy_kwh"], 0.0)
        self.assertGreater(ai_data["baseline_energy_kwh"], 0.0)
        self.assertTrue(ai_data["used_for_planning"])
        self.assertFalse(ai_data["used_for_battery_state"])
        self.assertIn("energy_prediction", data["algorithm"])

    # --------------------------------------------------------------------------
    # 9. CHARGE workflow with AI prediction metadata via API
    # --------------------------------------------------------------------------
    def test_9_api_charge_workflow_with_ai_metadata(self):
        # Origin: (12.9716, 77.5946), Destination: (12.6500, 77.2000) ~50 km
        # 15% SoC of 30 kWh = 4.5 kWh, usable (15% - 5%) = 3.0 kWh (~20 km range)
        # Charger placed at (12.9000, 77.5000) ~12 km away (requires 1.8 kWh <= 3.0 kWh usable)
        payload = {
            "vehicle_id": "EV_TEST_24",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 15.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.6500,
            "destination_longitude": 77.2000,
            "connector_type": "CCS2",
            "target_soc_percent": 80.0,
            "candidate_chargers": [
                {
                    "charger_id": "CH_MIDWAY",
                    "name": "Midway 120kW Supercharger",
                    "latitude": 12.9000,
                    "longitude": 77.5000,
                    "charging_power_kw": 120.0,
                    "reliability": 0.98,
                    "probability_available": 0.90,
                    "charging_wait_minutes": 2.0,
                }
            ],
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "CHARGE")
        self.assertIsNotNone(data["selected_charger"])
        self.assertIn("ai_energy_prediction", data)
        ai_data = data["ai_energy_prediction"]
        self.assertIsNotNone(ai_data)
        self.assertTrue(ai_data["available"])
        self.assertEqual(ai_data["model"], "XGBoost")
        self.assertGreater(ai_data["predicted_energy_kwh"], 0.0)
        self.assertTrue(ai_data["used_for_planning"])
        self.assertFalse(ai_data["used_for_battery_state"])

    # --------------------------------------------------------------------------
    # 10. INFEASIBLE workflow with clean diagnostics
    # --------------------------------------------------------------------------
    def test_10_api_infeasible_workflow_clean_diagnostics(self):
        payload = {
            "vehicle_id": "EV_TEST_24",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 6.0,  # 1.8 kWh total, usable only 0.3 kWh (~2 km)
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
            "connector_type": "CCS2",
            "candidate_chargers": [],  # No chargers available
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertFalse(data["success"])
        self.assertEqual(data["decision_type"], "INFEASIBLE")
        self.assertIn("ai_energy_prediction", data)
        self.assertIn("explanation", data)
        self.assertIsNotNone(data["explanation"])


if __name__ == "__main__":
    unittest.main()

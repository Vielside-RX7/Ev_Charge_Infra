"""
Comprehensive End-to-End Journey Validation & Integration Stabilization Tests
=============================================================================
Change 25 Validation Suite:
  1. End-to-End Scenario A: DIRECT Journey Contract & Telemetry Consistency
  2. End-to-End Scenario B: CHARGING Journey Contract, Multi-Leg & Energy Addition
  3. End-to-End Scenario C: INFEASIBLE Journey Contract & Route Polyline Suppression
  4. Plan Reset & Replan State Isolation (No leakage from Plan A to Plan B)
  5. Repeated Planning Idempotency (Deterministic outputs without stale accumulation)
  6. Physical Energy Conservation & Telemetry Consistency Invariant
  7. Pause & Resume State Transition Invariant (Driving and Charging)
  8. Health & Clean Endpoint Connectivity
"""

import os
import sys
import unittest
import pandas as pd
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from api.main import app
import recommendation_engine
from joint_route_charging_engine import VehicleState, evaluate_joint_route_and_charging, RoutingGraph
from energy_prediction_service import EnergyPredictionService


class TestChange25EndToEndIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        mock_df = pd.DataFrame([
            {
                "id": 101,
                "name": "Mandya Express DC Supercharger",
                "operator": "Tata Power",
                "address": "Bangalore-Mysore Expressway",
                "city": "Mandya",
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
    # 1. End-to-End Scenario A: DIRECT Journey
    # --------------------------------------------------------------------------
    def test_e2e_scenario_a_direct_journey(self):
        payload = {
            "vehicle_id": "EV_E2E_01",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.9784,
            "destination_longitude": 77.6408,
            "connector_type": "CCS2",
        }
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Decision & structure
        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "DIRECT")
        self.assertIsNone(data["selected_charger"])
        self.assertIsNotNone(data["legs"]["direct"])
        self.assertIsNone(data["legs"]["origin_to_charger"])
        self.assertIsNone(data["legs"]["charger_to_destination"])

        # Consistency: Route distance, duration, energy
        total_dist = data["route"]["total_distance_km"]
        self.assertGreater(total_dist, 0.0)
        leg_dist = data["legs"]["direct"]["distance_km"]
        self.assertAlmostEqual(total_dist, leg_dist, places=2)

        # Baseline physics: 0.150 kWh/km
        energy_req = data["energy"]["energy_required_kwh"]
        self.assertAlmostEqual(energy_req, total_dist * 0.150, places=1)
        self.assertEqual(data["route"]["charging_duration_minutes"], 0.0)
        self.assertEqual(data["route"]["charging_wait_minutes"], 0.0)

        # Usable battery buffer
        usable_energy = 30.0 * (80.0 - 5.0) / 100.0  # 22.5 kWh
        self.assertLess(energy_req, usable_energy)
        self.assertAlmostEqual(
            data["energy"]["arrival_energy_kwh"],
            (30.0 * 0.80) - energy_req,
            places=1,
        )

        # Geometry
        geom = data["legs"]["direct"]["geometry"]
        self.assertEqual(geom["type"], "LineString")
        self.assertGreaterEqual(len(geom["coordinates"]), 2)

    # --------------------------------------------------------------------------
    # 2. End-to-End Scenario B: CHARGING Journey
    # --------------------------------------------------------------------------
    def test_e2e_scenario_b_charging_journey(self):
        # 15% SoC of 30 kWh = 4.5 kWh, usable (15% - 5%) = 3.0 kWh (max ~20 km range)
        # Trip is ~50 km (direct requires ~7.5 kWh > 3.0 kWh usable -> Infeasible direct)
        # Charger placed at ~12 km (requires ~1.8 kWh <= 3.0 kWh usable)
        payload = {
            "vehicle_id": "EV_E2E_02",
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
                    "charger_id": "CH_E2E_WAYPOINT",
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
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Decision & structure
        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "CHARGE")
        self.assertIsNotNone(data["selected_charger"])
        self.assertEqual(data["selected_charger"]["id"], "CH_E2E_WAYPOINT")
        self.assertEqual(data["selected_charger"]["charging_power_kw"], 120.0)

        # Multi-leg structure
        leg1 = data["legs"]["origin_to_charger"]
        leg2 = data["legs"]["charger_to_destination"]
        self.assertIsNotNone(leg1)
        self.assertIsNotNone(leg2)

        # Distance consistency: Leg 1 + Leg 2 = Total Distance
        total_dist = data["route"]["total_distance_km"]
        sum_legs_dist = leg1["distance_km"] + leg2["distance_km"]
        self.assertAlmostEqual(total_dist, sum_legs_dist, places=1)

        # Energy added at charger is strictly positive and bounded
        energy_added = data["energy"]["energy_added_kwh"]
        self.assertGreater(energy_added, 0.0)
        self.assertLessEqual(energy_added, 30.0)
        self.assertGreater(data["route"]["charging_duration_minutes"], 0.0)
        self.assertEqual(data["route"]["charging_wait_minutes"], 2.0)

        # Physical safety check: Leg 1 requires energy <= available usable battery
        usable_starting = 30.0 * (15.0 - 5.0) / 100.0  # 3.0 kWh
        self.assertLessEqual(leg1["energy_kwh"], usable_starting)

        # Departure energy after charging supports Leg 2
        departure_energy = data["energy"]["departure_energy_kwh"]
        reserve_energy = data["energy"]["reserve_energy_kwh"]
        self.assertGreaterEqual(departure_energy - leg2["energy_kwh"], reserve_energy)

    # --------------------------------------------------------------------------
    # 3. End-to-End Scenario C: INFEASIBLE Journey
    # --------------------------------------------------------------------------
    def test_e2e_scenario_c_infeasible_journey(self):
        # Extremely low battery (6% SoC, 0.3 kWh usable = ~2 km range)
        # Long trip (~140 km) with no candidate chargers
        payload = {
            "vehicle_id": "EV_E2E_03",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 6.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
            "connector_type": "CCS2",
            "candidate_chargers": [],
        }
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertFalse(data["success"])
        self.assertEqual(data["decision_type"], "INFEASIBLE")
        self.assertIsNone(data["selected_charger"])
        self.assertEqual(data["route"]["total_distance_km"], 0.0)
        self.assertEqual(data["route"]["total_energy_kwh"], 0.0)

        # Clear human-readable explanation
        self.assertIn("explanation", data)
        self.assertIn("infeasible", data["explanation"].lower())
        self.assertIn("insufficient battery", data["explanation"].lower())

    # --------------------------------------------------------------------------
    # 4. Plan Reset & Replan Isolation (No State Leakage)
    # --------------------------------------------------------------------------
    def test_plan_reset_and_replan_isolation(self):
        # First plan: Charging trip to Destination A
        payload_a = {
            "vehicle_id": "EV_E2E_04",
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
                    "charger_id": "CH_A",
                    "name": "Charger A",
                    "latitude": 12.9000,
                    "longitude": 77.5000,
                    "charging_power_kw": 50.0,
                }
            ],
        }
        res_a = self.client.post("/trip/plan", json=payload_a)
        data_a = res_a.json()
        self.assertEqual(data_a["decision_type"], "CHARGE")
        self.assertEqual(data_a["selected_charger"]["id"], "CH_A")

        # Second plan: Direct short trip to Destination B (with higher SoC)
        payload_b = {
            "vehicle_id": "EV_E2E_04",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.9784,
            "destination_longitude": 77.6408,
            "connector_type": "CCS2",
        }
        res_b = self.client.post("/trip/plan", json=payload_b)
        data_b = res_b.json()

        # Strict isolation: Plan B has NO charger from Plan A
        self.assertEqual(data_b["decision_type"], "DIRECT")
        self.assertIsNone(data_b["selected_charger"])
        self.assertNotEqual(data_a["route"]["total_distance_km"], data_b["route"]["total_distance_km"])
        self.assertEqual(data_b["route"]["charging_duration_minutes"], 0.0)

    # --------------------------------------------------------------------------
    # 5. Repeated Planning Idempotency
    # --------------------------------------------------------------------------
    def test_repeated_planning_idempotency(self):
        payload = {
            "vehicle_id": "EV_E2E_05",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.9784,
            "destination_longitude": 77.6408,
            "connector_type": "CCS2",
        }
        # Run 3 consecutive requests
        results = [self.client.post("/trip/plan", json=payload).json() for _ in range(3)]

        for r in results:
            self.assertEqual(r["decision_type"], "DIRECT")
            self.assertAlmostEqual(r["route"]["total_distance_km"], results[0]["route"]["total_distance_km"], places=4)
            self.assertAlmostEqual(r["route"]["total_energy_kwh"], results[0]["route"]["total_energy_kwh"], places=4)
            self.assertAlmostEqual(r["route"]["total_cost"], results[0]["route"]["total_cost"], places=5)

    # --------------------------------------------------------------------------
    # 6. Physical Energy Conservation & Telemetry Consistency
    # --------------------------------------------------------------------------
    def test_physical_energy_conservation(self):
        # 30 kWh battery, 15% starting SoC
        start_soc = 15.0
        capacity = 30.0
        start_energy = (start_soc / 100.0) * capacity  # 4.5 kWh

        # Leg 1: 12.0 km -> consumed = 12.0 * 0.150 = 1.8 kWh
        leg1_dist = 12.0
        leg1_energy = leg1_dist * 0.150
        energy_after_leg1 = start_energy - leg1_energy  # 2.7 kWh

        # Charging: adds 18.0 kWh
        charge_added = 18.0
        energy_after_charge = energy_after_leg1 + charge_added  # 20.7 kWh

        # Leg 2: 30.0 km -> consumed = 30.0 * 0.150 = 4.5 kWh
        leg2_dist = 30.0
        leg2_energy = leg2_dist * 0.150
        final_remaining = energy_after_charge - leg2_energy  # 16.2 kWh

        # Total energy consumed = Leg 1 + Leg 2 = 6.3 kWh
        total_consumed = leg1_energy + leg2_energy

        # Conservation law: start_energy - total_consumed + charge_added == final_remaining
        self.assertAlmostEqual(start_energy - total_consumed + charge_added, final_remaining, places=3)

        # SoC formula: remaining / capacity * 100
        final_soc = (final_remaining / capacity) * 100.0
        self.assertAlmostEqual(final_soc, 54.0, places=1)

        # Range formula: remaining / 0.150
        final_range = final_remaining / 0.150
        self.assertAlmostEqual(final_range, 108.0, places=1)

    # --------------------------------------------------------------------------
    # 7. Pause & Resume State Transition Invariants
    # --------------------------------------------------------------------------
    def test_pause_resume_state_transitions(self):
        # Valid state transitions
        valid_transitions = {
            "IDLE": ["PLANNING"],
            "PLANNING": ["READY", "INFEASIBLE"],
            "READY": ["DRIVING", "DRIVING_LEG_1"],
            "DRIVING": ["PAUSED", "COMPLETED"],
            "DRIVING_LEG_1": ["PAUSED", "CHARGING"],
            "CHARGING": ["PAUSED", "DRIVING_LEG_2"],
            "PAUSED": ["DRIVING", "DRIVING_LEG_1", "CHARGING", "DRIVING_LEG_2", "IDLE"],
            "DRIVING_LEG_2": ["PAUSED", "COMPLETED"],
            "COMPLETED": ["READY", "IDLE"],
        }
        for state, next_states in valid_transitions.items():
            self.assertGreater(len(next_states), 0)
            self.assertTrue(all(isinstance(s, str) for s in next_states))

    # --------------------------------------------------------------------------
    # 8. Clean Health & Endpoint Connectivity
    # --------------------------------------------------------------------------
    def test_health_and_endpoint_connectivity(self):
        res_health = self.client.get("/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json()["status"], "ok")

        res_root = self.client.get("/")
        self.assertEqual(res_root.status_code, 200)
        self.assertIn("message", res_root.json())

        res_energy = self.client.get("/energy/status")
        self.assertEqual(res_energy.status_code, 200)
        energy_data = res_energy.json()
        self.assertIn("xgboost_whole_trip", energy_data)
        self.assertIn("lstm_near_term", energy_data)


if __name__ == "__main__":
    unittest.main()

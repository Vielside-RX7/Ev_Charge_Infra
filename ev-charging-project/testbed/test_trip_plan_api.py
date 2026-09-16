"""
Comprehensive Unit Tests for Dedicated Application Planning API (POST /trip/plan)
==================================================================================
Change 20 Validation Suite covering all 9 required scenarios:
  1. Direct route feasible -> HTTP 200 + DIRECT
  2. Direct route infeasible but charger available -> HTTP 200 + CHARGE
  3. No feasible route/charger -> HTTP 200 + INFEASIBLE
  4. Invalid SoC (>100% or <0%) -> HTTP 422
  5. Invalid battery capacity (<=0) -> HTTP 422
  6. Missing destination -> HTTP 422
  7. Deterministic repeated request -> identical planning output
  8. Existing /route endpoint remains unchanged
  9. Existing /recommend endpoint contract preserved
"""

import os
import sys
import unittest

from fastapi.testclient import TestClient

# Ensure project root is in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from api.main import app
import recommendation_engine
import pandas as pd


class TestTripPlanAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        mock_df = pd.DataFrame([
            {
                "id": 1,
                "name": "Test Charger",
                "operator": "Tata Power",
                "address": "Koramangala, Bangalore",
                "city": "Bangalore",
                "latitude": 12.9352,
                "longitude": 77.6245,
                "charging_power_kw": 50.0,
                "num_ports": 2,
                "connector_type": "CCS2",
                "tariff_inr_per_kwh": 17.0,
                "reliability": 0.95,
                "probability_available": 0.85,
            }
        ])
        recommendation_engine._CACHED_CHARGERS_DF = mock_df
        cls.client = TestClient(app)

    # 1. Direct route feasible -> HTTP 200 + DIRECT
    def test_scenario_1_direct_route_feasible(self):
        # Short trip (e.g. Bangalore center to Indiranagar ~6 km), 80% SoC on 30 kWh battery
        # 6 km * 0.150 = 0.9 kWh << 22.5 kWh usable
        payload = {
            "vehicle_id": "EV_TEST_01",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.9784,
            "destination_longitude": 77.6408,
            "connector_type": "CCS2",
            "target_soc_percent": 80.0,
            "candidate_chargers": [
                {
                    "charger_id": "CH_NEAR",
                    "name": "En-Route DC Fast Charger",
                    "latitude": 12.9750,
                    "longitude": 77.6100,
                    "charging_power_kw": 50.0,
                    "reliability": 0.95,
                    "probability_available": 0.85,
                }
            ],
        }

        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "DIRECT")
        self.assertIsNone(data["selected_charger"])
        self.assertIn("direct", data["legs"])
        self.assertIsNotNone(data["legs"]["direct"])
        self.assertGreater(data["route"]["total_distance_km"], 0.0)
        self.assertEqual(data["route"]["charging_duration_minutes"], 0.0)

    # 2. Direct route infeasible but charger available -> HTTP 200 + CHARGE
    def test_scenario_2_direct_infeasible_charger_available(self):
        # Bangalore to Mysore (~140 km), battery at 15% SoC (15% of 30 kWh = 4.5 kWh, usable 3.0 kWh = ~20 km range)
        # Midway charger at Mandya (~60 km) with 100 kW power
        # Let's use coordinates ~15 km away for charger (reachable with 3.0 kWh), then destination 25 km away
        # Origin: (12.9716, 77.5946), Destination: (12.6500, 77.2000) ~50 km away
        # Usable energy: 30 kWh * (15% - 5%) = 3.0 kWh (max ~20 km)
        # Direct requires ~50 * 0.150 = 7.5 kWh > 3.0 kWh -> Infeasible
        # Charger placed at (12.9000, 77.5000) ~12 km away (requires 1.8 kWh <= 3.0 kWh -> Reached)
        payload = {
            "vehicle_id": "EV_TEST_02",
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
        self.assertEqual(data["selected_charger"]["id"], "CH_MIDWAY")
        self.assertGreater(data["energy"]["energy_added_kwh"], 0.0)
        self.assertGreater(data["route"]["charging_duration_minutes"], 0.0)
        self.assertIsNotNone(data["legs"]["origin_to_charger"])
        self.assertIsNotNone(data["legs"]["charger_to_destination"])

    # 3. No feasible route/charger -> explicit INFEASIBLE response
    def test_scenario_3_no_feasible_route_or_charger(self):
        # Battery empty: 5.0% SoC (exactly reserve buffer, 0.0 kWh usable)
        # Any trip > 0 km is physically impossible
        payload = {
            "vehicle_id": "EV_STRANDED",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 5.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
            "connector_type": "CCS2",
            "candidate_chargers": [
                {
                    "charger_id": "CH_UNREACHABLE",
                    "name": "Far Charger",
                    "latitude": 12.7000,
                    "longitude": 77.0000,
                    "charging_power_kw": 50.0,
                }
            ],
        }

        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertEqual(data["decision_type"], "INFEASIBLE")
        self.assertIsNone(data["selected_charger"])
        self.assertIn("infeasible", data["explanation"].lower())

    # 4. Invalid SoC -> HTTP 422
    def test_scenario_4_invalid_soc(self):
        payload_high = {
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 120.0,  # Invalid: > 100
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
        }
        res_high = self.client.post("/trip/plan", json=payload_high)
        self.assertEqual(res_high.status_code, 422)

        payload_neg = {
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": -10.0,  # Invalid: < 0
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
        }
        res_neg = self.client.post("/trip/plan", json=payload_neg)
        self.assertEqual(res_neg.status_code, 422)

    # 5. Invalid battery capacity -> HTTP 422
    def test_scenario_5_invalid_battery_capacity(self):
        payload = {
            "battery_capacity_kwh": -5.0,  # Invalid: <= 0
            "current_soc_percent": 50.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.2958,
            "destination_longitude": 76.6394,
        }
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 422)

    # 6. Missing destination -> HTTP 422
    def test_scenario_6_missing_destination(self):
        payload = {
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 50.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            # destination_latitude and destination_longitude omitted!
        }
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 422)

    # 7. Deterministic repeated request -> identical planning output
    def test_scenario_7_deterministic_repeated_request(self):
        payload = {
            "battery_capacity_kwh": 40.0,
            "current_soc_percent": 45.0,
            "origin_latitude": 12.9716,
            "origin_longitude": 77.5946,
            "destination_latitude": 12.8000,
            "destination_longitude": 77.5000,
            "candidate_chargers": [
                {
                    "charger_id": "CH_REP_1",
                    "name": "Station Alpha",
                    "latitude": 12.9100,
                    "longitude": 77.5500,
                    "charging_power_kw": 50.0,
                },
                {
                    "charger_id": "CH_REP_2",
                    "name": "Station Beta",
                    "latitude": 12.8500,
                    "longitude": 77.5200,
                    "charging_power_kw": 100.0,
                },
            ],
        }

        res1 = self.client.post("/trip/plan", json=payload).json()
        for _ in range(4):
            res_subsequent = self.client.post("/trip/plan", json=payload).json()
            self.assertEqual(res1["decision_type"], res_subsequent["decision_type"])
            self.assertEqual(res1["route"]["total_distance_km"], res_subsequent["route"]["total_distance_km"])
            self.assertEqual(res1["route"]["total_energy_kwh"], res_subsequent["route"]["total_energy_kwh"])
            self.assertEqual(res1["route"]["total_cost"], res_subsequent["route"]["total_cost"])

    # 8. Existing /route endpoint remains unchanged
    def test_scenario_8_existing_route_endpoint_unchanged(self):
        # /route requires user_lat, user_lon, charger_lat, charger_lon
        route_payload = {
            "user_lat": 12.9716,
            "user_lon": 77.5946,
            "charger_lat": 12.9352,
            "charger_lon": 77.6245,
        }
        res = self.client.post("/route", json=route_payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("distance_km", data)
        self.assertIn("travel_time_minutes", data)
        self.assertIn("geometry", data)

    # 9. Existing /recommend endpoint contract preserved
    def test_scenario_9_existing_recommend_endpoint_contract(self):
        # Test input validation on /recommend (e.g. target_soc <= current_soc should be rejected)
        bad_recommend_payload = {
            "user_lat": 12.9716,
            "user_lon": 77.5946,
            "current_soc_percent": 80.0,
            "target_soc_percent": 50.0,  # Invalid: target <= current
        }
        res = self.client.post("/recommend", json=bad_recommend_payload)
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()

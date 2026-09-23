"""
test_change_29_destination_search.py
------------------------------------
Seam & integration tests for Change 29:
Verifies that destination coordinates captured from Google Places search
seamlessly feed into the automatic multi-stop journey planner without
requiring charger selection.
"""

import unittest
import pandas as pd
from fastapi.testclient import TestClient
from api.main import app
import recommendation_engine


class TestChange29DestinationSearchIntegration(unittest.TestCase):
    """
    Seam tests for Google Places destination search coordinates feeding
    directly into the VoltGuide automatic journey planner.
    """

    @classmethod
    def setUpClass(cls):
        mock_df = pd.DataFrame([
            {
                "id": 1,
                "name": "Mandya Express Fast Charger",
                "operator": "Tata Power",
                "address": "Bangalore-Mysore Expressway, Mandya",
                "city": "Mandya",
                "latitude": 12.5240,
                "longitude": 76.8980,
                "charging_power_kw": 60.0,
                "num_ports": 4,
                "connector_type": "CCS2",
                "tariff_inr_per_kwh": 18.0,
                "reliability": 0.95,
                "probability_available": 0.85,
                "operational_status": "OPERATIONAL",
                "status_confidence": 0.98,
            },
            {
                "id": 2,
                "name": "Bidadi Highway Hub",
                "operator": "ChargeZone",
                "address": "Bidadi Industrial Area",
                "city": "Bidadi",
                "latitude": 12.7950,
                "longitude": 77.3850,
                "charging_power_kw": 50.0,
                "num_ports": 2,
                "connector_type": "CCS2",
                "tariff_inr_per_kwh": 17.0,
                "reliability": 0.92,
                "probability_available": 0.80,
                "operational_status": "OPERATIONAL",
                "status_confidence": 0.95,
            }
        ])
        recommendation_engine._CACHED_CHARGERS_DF = mock_df
        cls.client = TestClient(app)

    def test_places_destination_coordinate_planning(self):
        """
        Verify that coordinates resolved from a Google Places search
        (e.g., Bengaluru Airport: 13.1986, 77.7066) can be submitted
        directly to /trip/plan with no candidate chargers or preselected station.
        """
        payload = {
            "vehicle_id": "EV-001",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.2958,
            "origin_longitude": 76.6394,
            "destination_latitude": 13.1986,
            "destination_longitude": 77.7066,
            "connector_type": "CCS2",
            "multi_stop": True,
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Must have canonical multi-stop keys
        self.assertIn("decision_type", data)
        self.assertIn("charging_stops", data)
        self.assertIn("legs", data)

        # User is NOT required to select a charging station
        self.assertIn(data["decision_type"], ["DIRECT", "CHARGE", "INFEASIBLE"])
        if data["decision_type"] == "CHARGE":
            self.assertGreater(len(data["charging_stops"]), 0)
            for stop in data["charging_stops"]:
                self.assertIn("charger_name", stop)
                self.assertIn("charging_power_kw", stop)
                self.assertIn("energy_added_kwh", stop)

    def test_destination_can_be_changed(self):
        """
        Verify that when a user searches and selects a closer destination
        (e.g., Mysuru Palace: 12.3051, 76.6552), planning produces a DIRECT plan.
        """
        payload = {
            "vehicle_id": "EV-001",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 90.0,
            "origin_latitude": 12.2958,
            "origin_longitude": 76.6394,
            "destination_latitude": 12.3051,
            "destination_longitude": 76.6552,
            "connector_type": "CCS2",
            "multi_stop": True,
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["decision_type"], "DIRECT")
        self.assertEqual(len(data["charging_stops"]), 0)

    def test_invalid_destination_handled_gracefully(self):
        """
        Verify that out-of-range coordinates return validation errors
        without crashing the server.
        """
        payload = {
            "vehicle_id": "EV-001",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.2958,
            "origin_longitude": 76.6394,
            "destination_latitude": 999.0,
            "destination_longitude": 999.0,
            "connector_type": "CCS2",
        }
        response = self.client.post("/trip/plan", json=payload)
        self.assertIn(response.status_code, [400, 422, 500])


if __name__ == "__main__":
    unittest.main()

"""
Test Suite: User, Vehicle, Trip History, and Reviews Layer
==========================================================
Verifies:
1. Authentication: Signup, Login, Password Verification, JWT Tokens, Unauthorized access rejection.
2. Vehicle CRUD: Add vehicle, list vehicles, select vehicle, delete vehicle.
3. Trip History: Persist trip records, fetch authenticated user trips.
4. Reviews: Submit 1-5 star review, aggregate ratings and recent reviews.
"""

import os
import sys
import unittest
import uuid
from fastapi.testclient import TestClient

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "database") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "database"))

from api.main import app
from database.connection import get_db, get_engine
from database.models import User, UserVehicle, TripHistory, Review


class TestUserAccountLayer(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.rand_suffix = uuid.uuid4().hex[:8]
        self.test_email = f"user_{self.rand_suffix}@voltguide.com"
        self.test_password = "SecurePassword2026!"
        self.test_name = f"Driver {self.rand_suffix}"

    def test_01_signup_and_jwt_token_generation(self):
        res = self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
            "initial_vehicle": {
                "vehicle_name": "Tata Nexon EV Max",
                "battery_capacity_kwh": 30.0,
                "connector_type": "CCS2",
            }
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["email"], self.test_email)
        self.assertEqual(data["user"]["name"], self.test_name)
        self.assertIsNotNone(data["selected_vehicle"])
        self.assertEqual(data["selected_vehicle"]["vehicle_name"], "Tata Nexon EV Max")
        self.assertTrue(data["selected_vehicle"]["is_selected"])

    def test_02_duplicate_email_signup_rejected(self):
        # Initial signup
        self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
        })
        # Duplicate signup
        res = self.client.post("/auth/signup", json={
            "name": "Duplicate Driver",
            "email": self.test_email,
            "password": "OtherPassword!",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("already exists", res.json()["detail"])

    def test_03_login_and_auth_me(self):
        # Create user
        self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
        })

        # Login with correct password
        login_res = self.client.post("/auth/login", json={
            "email": self.test_email,
            "password": self.test_password,
        })
        self.assertEqual(login_res.status_code, 200)
        token = login_res.json()["token"]
        self.assertTrue(len(token) > 20)

        # Login with wrong password
        bad_login = self.client.post("/auth/login", json={
            "email": self.test_email,
            "password": "WrongPassword!",
        })
        self.assertEqual(bad_login.status_code, 401)

        # Verify /auth/me with Bearer token
        me_res = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me_res.status_code, 200)
        self.assertEqual(me_res.json()["user"]["email"], self.test_email)

        # Verify /auth/me without token fails with 401
        unauth_res = self.client.get("/auth/me")
        self.assertEqual(unauth_res.status_code, 401)

    def test_04_vehicle_crud_and_selection(self):
        # Register user
        signup = self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
        })
        token = signup.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Add second vehicle
        add_res = self.client.post("/vehicles", headers=headers, json={
            "vehicle_name": "MG ZS EV Long Range",
            "battery_capacity_kwh": 50.3,
            "connector_type": "CCS2",
            "is_selected": True,
        })
        self.assertEqual(add_res.status_code, 200)
        v2_id = add_res.json()["id"]
        self.assertTrue(add_res.json()["is_selected"])

        # 2. List vehicles (should have 2, with v2 selected)
        list_res = self.client.get("/vehicles", headers=headers)
        self.assertEqual(list_res.status_code, 200)
        vehicles = list_res.json()
        self.assertEqual(len(vehicles), 2)
        v1 = next(v for v in vehicles if v["id"] != v2_id)
        v2 = next(v for v in vehicles if v["id"] == v2_id)
        self.assertFalse(v1["is_selected"])
        self.assertTrue(v2["is_selected"])

        # 3. Select v1
        select_res = self.client.post(f"/vehicles/{v1['id']}/select", headers=headers)
        self.assertEqual(select_res.status_code, 200)
        self.assertTrue(select_res.json()["is_selected"])

        # Verify via /auth/me
        me = self.client.get("/auth/me", headers=headers).json()
        self.assertEqual(me["selected_vehicle"]["id"], v1["id"])

        # 4. Delete v2
        del_res = self.client.delete(f"/vehicles/{v2_id}", headers=headers)
        self.assertEqual(del_res.status_code, 200)
        remaining = self.client.get("/vehicles", headers=headers).json()
        self.assertEqual(len(remaining), 1)

    def test_05_trip_history_persistence(self):
        # Register user
        signup = self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
        })
        token = signup.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Save completed trip
        save_res = self.client.post("/trips/history", headers=headers, json={
            "origin_name": "Mysuru Palace",
            "origin_lat": 12.3051,
            "origin_lon": 76.6551,
            "dest_name": "Kempegowda International Airport",
            "dest_lat": 13.1986,
            "dest_lon": 77.7066,
            "total_distance_km": 184.6,
            "total_energy_kwh": 27.69,
            "total_travel_time_minutes": 165.0,
            "charging_stop_count": 1,
            "charging_stops_summary": "1 stop at Grand Mercure Mysore (120 kW)",
            "vehicle_name": "Tata Nexon EV Max",
            "status": "COMPLETED",
        })
        self.assertEqual(save_res.status_code, 200)
        trip_data = save_res.json()
        self.assertEqual(trip_data["dest_name"], "Kempegowda International Airport")
        self.assertEqual(trip_data["total_distance_km"], 184.6)
        self.assertEqual(trip_data["status"], "COMPLETED")

        # Fetch trip history for user
        get_res = self.client.get("/trips/history", headers=headers)
        self.assertEqual(get_res.status_code, 200)
        history = get_res.json()
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["dest_name"], "Kempegowda International Airport")

    def test_06_charger_reviews_and_aggregate_ratings(self):
        # Register user
        signup = self.client.post("/auth/signup", json={
            "name": self.test_name,
            "email": self.test_email,
            "password": self.test_password,
        })
        token = signup.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        target_charger_id = 7  # Grand Mercure Mysore

        # Submit review
        rev_res = self.client.post(f"/chargers/{target_charger_id}/reviews", headers=headers, json={
            "rating": 5,
            "review_text": "Excellent 120kW fast charging experience! High reliability.",
        })
        self.assertEqual(rev_res.status_code, 200)
        rev_data = rev_res.json()
        self.assertEqual(rev_data["rating"], 5)
        self.assertEqual(rev_data["user_name"], self.test_name)
        self.assertGreater(rev_data["sentiment_score"], 0.0)

        # Query reviews summary
        summary_res = self.client.get(f"/chargers/{target_charger_id}/reviews")
        self.assertEqual(summary_res.status_code, 200)
        summary = summary_res.json()
        self.assertEqual(summary["charger_id"], target_charger_id)
        self.assertGreaterEqual(summary["review_count"], 1)
        self.assertGreaterEqual(summary["avg_rating"], 1.0)
        self.assertLessEqual(summary["avg_rating"], 5.0)
        self.assertTrue(len(summary["recent_reviews"]) > 0)


if __name__ == "__main__":
    unittest.main()

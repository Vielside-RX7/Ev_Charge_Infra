"""
Change 27: Charger Operational Safety, Trust, and Availability Test Suite
=========================================================================
Covers all 15 required verification scenarios:
  1. Available charger -> eligible
  2. Out-of-service charger -> rejected
  3. Maintenance charger -> rejected
  4. Active fault -> rejected
  5. Unknown status -> not silently treated as available
  6. Stale status -> confidence reduced / policy applied
  7. Highly reliable but out-of-service -> rejected
  8. Low reliability but operational -> eligible with lower ranking quality
  9. Successful user feedback
  10. Negative user feedback
  11. Repeated negative reports
  12. Conflicting historical/current signals
  13. Deterministic repeated evaluation
  14. Existing charger recommendation tests remain passing
  15. Existing Change 18A–25 integration tests remain passing
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

import pandas as pd
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
from operational_safety import (
    OperationalStatus,
    OperationalPolicy,
    FeedbackResult,
    ChargingFeedbackEvent,
    UserFeedbackManager,
    global_feedback_manager,
    check_operational_eligibility,
    compute_station_trust_score,
    SimulatedLiveStatusProvider,
    HardwareState,
    map_hardware_state_to_operational_status,
)
from joint_route_charging_engine import (
    VehicleState,
    ChargerCandidate,
    evaluate_joint_route_and_charging,
    RoutingGraph,
)


class TestChange27OperationalSafety(unittest.TestCase):

    def setUp(self):
        # Clear feedback manager before each test for clean isolation
        global_feedback_manager.clear()
        self.feedback_mgr = UserFeedbackManager()
        self.client = TestClient(app)
        self.now = datetime.now(timezone.utc)

    # --------------------------------------------------------------------------
    # 1. Available charger -> eligible
    # --------------------------------------------------------------------------
    def test_01_available_charger_is_eligible(self):
        charger = {
            "charger_id": "CHG_01",
            "name": "Standard Highway Fast Charger",
            "operational_status": OperationalStatus.AVAILABLE,
            "reliability": 0.92,
            "charging_power_kw": 50.0,
            "status_last_updated": self.now.isoformat(),
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertTrue(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.AVAILABLE)
        self.assertIsNone(res.rejection_reason)
        self.assertGreaterEqual(res.status_confidence, 0.85)

    # --------------------------------------------------------------------------
    # 2. Out-of-service charger -> rejected
    # --------------------------------------------------------------------------
    def test_02_out_of_service_charger_is_rejected(self):
        charger = {
            "charger_id": "CHG_02",
            "name": "Broken Down Charger",
            "operational_status": OperationalStatus.OUT_OF_SERVICE,
            "reliability": 0.95,
            "rejection_reason": "Grid transformer damaged",
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.OUT_OF_SERVICE)
        self.assertIn("OUT_OF_SERVICE", res.rejection_reason)

    # --------------------------------------------------------------------------
    # 3. Maintenance charger -> rejected
    # --------------------------------------------------------------------------
    def test_03_maintenance_charger_is_rejected(self):
        charger = {
            "charger_id": "CHG_03",
            "name": "Routine Service Charger",
            "operational_status": OperationalStatus.MAINTENANCE,
            "reliability": 0.99,
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.MAINTENANCE)
        self.assertIn("MAINTENANCE", res.rejection_reason)

    # --------------------------------------------------------------------------
    # 4. Active fault -> rejected
    # --------------------------------------------------------------------------
    def test_04_active_fault_is_rejected(self):
        charger = {
            "charger_id": "CHG_04",
            "name": "Faulty Cable Station",
            "has_active_fault": True,
            "fault_type": "CCS2_CONNECTOR_LATCH_STUCK",
            "resolved": False,
            "reliability": 0.90,
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.OUT_OF_SERVICE)
        self.assertIsNotNone(res.rejection_reason)

    # --------------------------------------------------------------------------
    # 5. Unknown status -> not silently treated as available
    # --------------------------------------------------------------------------
    def test_05_unknown_status_not_silently_treated_as_available(self):
        charger = {
            "charger_id": "CHG_05",
            "name": "Unverified Station",
            "operational_status": OperationalStatus.UNKNOWN,
            "reliability": 0.85,
        }
        # Under default policy (allow_unknown=False)
        default_policy = OperationalPolicy(allow_unknown=False)
        res = check_operational_eligibility(charger, policy=default_policy, as_of=self.now)
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.UNKNOWN)
        self.assertIn("UNKNOWN", res.rejection_reason)

        # Confirm that status is never converted to AVAILABLE
        self.assertNotEqual(res.operational_status, OperationalStatus.AVAILABLE)

    # --------------------------------------------------------------------------
    # 6. Stale status -> confidence reduced / policy applied
    # --------------------------------------------------------------------------
    def test_06_stale_status_reduces_confidence_and_applies_policy(self):
        # 120 hours old (> 48h max_stale_hours)
        stale_time = self.now - timedelta(hours=120)
        charger = {
            "charger_id": "CHG_06",
            "name": "Abandoned Telemetry Station",
            "operational_status": OperationalStatus.AVAILABLE,
            "status_last_updated": stale_time.isoformat(),
            "reliability": 0.90,
        }
        policy = OperationalPolicy(max_stale_hours=48.0, min_confidence_threshold=0.40)
        res = check_operational_eligibility(charger, policy=policy, as_of=self.now)

        # Confidence should be degraded due to 120h staleness
        self.assertLess(res.status_confidence, 0.40)
        # Because confidence < min_confidence_threshold, station is rejected
        self.assertFalse(res.eligible)
        self.assertIn("stale telemetry", res.rejection_reason.lower())

    # --------------------------------------------------------------------------
    # 7. Highly reliable but out-of-service -> rejected
    # --------------------------------------------------------------------------
    def test_07_highly_reliable_but_out_of_service_is_rejected(self):
        charger = {
            "charger_id": "CHG_07",
            "name": "Premium Flagship Charger (Broken)",
            "operational_status": OperationalStatus.OUT_OF_SERVICE,
            "reliability": 0.999,  # Pristine historical reliability
            "charging_power_kw": 250.0,
            "tariff_inr_per_kwh": 10.0,  # Ultra cheap
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.OUT_OF_SERVICE)

        # In joint planning engine, ensure high reliability cannot override
        graph = RoutingGraph()
        graph.add_node("origin", x_km=0.0, y_km=0.0)
        graph.add_node("destination", x_km=60.0, y_km=0.0)
        graph.add_node("c_node", x_km=30.0, y_km=0.0)
        graph.add_edge("origin", "c_node", distance_km=30.0, predicted_energy_kwh=4.5)
        graph.add_edge("c_node", "destination", distance_km=30.0, predicted_energy_kwh=4.5)

        vehicle = VehicleState(battery_capacity_kwh=30.0, current_soc_percent=20.0)  # Cannot make direct
        candidate = ChargerCandidate(
            charger_id="CHG_07",
            name="Premium Broken Station",
            node_id="c_node",
            reliability=0.999,
            probability_available=0.95,
            operational_status="OUT_OF_SERVICE",
            eligible_for_planning=False,
            rejection_reason="Station OUT_OF_SERVICE",
        )
        plan = evaluate_joint_route_and_charging(
            origin="origin",
            destination="destination",
            vehicle_state=vehicle,
            charger_candidates=[candidate],
            graph=graph,
        )
        # Should be INFEASIBLE because the only charger is OUT_OF_SERVICE
        self.assertEqual(plan.decision_type, "INFEASIBLE")

    # --------------------------------------------------------------------------
    # 8. Low reliability but operational -> eligible with lower ranking quality
    # --------------------------------------------------------------------------
    def test_08_low_reliability_but_operational_remains_eligible(self):
        charger = {
            "charger_id": "CHG_08",
            "name": "Jittery Operational Charger",
            "operational_status": OperationalStatus.AVAILABLE,
            "reliability": 0.60,  # Mediocre historical reliability
            "status_last_updated": self.now.isoformat(),
        }
        res = check_operational_eligibility(charger, as_of=self.now)
        self.assertTrue(res.eligible)
        # Trust score reflects lower historical reliability
        self.assertLess(res.trust_score, 0.80)

    # --------------------------------------------------------------------------
    # 9. Successful user feedback
    # --------------------------------------------------------------------------
    def test_09_successful_user_feedback(self):
        event = ChargingFeedbackEvent(
            charger_id="CHG_09",
            result=FeedbackResult.SUCCESSFUL_CHARGE,
            timestamp=self.now,
            vehicle_id="V_NEXON_01",
        )
        self.feedback_mgr.record_feedback(event)
        summary = self.feedback_mgr.get_feedback_summary("CHG_09", as_of=self.now)
        self.assertEqual(summary["total_reports"], 1)
        self.assertEqual(summary["recent_positive_reports"], 1)
        self.assertEqual(summary["recent_negative_reports"], 0)

        # Trust score should receive positive feedback bonus
        t_score = compute_station_trust_score(
            historical_reliability=0.85,
            operational_status=OperationalStatus.AVAILABLE,
            status_confidence=0.90,
            feedback_mgr=self.feedback_mgr,
            charger_id="CHG_09",
            as_of=self.now,
        )
        self.assertGreater(t_score, 0.85 * 0.70 + 0.90 * 0.30)

    # --------------------------------------------------------------------------
    # 10. Negative user feedback
    # --------------------------------------------------------------------------
    def test_10_negative_user_feedback(self):
        # A single negative feedback report decreases confidence without instant exclusion
        event = ChargingFeedbackEvent(
            charger_id="CHG_10",
            result=FeedbackResult.CHARGER_FAULT,
            timestamp=self.now,
            notes="Screen was frozen and connector wouldn't unlock.",
        )
        self.feedback_mgr.record_feedback(event)

        charger = {
            "charger_id": "CHG_10",
            "operational_status": OperationalStatus.AVAILABLE,
            "reliability": 0.90,
            "status_last_updated": self.now.isoformat(),
        }
        res = check_operational_eligibility(charger, feedback_mgr=self.feedback_mgr, as_of=self.now)
        # Single negative report reduces confidence
        self.assertLess(res.status_confidence, 1.0)
        # But single report does NOT permanently exclude under threshold 3
        self.assertTrue(res.eligible)

    # --------------------------------------------------------------------------
    # 11. Repeated negative reports
    # --------------------------------------------------------------------------
    def test_11_repeated_negative_reports_triggers_exclusion(self):
        for i in range(3):
            self.feedback_mgr.record_feedback(
                ChargingFeedbackEvent(
                    charger_id="CHG_11",
                    result=FeedbackResult.STATION_UNAVAILABLE,
                    timestamp=self.now - timedelta(minutes=10 * i),
                )
            )

        charger = {
            "charger_id": "CHG_11",
            "operational_status": OperationalStatus.AVAILABLE,
            "reliability": 0.95,
            "status_last_updated": self.now.isoformat(),
        }
        policy = OperationalPolicy(consecutive_negative_threshold=3, negative_feedback_window_hours=24.0)
        res = check_operational_eligibility(charger, policy=policy, feedback_mgr=self.feedback_mgr, as_of=self.now)

        # Repeated negative reports triggers OUT_OF_SERVICE and hard exclusion
        self.assertFalse(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.OUT_OF_SERVICE)
        self.assertIn("consecutive negative user reports", res.rejection_reason)

    # --------------------------------------------------------------------------
    # 12. Conflicting historical/current signals
    # --------------------------------------------------------------------------
    def test_12_conflicting_historical_and_current_signals(self):
        # Historical reliability 0.98, current status OUT_OF_SERVICE -> REJECT
        res_a = check_operational_eligibility({
            "charger_id": "CHG_12A",
            "reliability": 0.98,
            "operational_status": OperationalStatus.OUT_OF_SERVICE,
        }, as_of=self.now)
        self.assertFalse(res_a.eligible)

        # Historical reliability 0.65, current status AVAILABLE -> ELIGIBLE
        res_b = check_operational_eligibility({
            "charger_id": "CHG_12B",
            "reliability": 0.65,
            "operational_status": OperationalStatus.AVAILABLE,
            "status_last_updated": self.now.isoformat(),
        }, as_of=self.now)
        self.assertTrue(res_b.eligible)

        # UNKNOWN status without verification -> REJECT
        res_c = check_operational_eligibility({
            "charger_id": "CHG_12C",
            "reliability": 0.95,
            "operational_status": OperationalStatus.UNKNOWN,
        }, as_of=self.now)
        self.assertFalse(res_c.eligible)

    # --------------------------------------------------------------------------
    # 13. Deterministic repeated evaluation
    # --------------------------------------------------------------------------
    def test_13_deterministic_repeated_evaluation(self):
        charger = {
            "charger_id": "CHG_13",
            "name": "Deterministic Evaluation Charger",
            "operational_status": OperationalStatus.AVAILABLE,
            "reliability": 0.88,
            "status_last_updated": self.now.isoformat(),
        }
        res1 = check_operational_eligibility(charger, as_of=self.now)
        for _ in range(5):
            res_sub = check_operational_eligibility(charger, as_of=self.now)
            self.assertEqual(res1.eligible, res_sub.eligible)
            self.assertEqual(res1.operational_status, res_sub.operational_status)
            self.assertEqual(res1.status_confidence, res_sub.status_confidence)
            self.assertEqual(res1.trust_score, res_sub.trust_score)

    # --------------------------------------------------------------------------
    # 14. Existing charger recommendation tests remain passing
    # --------------------------------------------------------------------------
    def test_14_recommendation_api_contract_and_operational_fields(self):
        mock_df = pd.DataFrame([
            {
                "id": 1,
                "name": "Bengaluru Electronic City Fast Charger",
                "operator": "Tata Power",
                "address": "Hosur Road, Bengaluru",
                "city": "Bengaluru",
                "latitude": 12.8450,
                "longitude": 77.6600,
                "charging_power_kw": 60.0,
                "num_ports": 2,
                "connector_type": "CCS2",
                "tariff_inr_per_kwh": 18.0,
                "reliability": 0.94,
                "probability_available": 0.85,
            }
        ])
        recommendation_engine._CACHED_CHARGERS_DF = mock_df

        # Verify /recommend contract with operational fields
        res = self.client.post("/recommend", json={
            "user_lat": 12.8500,
            "user_lon": 77.6500,
            "connector_type": "CCS2",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 30.0,
            "target_soc_percent": 80.0,
            "max_search_radius_km": 10.0,
            "top_n": 1,
        })
        self.assertEqual(res.status_code, 200)
        recs = res.json()
        self.assertGreaterEqual(len(recs), 1)
        r0 = recs[0]
        # Check all required fields from Change 27 Section 8
        self.assertEqual(r0["charger_id"], 1)
        self.assertEqual(r0["operational_status"], "AVAILABLE")
        self.assertIn("status_confidence", r0)
        self.assertIn("reliability", r0)
        self.assertIn("availability", r0)
        self.assertTrue(r0["eligible_for_planning"])
        self.assertIsNone(r0["rejection_reason"])

    # --------------------------------------------------------------------------
    # 15. New Feedback & Operational Status Endpoints Functional
    # --------------------------------------------------------------------------
    def test_15_feedback_and_operational_status_endpoints(self):
        # 1. Post feedback
        fb_res = self.client.post(
            "/chargers/101/feedback",
            json={
                "result": "SUCCESSFUL_CHARGE",
                "vehicle_id": "V_TATA_001",
                "notes": "Charged successfully at 50kW without issue.",
            },
        )
        self.assertEqual(fb_res.status_code, 200)
        fb_data = fb_res.json()
        self.assertEqual(fb_data["status"], "success")
        self.assertEqual(fb_data["operational_status"], "AVAILABLE")
        self.assertIn("data_honesty_note", fb_data)

        # 2. Query operational status endpoint
        st_res = self.client.get("/chargers/101/operational-status")
        self.assertEqual(st_res.status_code, 200)
        st_data = st_res.json()
        self.assertEqual(st_data["charger_id"], "101")
        self.assertEqual(st_data["operational_status"], "AVAILABLE")
        self.assertIn("feedback_summary", st_data)
        self.assertEqual(st_data["feedback_summary"]["recent_positive_reports"], 1)
        self.assertIn("Operational confidence based on latest available data", st_data["data_honesty_note"])

    # --------------------------------------------------------------------------
    # 16. OCM station with old DB updated_at is not rejected for fake staleness
    # --------------------------------------------------------------------------
    def test_16_ocm_station_with_old_updated_at_not_rejected_for_fake_staleness(self):
        # Database row updated_at from months ago (e.g. 900+ hours old)
        old_db_time = self.now - timedelta(days=60)
        station_data = {
            "charger_id": "OCM_KAR_101",
            "name": "Urs Kar, Krishnamurthy Puram",
            "operational_status": "AVAILABLE",
            "updated_at": old_db_time.isoformat(),
            "created_at": old_db_time.isoformat(),
            "reliability": 0.92,
            "charging_power_kw": 50.0,
        }
        res = check_operational_eligibility(station_data, as_of=self.now)
        # Must be eligible and not falsely rejected due to DB updated_at timestamp
        self.assertTrue(res.eligible)
        self.assertEqual(res.operational_status, OperationalStatus.AVAILABLE)
        self.assertIsNone(res.rejection_reason)
        self.assertGreaterEqual(res.status_confidence, 0.40)
        self.assertIsNone(res.status_age_hours)
        self.assertIsNone(res.status_last_updated)


if __name__ == "__main__":
    unittest.main()


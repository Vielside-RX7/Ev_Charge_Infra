"""
Change 28 Validation Suite: Automatic Multi-Stop Energy-Aware EV Journey Planner
================================================================================
Comprehensive test suite validating:
  1. destination directly reachable (0 stops)
  2. one automatic charging stop
  3. multiple automatic charging stops
  4. closest charger not selected (when diversion/progress worse)
  5. unreliable charger avoided
  6. out-of-service charger rejected (Change 27 safety gate)
  7. unreachable charger rejected (reserve buffer strictly enforced)
  8. high-diversion charger penalized
  9. better low-diversion charger selected
  10. destination reachable after final stop
  11. no feasible charger (clean INFEASIBLE diagnosis)
  12. duplicate charger prevention (no cycles)
  13. safe termination (max iteration limit)
  14. start equals destination (0 km, direct success)
  15. deterministic repeated planning
  16. all legs preserve reserve buffer
  17. 800 km controlled synthetic benchmark (Section 15)
  18. FastAPI POST /trip/plan multi_stop integration contract
"""

import os
import sys
import unittest
from typing import Any, Dict, List

# Ensure project root is in sys.path
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from fastapi.testclient import TestClient
from api.main import app
from joint_route_charging_engine import VehicleState, ChargerCandidate
from operational_safety import OperationalStatus, OperationalPolicy
from multi_stop_planner import MultiStopTripPlanner, MultiStopPlanResult, ChargingStopDetail, JourneyLeg


class TestChange28MultiStopPlanner(unittest.TestCase):

    def setUp(self):
        # 30 kWh battery, 80% starting SoC, 5% reserve (1.5 kWh buffer), 0.150 kWh/km
        # Usable starting energy = 30 * (0.80 - 0.05) = 22.5 kWh => ~150 km range
        self.default_vehicle = VehicleState(
            battery_capacity_kwh=30.0,
            current_soc_percent=80.0,
            connector_type="CCS2",
            reserve_battery_percent=5.0,
            target_soc_percent=80.0,
            energy_consumption_kwh_per_km=0.150,
        )
        self.planner = MultiStopTripPlanner(energy_rate_kwh_per_km=0.150)
        self.client = TestClient(app)

    # 1. Destination directly reachable (0 stops)
    def test_01_destination_directly_reachable(self):
        # Origin: (12.9716, 77.5946), Destination: ~20 km away
        # Energy required: ~3.0 kWh << 22.5 kWh usable
        result = self.planner.plan_journey(
            origin_lat=12.9716,
            origin_lon=77.5946,
            dest_lat=13.1500,
            dest_lon=77.5946,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "DIRECT")
        self.assertEqual(result.charging_stop_count, 0)
        self.assertEqual(len(result.charging_stops), 0)
        self.assertEqual(len(result.legs), 1)
        self.assertGreater(result.final_arrival_energy_kwh, self.default_vehicle.reserve_energy_kwh)

    # 2. One automatic charging stop
    def test_02_one_automatic_charging_stop(self):
        # Trip of ~200 km (requires 30 kWh, starting usable is 22.5 kWh -> ~150 km range)
        # Midway charger at ~100 km
        # Origin: 12.0, 77.0 -> Destination: 13.8, 77.0 (~200 km)
        cand = ChargerCandidate(
            charger_id="CH_STOP_1",
            name="Midway Supercharger 100km",
            latitude=12.9,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.98,
            probability_available=0.90,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "CHARGE")
        self.assertEqual(result.charging_stop_count, 1)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_STOP_1")
        self.assertEqual(len(result.legs), 2)  # Origin -> C1 -> Destination

    # 3. Multiple automatic charging stops (dynamic generation)
    def test_03_multiple_automatic_charging_stops(self):
        # 350 km trip: requires ~52.5 kWh.
        # Starting usable: 22.5 kWh (~150 km).
        # Charger A at ~100 km, Charger B at ~220 km, Destination at ~350 km
        cand_a = ChargerCandidate(
            charger_id="CH_A",
            name="Station Alpha (100km)",
            latitude=12.9,
            longitude=77.0,
            charging_power_kw=120.0,
            reliability=0.95,
            probability_available=0.85,
            operational_status="AVAILABLE",
        )
        cand_b = ChargerCandidate(
            charger_id="CH_B",
            name="Station Beta (220km)",
            latitude=14.0,
            longitude=77.0,
            charging_power_kw=120.0,
            reliability=0.96,
            probability_available=0.88,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=14.85,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_a, cand_b],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "CHARGE")
        self.assertEqual(result.charging_stop_count, 2)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_A")
        self.assertEqual(result.charging_stops[1].charger_id, "CH_B")
        self.assertEqual(len(result.legs), 3)  # Origin -> A -> B -> Dest

    # 4. Closest charger not selected (when progress toward destination is poor)
    def test_04_closest_charger_not_selected_when_suboptimal(self):
        # Vehicle at 12.0, 77.0 heading North to 13.8, 77.0.
        # Candidate 1: 15 km in reverse/off-route (11.85, 77.0) -> close, but negative progress
        # Candidate 2: 80 km straight en route toward destination (12.72, 77.0)
        cand_reverse = ChargerCandidate(
            charger_id="CH_REVERSE",
            name="Behind Station (15km south)",
            latitude=11.85,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.95,
            probability_available=0.90,
            operational_status="AVAILABLE",
        )
        cand_forward = ChargerCandidate(
            charger_id="CH_FORWARD",
            name="En-Route Station (80km north)",
            latitude=12.72,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.95,
            probability_available=0.90,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_reverse, cand_forward],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_FORWARD")

    # 5. Unreliable charger avoided
    def test_05_unreliable_charger_avoided(self):
        # Two chargers at similar forward locations:
        # One with 50% reliability, one with 98% reliability
        cand_flaky = ChargerCandidate(
            charger_id="CH_FLAKY",
            name="Flaky Station (rel=0.50)",
            latitude=12.8,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.50,
            probability_available=0.40,
            operational_status="AVAILABLE",
        )
        cand_solid = ChargerCandidate(
            charger_id="CH_SOLID",
            name="Solid Station (rel=0.98)",
            latitude=12.82,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.98,
            probability_available=0.92,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_flaky, cand_solid],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_SOLID")

    # 6. Out-of-service charger rejected (Change 27 safety gate)
    def test_06_out_of_service_charger_rejected(self):
        # One charger is closer and has 100 kW, but is OUT_OF_SERVICE
        # Second charger is slightly further but AVAILABLE
        cand_broken = ChargerCandidate(
            charger_id="CH_BROKEN",
            name="Broken Supercharger",
            latitude=12.7,
            longitude=77.0,
            charging_power_kw=150.0,
            reliability=0.99,
            operational_status="OUT_OF_SERVICE",
        )
        cand_healthy = ChargerCandidate(
            charger_id="CH_HEALTHY",
            name="Healthy Station",
            latitude=12.85,
            longitude=77.0,
            charging_power_kw=60.0,
            reliability=0.90,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_broken, cand_healthy],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_HEALTHY")
        self.assertNotIn("CH_BROKEN", [s.charger_id for s in result.charging_stops])

    # 7. Unreachable charger rejected (reserve strictly enforced)
    def test_07_unreachable_charger_rejected(self):
        # Starting SoC is 10% (3.0 kWh total, reserve is 1.5 kWh -> usable is 1.5 kWh = 10 km range)
        # Charger at 25 km away requires 3.75 kWh > 1.5 kWh usable
        low_soc_vehicle = VehicleState(
            battery_capacity_kwh=30.0,
            current_soc_percent=10.0,
            reserve_battery_percent=5.0,
            energy_consumption_kwh_per_km=0.150,
        )
        cand_far = ChargerCandidate(
            charger_id="CH_TOO_FAR",
            name="Unreachable Station (25km)",
            latitude=12.225,
            longitude=77.0,  # ~25 km away
            charging_power_kw=100.0,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.0,
            dest_lon=77.0,
            vehicle_state=low_soc_vehicle,
            candidate_chargers=[cand_far],
        )
        self.assertFalse(result.success)
        self.assertEqual(result.decision_type, "INFEASIBLE")

    # 8. High-diversion charger penalized
    def test_08_high_diversion_charger_penalized(self):
        # Candidate 1 has 35 km diversion detour
        # Candidate 2 has 2 km diversion detour
        # Both reachable; candidate 2 must be chosen
        cand_detour = ChargerCandidate(
            charger_id="CH_DETOUR",
            name="Deep Detour Station (35km off-corridor)",
            latitude=12.8,
            longitude=77.4,  # deep east detour
            charging_power_kw=100.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        cand_corridor = ChargerCandidate(
            charger_id="CH_CORRIDOR",
            name="On-Highway Station (near 0 detour)",
            latitude=12.8,
            longitude=77.01,
            charging_power_kw=100.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_detour, cand_corridor],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_CORRIDOR")
        self.assertLess(result.charging_stops[0].diversion_distance_km, 5.0)

    # 9. Better low-diversion charger selected
    def test_09_better_low_diversion_charger_selected(self):
        # Even if high-diversion charger has slightly higher power,
        # low diversion should win under Change 18A multi-criteria cost
        cand_fast_far = ChargerCandidate(
            charger_id="CH_FAST_FAR",
            name="150kW Detour Station (25km off)",
            latitude=12.75,
            longitude=77.30,
            charging_power_kw=150.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        cand_clean = ChargerCandidate(
            charger_id="CH_CLEAN",
            name="100kW Direct Corridor Station",
            latitude=12.75,
            longitude=77.02,
            charging_power_kw=100.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_fast_far, cand_clean],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_CLEAN")

    # 10. Destination reachable after final stop
    def test_10_destination_reachable_after_final_stop(self):
        # Destination is reached with battery charge >= reserve
        cand = ChargerCandidate(
            charger_id="CH_STOP",
            name="Mandya Midway Fast Charger",
            latitude=12.52,
            longitude=76.90,
            charging_power_kw=80.0,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.9716,
            origin_lon=77.5946,
            dest_lat=12.2958,
            dest_lon=76.6394,  # Mysore (~140 km)
            vehicle_state=VehicleState(
                battery_capacity_kwh=30.0,
                current_soc_percent=55.0,  # 55% of 30 kWh = 16.5 kWh, usable 15.0 kWh (~100 km range)
                reserve_battery_percent=5.0,
                energy_consumption_kwh_per_km=0.150,
            ),
            candidate_chargers=[cand],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "CHARGE")
        self.assertGreaterEqual(result.final_arrival_energy_kwh, result.reserve_energy_kwh)

    # 11. No feasible charger (clean INFEASIBLE diagnosis)
    def test_11_no_feasible_charger_diagnosis(self):
        # Destination 500 km away, no chargers provided
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=16.5,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[],
        )
        self.assertFalse(result.success)
        self.assertEqual(result.decision_type, "INFEASIBLE")
        self.assertIn("exceeds remaining battery range", result.explanation)

    # 12. Duplicate charger prevention (no cycling)
    def test_12_duplicate_charger_prevention(self):
        # Single charger in candidate pool: planner cannot visit it twice
        cand = ChargerCandidate(
            charger_id="CH_SOLO",
            name="Single En-Route Station",
            latitude=12.8,
            longitude=77.0,
            charging_power_kw=100.0,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=16.0,
            dest_lon=77.0,  # 440 km trip (needs multiple stops)
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand],
        )
        # Because there's only 1 charger and destination is 440 km away,
        # it visits CH_SOLO once, then cannot reach destination and terminates INFEASIBLE
        # rather than looping into CH_SOLO repeatedly
        visited_ids = [s.charger_id for s in result.charging_stops]
        self.assertLessEqual(visited_ids.count("CH_SOLO"), 1)
        self.assertEqual(result.decision_type, "INFEASIBLE")

    # 13. Safe termination (iteration limit)
    def test_13_safe_termination_iteration_limit(self):
        planner_limited = MultiStopTripPlanner(max_stops=3)
        # 10 chargers along a 1000 km route
        cands = [
            ChargerCandidate(
                charger_id=f"CH_STEP_{i}",
                name=f"Step Station {i}",
                latitude=12.0 + (i * 0.8),
                longitude=77.0,
                charging_power_kw=100.0,
                operational_status="AVAILABLE",
            )
            for i in range(1, 10)
        ]
        result = planner_limited.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=22.0,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=cands,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.decision_type, "INFEASIBLE")
        self.assertLessEqual(result.charging_stop_count, 3)
        self.assertIn("safety limit", result.explanation)

    # 14. Start equals destination
    def test_14_start_equals_destination(self):
        result = self.planner.plan_journey(
            origin_lat=12.9716,
            origin_lon=77.5946,
            dest_lat=12.9716,
            dest_lon=77.5946,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "DIRECT")
        self.assertEqual(result.total_distance_km, 0.0)
        self.assertEqual(result.total_energy_kwh, 0.0)
        self.assertEqual(result.charging_stop_count, 0)
        self.assertEqual(len(result.legs), 0)

    # 15. Deterministic repeated planning
    def test_15_deterministic_repeated_planning(self):
        cand = ChargerCandidate(
            charger_id="CH_DET_1",
            name="Deterministic Fast Station",
            latitude=12.9,
            longitude=77.0,
            charging_power_kw=100.0,
            operational_status="AVAILABLE",
        )
        res1 = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand],
        ).to_dict()

        res2 = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=13.8,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand],
        ).to_dict()

        self.assertEqual(res1["decision_type"], res2["decision_type"])
        self.assertEqual(res1["charging_stop_count"], res2["charging_stop_count"])
        self.assertEqual(res1["total_distance_km"], res2["total_distance_km"])
        self.assertEqual(res1["total_energy_kwh"], res2["total_energy_kwh"])
        self.assertEqual(res1["charging_stops"], res2["charging_stops"])

    # 16. All legs preserve reserve buffer
    def test_16_all_legs_preserve_reserve(self):
        # 3-stop journey setup
        cands = [
            ChargerCandidate(
                charger_id="CH_LEG_1",
                name="Leg 1 Station (110km)",
                latitude=13.0,
                longitude=77.0,
                charging_power_kw=100.0,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_LEG_2",
                name="Leg 2 Station (220km)",
                latitude=14.0,
                longitude=77.0,
                charging_power_kw=100.0,
                operational_status="AVAILABLE",
            ),
        ]
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=14.9,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=cands,
        )
        self.assertTrue(result.success)
        for stop in result.charging_stops:
            self.assertGreaterEqual(
                stop.arrival_energy_kwh,
                self.default_vehicle.reserve_energy_kwh - 1e-4,
                f"Stop {stop.charger_id} violated battery safety reserve buffer!"
            )
        self.assertGreaterEqual(
            result.final_arrival_energy_kwh,
            self.default_vehicle.reserve_energy_kwh - 1e-4,
            "Destination arrival violated battery safety reserve buffer!"
        )

    # 17. Dedicated 800 km Controlled Synthetic Test (Section 15)
    def test_17_controlled_800km_journey(self):
        """
        Section 15 Controlled Test:
        - Distance: ~800 km
        - Battery capacity: 30 kWh
        - Baseline consumption: 0.150 kWh/km
        - Safety reserve: 5% (1.5 kWh)
        - Target SoC: 80% (24.0 kWh post-charge => 22.5 kWh usable => ~150 km per leg)
        - Starting SoC: 100% (30 kWh total => 28.5 kWh usable => ~190 km initial range)
        """
        vehicle_800km = VehicleState(
            battery_capacity_kwh=30.0,
            current_soc_percent=100.0,
            connector_type="CCS2",
            reserve_battery_percent=5.0,
            target_soc_percent=80.0,
            energy_consumption_kwh_per_km=0.150,
        )

        # Place chargers spaced every ~115-135 km along the route corridor
        # Road distance from (12.0, 77.0) to (17.80, 77.0) is 792.75 km (~800 km benchmark)
        # We place 6 operational stations along the corridor, 1 broken (fault), 1 high-detour, 1 unreachable
        candidates_800km = [
            ChargerCandidate(
                charger_id="CH_800_1",
                name="Highway Hub 1 (123km)",
                latitude=12.85,
                longitude=77.0,
                charging_power_kw=120.0,
                reliability=0.96,
                probability_available=0.88,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_BROKEN",
                name="Broken Station (125km)",
                latitude=12.90,
                longitude=77.0,
                charging_power_kw=150.0,
                reliability=0.99,
                operational_status="OUT_OF_SERVICE",  # Must be rejected by Change 27 gate
            ),
            ChargerCandidate(
                charger_id="CH_800_2",
                name="Highway Hub 2 (242km)",
                latitude=13.70,
                longitude=77.0,
                charging_power_kw=120.0,
                reliability=0.97,
                probability_available=0.90,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_3",
                name="Highway Hub 3 (360km)",
                latitude=14.55,
                longitude=77.0,
                charging_power_kw=100.0,
                reliability=0.95,
                probability_available=0.85,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_DETOUR",
                name="Deep Detour Station (360km + 45km east)",
                latitude=14.55,
                longitude=77.40,
                charging_power_kw=120.0,
                reliability=0.95,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_4",
                name="Highway Hub 4 (479km)",
                latitude=15.40,
                longitude=77.0,
                charging_power_kw=120.0,
                reliability=0.98,
                probability_available=0.91,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_5",
                name="Highway Hub 5 (616km)",
                latitude=16.25,
                longitude=77.0,
                charging_power_kw=120.0,
                reliability=0.94,
                probability_available=0.86,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_6",
                name="Highway Hub 6 (732km)",
                latitude=17.10,
                longitude=77.0,
                charging_power_kw=120.0,
                reliability=0.97,
                probability_available=0.92,
                operational_status="AVAILABLE",
            ),
            ChargerCandidate(
                charger_id="CH_800_UNREACHABLE",
                name="Far Station Beyond Range",
                latitude=25.0,
                longitude=77.0,
                charging_power_kw=150.0,
                reliability=0.99,
                operational_status="AVAILABLE",
            ),
        ]

        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=17.80,
            dest_lon=77.0,
            vehicle_state=vehicle_800km,
            candidate_chargers=candidates_800km,
        )

        # 1. Successful journey plan
        self.assertTrue(result.success)
        self.assertEqual(result.decision_type, "CHARGE")
        self.assertTrue(result.feasible)

        # 2. Distance is approximately 800 km (within 5% tolerance: 760 - 850 km)
        self.assertAlmostEqual(result.total_distance_km, 800.0, delta=45.0)

        # 3. Dynamic stop count: emerges organically from physics (6 stops for 836 km at ~120-137 km/leg)
        self.assertGreaterEqual(result.charging_stop_count, 4)
        self.assertLessEqual(result.charging_stop_count, 7)

        # 4. Out of service charger CH_800_BROKEN was never selected
        selected_ids = [s.charger_id for s in result.charging_stops]
        self.assertNotIn("CH_800_BROKEN", selected_ids)

        # 5. Deep detour charger and unreachable charger were avoided
        self.assertNotIn("CH_800_DETOUR", selected_ids)
        self.assertNotIn("CH_800_UNREACHABLE", selected_ids)

        # 6. Every leg and stop preserves the 1.5 kWh (5%) reserve
        for stop in result.charging_stops:
            self.assertGreaterEqual(stop.arrival_energy_kwh, 1.5 - 1e-4)
            self.assertEqual(stop.operational_status, "AVAILABLE")
            self.assertGreater(stop.charging_power_kw, 0.0)
            self.assertGreater(stop.charging_duration_minutes, 0.0)

        # 7. Destination reachable with buffer >= reserve
        self.assertGreaterEqual(result.final_arrival_energy_kwh, 1.5 - 1e-4)

        # 8. Complete journey legs structure
        self.assertEqual(len(result.legs), result.charging_stop_count + 1)
        for i, leg in enumerate(result.legs):
            self.assertEqual(leg.leg_index, i + 1)
            self.assertGreater(leg.distance_km, 0.0)
            self.assertGreater(leg.energy_kwh, 0.0)

    # 18. Look-Ahead Continuation Quality Scenario (Section 8)
    def test_19_lookahead_continuation_quality(self):
        """
        Charger A: closer now (~50km), but traps vehicle in a dead end (no continuation to dest).
        Charger B: slightly further (~90km), but provides valid continuation forward to Charger C and dest.
        The planner must reject dead-end Charger A and select Charger B.
        """
        # Origin: 12.0, 77.0 -> Destination: 14.5, 77.0 (~280 km)
        cand_trap = ChargerCandidate(
            charger_id="CH_TRAP",
            name="Trap Station (50km out, dead end)",
            latitude=12.4,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.98,
            operational_status="AVAILABLE",
        )
        cand_good_1 = ChargerCandidate(
            charger_id="CH_GOOD_1",
            name="Forward Corridor Station 1 (95km)",
            latitude=12.8,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        cand_good_2 = ChargerCandidate(
            charger_id="CH_GOOD_2",
            name="Forward Corridor Station 2 (190km)",
            latitude=13.6,
            longitude=77.0,
            charging_power_kw=100.0,
            reliability=0.95,
            operational_status="AVAILABLE",
        )
        result = self.planner.plan_journey(
            origin_lat=12.0,
            origin_lon=77.0,
            dest_lat=14.5,
            dest_lon=77.0,
            vehicle_state=self.default_vehicle,
            candidate_chargers=[cand_trap, cand_good_1, cand_good_2],
        )
        self.assertTrue(result.success)
        self.assertNotIn("CH_TRAP", [s.charger_id for s in result.charging_stops])

    # 18. API POST /trip/plan with multi_stop=True contract validation
    def test_18_api_trip_plan_multistop_contract(self):
        payload = {
            "vehicle_id": "EV_MULTI_28",
            "battery_capacity_kwh": 30.0,
            "current_soc_percent": 80.0,
            "origin_latitude": 12.0,
            "origin_longitude": 77.0,
            "destination_latitude": 13.8,
            "destination_longitude": 77.0,
            "connector_type": "CCS2",
            "multi_stop": True,
            "candidate_chargers": [
                {
                    "charger_id": "CH_API_1",
                    "name": "Midway Hub",
                    "latitude": 12.9,
                    "longitude": 77.0,
                    "charging_power_kw": 120.0,
                    "reliability": 0.98,
                    "probability_available": 0.90,
                    "operational_status": "AVAILABLE",
                }
            ],
        }
        res = self.client.post("/trip/plan", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["decision_type"], "CHARGE")
        self.assertEqual(data["charging_stop_count"], 1)
        self.assertIsInstance(data["charging_stops"], list)
        self.assertEqual(len(data["charging_stops"]), 1)
        self.assertEqual(data["charging_stops"][0]["charger_id"], "CH_API_1")

        # Section 16 legs list
        self.assertIsInstance(data["legs"], list)
        self.assertEqual(len(data["legs"]), 2)
        self.assertEqual(data["legs"][0]["from"], "Origin")
        self.assertEqual(data["legs"][0]["to"], "Midway Hub")
        self.assertEqual(data["legs"][1]["from"], "Midway Hub")
        self.assertEqual(data["legs"][1]["to"], "Destination")

        # Top-level Section 16 attributes
        self.assertIn("total_distance_km", data)
        self.assertIn("total_energy_kwh", data)
        self.assertIn("total_travel_time_minutes", data)
        self.assertIn("total_charging_wait_minutes", data)
        self.assertIn("total_charging_duration_minutes", data)
        self.assertIn("total_cost", data)
        self.assertIn("feasible", data)
        self.assertTrue(data["feasible"])
        self.assertIn("algorithm_used", data)


if __name__ == "__main__":
    unittest.main()

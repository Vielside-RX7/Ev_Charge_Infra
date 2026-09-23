"""
Change 28 Performance & Bounded Shortlisting Verification Test Suite
=====================================================================
Validates:
1. Obvious out-of-range candidates are filtered before any OSRM road routing call.
2. Shortlist size is bounded and deterministic.
3. Multi-stop planning returns a result for the Mysuru -> Kempegowda Airport case.
4. Final selected legs still use authoritative road-route feasibility.
"""

import os
import sys
import unittest
from typing import Any, Dict, List, Tuple

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))
if os.path.join(_PROJECT_ROOT, "api") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "api"))

from joint_route_charging_engine import VehicleState, ChargerCandidate
from multi_stop_planner import MultiStopTripPlanner, haversine_km


class TestChange28PerformanceShortlist(unittest.TestCase):

    def setUp(self):
        # 30 kWh battery, 80% SoC, 5% reserve = 22.5 kWh usable => 150 km usable range at 0.150 kWh/km
        self.vehicle = VehicleState(
            battery_capacity_kwh=30.0,
            current_soc_percent=80.0,
            reserve_battery_percent=5.0,
            target_soc_percent=80.0,
            energy_consumption_kwh_per_km=0.150,
        )

    def test_out_of_range_candidates_filtered_before_osrm(self):
        """
        Prove that candidates with Haversine distance exceeding vehicle usable range
        are rejected deterministically BEFORE any road-route call is dispatched.
        """
        called_routes: List[Tuple[float, float, float, float]] = []

        def spy_router(user_lat, user_lon, charger_lat, charger_lon, include_geometry=False):
            called_routes.append((round(user_lat, 4), round(user_lon, 4), round(charger_lat, 4), round(charger_lon, 4)))
            h = haversine_km(user_lat, user_lon, charger_lat, charger_lon)
            return {
                "distance_km": round(h * 1.2, 2),
                "travel_time_minutes": round((h * 1.2 / 65.0) * 60.0, 1),
                "geometry": None,
                "is_fallback": False,
            }

        planner = MultiStopTripPlanner(
            routing_provider=spy_router,
            energy_rate_kwh_per_km=0.150,
            candidate_shortlist_size=10,
        )

        origin_lat, origin_lon = 12.0, 77.0
        dest_lat, dest_lon = 13.8, 77.0  # 200 km trip (requires 1 stop)
        usable_range_km = self.vehicle.usable_energy_kwh / 0.150  # 150.0 km

        # Create 1 valid candidate in range, plus 40 candidates well beyond 150 km (e.g. 180 - 400 km away)
        candidates = [
            ChargerCandidate(
                charger_id="IN_RANGE_1",
                name="Reachable Midway Station",
                latitude=12.8,  # ~89 km away straight-line
                longitude=77.0,
                charging_power_kw=100.0,
                reliability=0.95,
                operational_status="AVAILABLE",
            )
        ]

        for i in range(1, 41):
            candidates.append(
                ChargerCandidate(
                    charger_id=f"OUT_OF_RANGE_{i}",
                    name=f"Far Station {i}",
                    latitude=12.0 + 1.8 + (i * 0.1),  # >= 200 km to 450 km away
                    longitude=77.0,
                    charging_power_kw=120.0,
                    reliability=0.99,
                    operational_status="AVAILABLE",
                )
            )

        res = planner.plan_journey(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            vehicle_state=self.vehicle,
            candidate_chargers=candidates,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.charging_stop_count, 1)
        self.assertEqual(res.charging_stops[0].charger_id, "IN_RANGE_1")

        # Verify spy router: none of the OUT_OF_RANGE candidates should ever have been queried from Origin
        for u_lat, u_lon, c_lat, c_lon in called_routes:
            if u_lat == round(origin_lat, 4) and u_lon == round(origin_lon, 4):
                # If destination, it's the direct route check
                if c_lat == round(dest_lat, 4) and c_lon == round(dest_lon, 4):
                    continue
                # For candidates queried from origin, straight line MUST be <= usable_range_km
                straight_line = haversine_km(origin_lat, origin_lon, c_lat, c_lon)
                self.assertLessEqual(
                    straight_line,
                    usable_range_km,
                    f"Candidate at ({c_lat}, {c_lon}) was queried but straight-line distance {straight_line:.1f} km > usable {usable_range_km:.1f} km!",
                )

    def test_shortlist_size_is_bounded_and_deterministic(self):
        """
        Prove that the candidate shortlist is strictly bounded to candidate_shortlist_size
        and produces the exact same set of candidates regardless of initial order.
        """
        planner = MultiStopTripPlanner(
            energy_rate_kwh_per_km=0.150,
            candidate_shortlist_size=10,
        )

        origin_lat, origin_lon = 12.0, 77.0
        dest_lat, dest_lon = 13.8, 77.0
        usable_energy = self.vehicle.usable_energy_kwh

        # Generate 60 diverse reachable candidates within 150 km
        candidates = []
        for i in range(60):
            candidates.append(
                ChargerCandidate(
                    charger_id=f"CH_DIVERSE_{i:03d}",
                    name=f"Station {i}",
                    latitude=12.1 + (i * 0.018),  # 11 km to 120 km
                    longitude=77.0 + ((i % 5) * 0.01),
                    charging_power_kw=50.0 + ((i % 3) * 30.0),
                    reliability=0.85 + ((i % 10) * 0.01),
                    probability_available=0.80 + ((i % 8) * 0.02),
                    operational_status="AVAILABLE",
                )
            )

        # Call shortlist with original order
        shortlist_1 = planner._shortlist_reachable_candidates(
            current_lat=origin_lat,
            current_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            candidates=candidates,
            usable_energy=usable_energy,
            visited_charger_ids=set(),
        )

        # Reverse candidates order
        reversed_candidates = list(reversed(candidates))
        shortlist_2 = planner._shortlist_reachable_candidates(
            current_lat=origin_lat,
            current_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            candidates=reversed_candidates,
            usable_energy=usable_energy,
            visited_charger_ids=set(),
        )

        # 1. Bounded size
        self.assertLessEqual(len(shortlist_1), 10)
        self.assertLessEqual(len(shortlist_2), 10)
        self.assertEqual(len(shortlist_1), 10)

        # 2. Strict Determinism
        ids_1 = [c.charger_id for c in shortlist_1]
        ids_2 = [c.charger_id for c in shortlist_2]
        self.assertEqual(ids_1, ids_2)

    def test_mysuru_to_kempegowda_airport_flow(self):
        """
        Verify end-to-end multi-stop planning for Mysuru -> Kempegowda International Airport (BLR).
        Route distance ~177 km > 150 km usable range, requiring an automatic stop along the corridor.
        """
        planner = MultiStopTripPlanner(energy_rate_kwh_per_km=0.150, candidate_shortlist_size=10)

        # Mysuru GPS
        mysuru_lat, mysuru_lon = 12.2958, 76.6394
        # Kempegowda Airport GPS
        kia_lat, kia_lon = 13.1986, 77.7066

        res = planner.plan_journey(
            origin_lat=mysuru_lat,
            origin_lon=mysuru_lon,
            dest_lat=kia_lat,
            dest_lon=kia_lon,
            vehicle_state=self.vehicle,
        )

        self.assertTrue(res.success)
        self.assertEqual(res.decision_type, "CHARGE")
        self.assertTrue(res.feasible)
        self.assertGreaterEqual(res.charging_stop_count, 1)

        # Verify reserve buffer is preserved at all stops and destination
        for stop in res.charging_stops:
            self.assertGreaterEqual(
                stop.arrival_energy_kwh,
                self.vehicle.reserve_energy_kwh - 1e-4,
                f"Stop {stop.charger_name} violated 5% reserve buffer!",
            )
        self.assertGreaterEqual(
            res.final_arrival_energy_kwh,
            self.vehicle.reserve_energy_kwh - 1e-4,
            "Destination arrival violated 5% reserve buffer!",
        )

        # Total distance is approximately ~180-210 km
        self.assertGreater(res.total_distance_km, 170.0)
        self.assertLess(res.total_distance_km, 250.0)

    def test_final_selected_legs_still_use_road_route_feasibility(self):
        """
        Verify that final legs and feasibility calculations strictly use authoritative
        road route distance (with curvature/real path) rather than straight-line Haversine.
        """
        ROAD_CURVATURE_FACTOR = 1.30

        def curved_road_router(user_lat, user_lon, charger_lat, charger_lon, include_geometry=False):
            h_dist = haversine_km(user_lat, user_lon, charger_lat, charger_lon)
            road_dist = round(h_dist * ROAD_CURVATURE_FACTOR, 2)
            speed_kmh = 60.0
            return {
                "distance_km": road_dist,
                "travel_time_minutes": round((road_dist / speed_kmh) * 60.0, 1),
                "geometry": {"type": "LineString", "coordinates": [[user_lon, user_lat], [charger_lon, charger_lat]]},
                "is_fallback": False,
            }

        planner = MultiStopTripPlanner(
            routing_provider=curved_road_router,
            energy_rate_kwh_per_km=0.150,
        )

        origin_lat, origin_lon = 12.0, 77.0
        cand_lat, cand_lon = 12.8, 77.0
        dest_lat, dest_lon = 13.8, 77.0

        cand = ChargerCandidate(
            charger_id="CURVED_STOP",
            name="Curved Road Stop",
            latitude=cand_lat,
            longitude=cand_lon,
            charging_power_kw=100.0,
            operational_status="AVAILABLE",
        )

        res = planner.plan_journey(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            vehicle_state=self.vehicle,
            candidate_chargers=[cand],
        )

        self.assertTrue(res.success)
        self.assertEqual(len(res.legs), 2)

        # Leg 1: Origin -> Cand
        h1 = haversine_km(origin_lat, origin_lon, cand_lat, cand_lon)
        expected_road_1 = round(h1 * ROAD_CURVATURE_FACTOR, 2)
        self.assertAlmostEqual(res.legs[0].distance_km, expected_road_1, delta=0.1)
        self.assertAlmostEqual(res.legs[0].energy_kwh, round(expected_road_1 * 0.150, 3), delta=0.05)

        # Leg 2: Cand -> Dest
        h2 = haversine_km(cand_lat, cand_lon, dest_lat, dest_lon)
        expected_road_2 = round(h2 * ROAD_CURVATURE_FACTOR, 2)
        self.assertAlmostEqual(res.legs[1].distance_km, expected_road_2, delta=0.1)
        self.assertAlmostEqual(res.legs[1].energy_kwh, round(expected_road_2 * 0.150, 3), delta=0.05)

        # Total distance is authoritative road distance
        self.assertAlmostEqual(res.total_distance_km, expected_road_1 + expected_road_2, delta=0.2)
        self.assertGreater(res.total_distance_km, (h1 + h2) * 1.15)


if __name__ == "__main__":
    unittest.main()

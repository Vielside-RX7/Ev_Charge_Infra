"""
Change 28R Scoring Recalibration — Focused Test Suite
======================================================
Tests the recalibrated scoring formula added in Change 28R:
  - short_progress_penalty  (w=0.45, dominant anti-micro-hop term)
  - forward_progress_reward (up to -0.25)
  - explicit_stop_penalty   (flat +0.15 per stop)
  - reweighted existing terms (cost weights sum to 1.00)

All tests use a synthetic Haversine-based router — fast and deterministic.
"""

import math
import os
import sys
import unittest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if os.path.join(_PROJECT_ROOT, "models") not in sys.path:
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, "models"))

from joint_route_charging_engine import VehicleState, ChargerCandidate
from multi_stop_planner import MultiStopTripPlanner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _router(user_lat=None, user_lon=None, charger_lat=None, charger_lon=None, lat1=None, lon1=None, lat2=None, lon2=None, include_geometry=False, **kwargs):
    u_lat = user_lat if user_lat is not None else lat1
    u_lon = user_lon if user_lon is not None else lon1
    c_lat = charger_lat if charger_lat is not None else lat2
    c_lon = charger_lon if charger_lon is not None else lon2
    d = _haversine(u_lat, u_lon, c_lat, c_lon) * 1.15
    return {
        "distance_km": round(d, 3),
        "travel_time_minutes": round(d / 65.0 * 60.0, 1),
        "geometry": None,
        "is_fallback": True,
    }


def _vehicle(soc=80.0, cap=30.0, reserve=5.0, rate=0.15, target=80.0):
    return VehicleState(
        battery_capacity_kwh=cap,
        current_soc_percent=soc,
        connector_type="CCS2",
        reserve_battery_percent=reserve,
        target_soc_percent=target,
        energy_consumption_kwh_per_km=rate,
    )


def _charger(cid, name, lat, lon, power=100.0, reliability=0.95, status="AVAILABLE"):
    return ChargerCandidate(
        charger_id=cid, name=name, latitude=lat, longitude=lon,
        charging_power_kw=power, reliability=reliability,
        probability_available=0.90, operational_status=status,
    )


def _planner(**kwargs):
    return MultiStopTripPlanner(
        routing_provider=_router, energy_rate_kwh_per_km=0.15, **kwargs
    )


def _stop_name(stop):
    """Return the charger name regardless of field name variant."""
    return getattr(stop, "charger_name", None) or getattr(stop, "name", stop.charger_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChange28RScoringRecalibration(unittest.TestCase):
    """Change 28R focused scoring recalibration tests."""

    def setUp(self):
        self.v = _vehicle()
        self.p = _planner()
        # Usable energy = 30 * (0.80 - 0.05) = 22.5 kWh -> max range = 150 km at 0.15 kWh/km
        self.usable_range_km = 150.0

    # ------------------------------------------------------------------
    # 1. Strategic forward charger beats tiny-hop
    # ------------------------------------------------------------------
    def test_01_strategic_charger_beats_tiny_hop(self):
        """
        Charger at ~96 km (hop_fraction ~64%, spp=0) must beat charger at ~8 km
        (hop_fraction ~5%, spp~0.875, adding +0.394 cost) when both are on-corridor
        with equal reliability and power.

        With Change 28R:
          micro  score ~ 0.15 + 0.45*0.875 - 0.25*~0.05  = 0.534 (bad)
          strat  score ~ 0.15 +           0 - 0.25*~0.53  = 0.017 (good)
        """
        # Origin (12.0,77.0) -> Dest (13.8,77.0) ~ 200 km road
        # Usable range from 80% SoC = 150 km; charger must be <= 150 km road
        tiny    = _charger("CH_TINY", "8km Micro-Hop",   12.072, 77.0)  # ~8 km h/vine * 1.15 = ~9 km road
        forward = _charger("CH_FWD",  "96km Strategic",  12.860, 77.0)  # ~96 km * 1.15 = ~110 km road

        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v,
            candidate_chargers=[tiny, forward],
        )

        self.assertTrue(result.success, f"Plan must succeed. Got: {result.explanation}")
        first_id = result.charging_stops[0].charger_id
        self.assertEqual(
            first_id, "CH_FWD",
            f"Expected CH_FWD (96km strategic), got '{_stop_name(result.charging_stops[0])}' "
            f"(leg {result.charging_stops[0].leg_distance_km:.1f} km). "
            "short_progress_penalty must dominate for 8km micro-hop."
        )

    # ------------------------------------------------------------------
    # 2. Stop penalty does not suppress required stops
    # ------------------------------------------------------------------
    def test_02_stop_penalty_does_not_suppress_required_stop(self):
        """
        A single mid-route charger at 100km (>40% of 150km range) must still be
        selected even though flat _STOP_PENALTY=0.15 applies.
        Forward-progress reward offsets the penalty for a meaningful-distance stop.
        """
        mid = _charger("CH_MID", "Midpoint 100km", 12.90, 77.0, power=120, reliability=0.97)
        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v, candidate_chargers=[mid],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stop_count, 1)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_MID")
        # Confirm leg > 60 km (> 40% of usable range) = not a micro-hop
        self.assertGreater(result.charging_stops[0].leg_distance_km, 60.0)

    # ------------------------------------------------------------------
    # 3. Required sole charger wins despite stop penalty
    # ------------------------------------------------------------------
    def test_03_required_sole_charger_wins_despite_penalty(self):
        """When only one charger exists and the trip is infeasible without it, it is selected."""
        only = _charger("CH_ONLY", "Only Station 90km", 12.81, 77.0, power=60, reliability=0.90)
        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v, candidate_chargers=[only],
        )
        self.assertTrue(result.success, "Sole required stop must be selected despite penalty")
        self.assertEqual(result.charging_stops[0].charger_id, "CH_ONLY")

    # ------------------------------------------------------------------
    # 4. Unsafe / unreachable remain excluded
    # ------------------------------------------------------------------
    def test_04_unsafe_and_unreachable_excluded(self):
        """OUT_OF_SERVICE and beyond-range chargers must remain excluded under new scoring."""
        broken  = _charger("CH_BROKEN", "Broken 80km",  12.72, 77.0, power=200, reliability=0.99,
                            status="OUT_OF_SERVICE")
        # Place too_far beyond road range (>150 km): 13.85,77.0 ~ 203 km road
        too_far = _charger("CH_FAR",    "Far 200km",    13.85, 77.0, power=100, reliability=0.99)
        valid   = _charger("CH_VALID",  "Valid 90km",   12.81, 77.0, power=100, reliability=0.95)

        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v,
            candidate_chargers=[broken, too_far, valid],
        )
        self.assertTrue(result.success)
        stop_ids = [s.charger_id for s in result.charging_stops]
        self.assertNotIn("CH_BROKEN", stop_ids, "OUT_OF_SERVICE charger must be excluded")
        self.assertNotIn("CH_FAR",    stop_ids, "Out-of-range charger must be excluded")
        self.assertIn   ("CH_VALID",  stop_ids, "Valid charger must be selected")

    # ------------------------------------------------------------------
    # 5. KIA remains feasible
    # ------------------------------------------------------------------
    def test_05_kia_route_remains_feasible(self):
        """Mysuru → KIA Bengaluru with a corridor mid-stop must remain feasible."""
        # Mysuru→KIA road ~136 km; needs ~20.4 kWh > 22.5 kWh usable → requires 1 stop
        # Charger at ~80 km haversine from Mysuru on Bengaluru highway
        mid = _charger("CH_KIA", "Mandya Corridor DC", 12.82, 77.02, power=120, reliability=0.95)
        result = self.p.plan_journey(
            origin_lat=12.2958, origin_lon=76.6394,
            dest_lat=13.1986, dest_lon=77.7066,
            vehicle_state=self.v, candidate_chargers=[mid],
        )
        self.assertTrue(result.success, f"KIA route must be feasible: {result.explanation}")
        self.assertGreater(result.final_arrival_energy_kwh, self.v.reserve_energy_kwh)

    # ------------------------------------------------------------------
    # 6. Kannur remains feasible
    # ------------------------------------------------------------------
    def test_06_kannur_route_remains_feasible(self):
        """Mysuru → Kannur (~330 km road) with two corridor chargers must remain feasible."""
        chargers = [
            # ~100 km from Mysuru on Nagarhole route (within 150 km usable range)
            _charger("CH_KAN_A", "Virajpet Charger", 12.18, 75.80, power=60, reliability=0.90),
            # ~220 km from Mysuru
            _charger("CH_KAN_B", "Kalpetta Charger", 11.61, 76.08, power=60, reliability=0.88),
        ]
        result = self.p.plan_journey(
            origin_lat=12.2958, origin_lon=76.6394,
            dest_lat=11.8745, dest_lon=75.3704,
            vehicle_state=self.v, candidate_chargers=chargers,
        )
        self.assertTrue(result.success, f"Kannur route must be feasible: {result.explanation}")
        self.assertGreater(result.final_arrival_energy_kwh, self.v.reserve_energy_kwh)

    # ------------------------------------------------------------------
    # 7. Ballari remains multi-stop feasible
    # ------------------------------------------------------------------
    def test_07_ballari_multi_stop_feasible(self):
        """
        Mysuru → Ballari (~320 km) must produce >= 2 charging stops.
        Chargers placed within the 150 km usable-range per leg.
        """
        chargers = [
            # ~115 km haversine from Mysuru; road ~132 km (within 150 km range)
            _charger("CH_BAL_A", "Hassan Junction", 13.00, 76.10, power=60, reliability=0.92),
            # ~115 km from CH_BAL_A; road ~105 km from there
            _charger("CH_BAL_B", "Chitradurga Charger", 13.80, 76.30, power=60, reliability=0.90),
            # ~96 km from CH_BAL_B; road ~91 km to dest
            _charger("CH_BAL_C", "Ballari Approach", 14.50, 76.60, power=60, reliability=0.88),
        ]
        result = self.p.plan_journey(
            origin_lat=12.2958, origin_lon=76.6394,
            dest_lat=15.1394, dest_lon=76.9214,
            vehicle_state=self.v, candidate_chargers=chargers,
        )
        self.assertTrue(result.success, f"Ballari route must be feasible: {result.explanation}")
        self.assertGreaterEqual(result.charging_stop_count, 2,
                                "Ballari (~320 km) must need >= 2 stops")
        self.assertGreater(result.final_arrival_energy_kwh, self.v.reserve_energy_kwh)

    # ------------------------------------------------------------------
    # 8. Belagavi: no immediate micro-hop preference
    # ------------------------------------------------------------------
    def test_08_belagavi_micro_hop_not_preferred(self):
        """
        Regression: from BurgerKing position (24.0 kWh, 80% SoC), the planner
        must prefer an ~80 km strategic charger over a 12 km micro-hop charger
        when both are feasible and continuation chargers are available.

        This represents the DP2 decision in the Belagavi diagnostic:
          Shanthigrama (~12 km)  vs  strategic (~80 km).
        """
        vehicle_after_bk = _vehicle(soc=80.0)   # 24.0 kWh, usable 22.5 kWh

        # 12 km micro-hop (Shanthigrama-style) — haversine ~12 km * 1.15 = 13.8 km road
        micro_hop = _charger("CH_MICRO", "12km Micro-Hop",
                              12.982, 76.229, power=40, reliability=0.917)
        # ~80 km strategic — haversine ~76 km * 1.15 = ~87 km road (within 150 km range)
        strategic = _charger("CH_STRAT", "80km Strategic",
                              12.820, 75.710, power=60, reliability=0.921)
        # Continuation chargers so plan can complete to Belagavi
        cont_a = _charger("CH_CONT_A", "Harsha-style",
                           13.949, 75.532, power=60, reliability=0.860)
        cont_b = _charger("CH_CONT_B", "Panchavati-style",
                           14.945, 75.250, power=60, reliability=0.816)

        result = self.p.plan_journey(
            origin_lat=12.960, origin_lon=76.330,   # BurgerKing position
            dest_lat=15.8497,  dest_lon=74.4977,    # Belagavi
            vehicle_state=vehicle_after_bk,
            candidate_chargers=[micro_hop, strategic, cont_a, cont_b],
        )

        self.assertTrue(result.success, f"Plan must be feasible: {result.explanation}")
        self.assertGreater(result.charging_stop_count, 0)

        first = result.charging_stops[0]
        self.assertEqual(
            first.charger_id, "CH_STRAT",
            f"Expected 80km strategic charger first, got '{_stop_name(first)}' "
            f"(leg {first.leg_distance_km:.1f} km). Micro-hop must be penalised."
        )
        self.assertGreater(first.leg_distance_km, 20.0,
                           "First stop leg must be > 20 km (not a micro-hop)")

    # ------------------------------------------------------------------
    # 9. Forward direction preferred over backward
    # ------------------------------------------------------------------
    def test_09_forward_direction_preferred_over_backward(self):
        backward = _charger("CH_BACK", "Behind 15km south", 11.865, 77.0, power=200, reliability=0.99)
        forward  = _charger("CH_FWD",  "Ahead 80km",        12.72,  77.0, power=100, reliability=0.95)
        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v, candidate_chargers=[backward, forward],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_FWD")

    # ------------------------------------------------------------------
    # 10. Reserve buffer preserved on all legs
    # ------------------------------------------------------------------
    def test_10_reserve_buffer_preserved_all_legs(self):
        cands = [
            _charger("CH_A", "Station A 100km", 12.90, 77.0, power=100),
            _charger("CH_B", "Station B 220km", 14.00, 77.0, power=100),
        ]
        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=14.9, dest_lon=77.0,
            vehicle_state=self.v, candidate_chargers=cands,
        )
        self.assertTrue(result.success)
        for stop in result.charging_stops:
            self.assertGreaterEqual(
                stop.arrival_energy_kwh, self.v.reserve_energy_kwh - 1e-4,
                f"Stop '{_stop_name(stop)}' violated reserve buffer"
            )
        self.assertGreaterEqual(result.final_arrival_energy_kwh, self.v.reserve_energy_kwh - 1e-4)

    # ------------------------------------------------------------------
    # 11. High diversion still penalised
    # ------------------------------------------------------------------
    def test_11_high_diversion_still_penalised(self):
        detour   = _charger("CH_DET", "35km Detour", 12.80, 77.42, power=150, reliability=0.97)
        corridor = _charger("CH_COR", "On-Corridor",  12.80, 77.01, power=100, reliability=0.95)
        result = self.p.plan_journey(
            origin_lat=12.0, origin_lon=77.0,
            dest_lat=13.8, dest_lon=77.0,
            vehicle_state=self.v, candidate_chargers=[detour, corridor],
        )
        self.assertTrue(result.success)
        self.assertEqual(result.charging_stops[0].charger_id, "CH_COR",
                         "High-diversion charger must still be penalised under new weights")


if __name__ == "__main__":
    unittest.main(verbosity=2)

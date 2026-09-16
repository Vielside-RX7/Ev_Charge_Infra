"""
Comprehensive Unit Tests for Energy-Aware Route Cost Function
=============================================================
Validation suite covering all 9 specified test scenarios:
  1. Same distance, lower energy -> lower cost
  2. Same energy, lower traffic delay -> lower cost
  3. Same route, lower charger wait -> lower cost
  4. Same everything except charging duration -> lower cost
  5. All normalized factors constrained to [0, 1]
  6. Weights sum to 1.0 constraint validation
  7. Component contributions sum exactly to total_cost
  8. Out-of-bounds input clamping behavior
  9. Zero-range normalization edge case handling
  + Route ranking & sensitivity analysis validation
"""

import math
import os
import sys
import unittest

# Ensure models directory is accessible
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MODELS_DIR = os.path.join(_PROJECT_ROOT, "models")
if _MODELS_DIR not in sys.path:
    sys.path.insert(0, _MODELS_DIR)

from route_cost_function import (
    CostWeights,
    NormalizationBounds,
    RouteCostResult,
    compute_route_cost,
    compare_routes,
    normalize_value,
    perform_sensitivity_analysis,
    DEFAULT_WEIGHTS,
    DEFAULT_BOUNDS,
)


class TestRouteCostFunction(unittest.TestCase):

    def setUp(self):
        self.weights = DEFAULT_WEIGHTS
        self.bounds = DEFAULT_BOUNDS

    # Scenario 1: Same distance, lower energy -> lower energy route has lower cost
    def test_scenario_1_same_distance_lower_energy(self):
        distance = 30.0
        cost_high_energy = compute_route_cost(
            distance_km=distance,
            predicted_energy_kwh=6.0,
            traffic_delay_minutes=10.0,
            charging_wait_minutes=15.0,
            charging_duration_minutes=25.0,
        )
        cost_low_energy = compute_route_cost(
            distance_km=distance,
            predicted_energy_kwh=4.0,  # lower energy
            traffic_delay_minutes=10.0,
            charging_wait_minutes=15.0,
            charging_duration_minutes=25.0,
        )
        self.assertLess(cost_low_energy.total_cost, cost_high_energy.total_cost)
        self.assertLess(cost_low_energy.energy_component, cost_high_energy.energy_component)
        self.assertEqual(cost_low_energy.distance_component, cost_high_energy.distance_component)

    # Scenario 2: Same energy, lower traffic delay -> lower traffic route has lower cost
    def test_scenario_2_same_energy_lower_traffic_delay(self):
        cost_heavy_traffic = compute_route_cost(
            distance_km=25.0,
            predicted_energy_kwh=4.5,
            traffic_delay_minutes=35.0,  # heavy traffic
            charging_wait_minutes=5.0,
            charging_duration_minutes=20.0,
        )
        cost_light_traffic = compute_route_cost(
            distance_km=25.0,
            predicted_energy_kwh=4.5,
            traffic_delay_minutes=10.0,  # light traffic
            charging_wait_minutes=5.0,
            charging_duration_minutes=20.0,
        )
        self.assertLess(cost_light_traffic.total_cost, cost_heavy_traffic.total_cost)
        self.assertLess(cost_light_traffic.traffic_component, cost_heavy_traffic.traffic_component)
        self.assertEqual(cost_light_traffic.energy_component, cost_heavy_traffic.energy_component)

    # Scenario 3: Same route, lower charger wait -> lower waiting time reduces cost
    def test_scenario_3_same_route_lower_charger_wait(self):
        cost_busy_queue = compute_route_cost(
            distance_km=20.0,
            predicted_energy_kwh=3.0,
            traffic_delay_minutes=5.0,
            charging_wait_minutes=40.0,  # 40 min queue
            charging_duration_minutes=20.0,
        )
        cost_free_charger = compute_route_cost(
            distance_km=20.0,
            predicted_energy_kwh=3.0,
            traffic_delay_minutes=5.0,
            charging_wait_minutes=5.0,  # 5 min queue
            charging_duration_minutes=20.0,
        )
        self.assertLess(cost_free_charger.total_cost, cost_busy_queue.total_cost)
        self.assertLess(cost_free_charger.charging_wait_component, cost_busy_queue.charging_wait_component)

    # Scenario 4: Same everything except charging duration -> faster charging reduces cost
    def test_scenario_4_faster_charging_duration_reduces_cost(self):
        cost_slow_charger = compute_route_cost(
            distance_km=18.0,
            predicted_energy_kwh=2.8,
            traffic_delay_minutes=5.0,
            charging_wait_minutes=0.0,
            charging_duration_minutes=60.0,  # 50 kW charger -> 60 min
        )
        cost_fast_charger = compute_route_cost(
            distance_km=18.0,
            predicted_energy_kwh=2.8,
            traffic_delay_minutes=5.0,
            charging_wait_minutes=0.0,
            charging_duration_minutes=20.0,  # 150 kW charger -> 20 min
        )
        self.assertLess(cost_fast_charger.total_cost, cost_slow_charger.total_cost)
        self.assertLess(cost_fast_charger.charging_duration_component, cost_slow_charger.charging_duration_component)

    # Scenario 5: Verify every normalized factor is strictly within [0, 1]
    def test_scenario_5_normalized_factors_bounded_in_zero_to_one(self):
        test_inputs = [
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (50.0, 10.0, 30.0, 30.0, 45.0),
            (100.0, 20.0, 60.0, 60.0, 90.0),
            (150.0, 35.0, 120.0, 90.0, 180.0),  # Extreme over-bound
        ]
        for dist, eng, traf, wait, dur in test_inputs:
            res = compute_route_cost(
                distance_km=dist,
                predicted_energy_kwh=eng,
                traffic_delay_minutes=traf,
                charging_wait_minutes=wait,
                charging_duration_minutes=dur,
            )
            self.assertGreaterEqual(res.total_cost, 0.0)
            self.assertLessEqual(res.total_cost, 1.0)
            for factor_name, norm_val in res.normalized_factors.items():
                self.assertGreaterEqual(norm_val, 0.0, f"{factor_name} < 0.0")
                self.assertLessEqual(norm_val, 1.0, f"{factor_name} > 1.0")

    # Scenario 6: Verify weights sum to 1.0 and error handling
    def test_scenario_6_weights_sum_to_one(self):
        # Default weights check
        total_default = (
            DEFAULT_WEIGHTS.distance
            + DEFAULT_WEIGHTS.energy
            + DEFAULT_WEIGHTS.traffic_delay
            + DEFAULT_WEIGHTS.charging_wait
            + DEFAULT_WEIGHTS.charging_duration
        )
        self.assertAlmostEqual(total_default, 1.0, places=6)

        # Valid custom weights
        valid_custom = CostWeights(
            distance=0.10,
            energy=0.40,
            traffic_delay=0.10,
            charging_wait=0.20,
            charging_duration=0.20,
        )
        self.assertAlmostEqual(sum(valid_custom.to_dict().values()), 1.0, places=6)

        # Invalid: sum != 1.0
        with self.assertRaises(ValueError):
            CostWeights(distance=0.5, energy=0.5, traffic_delay=0.5, charging_wait=0.0, charging_duration=0.0)

        # Invalid: negative weight
        with self.assertRaises(ValueError):
            CostWeights(distance=-0.1, energy=0.5, traffic_delay=0.3, charging_wait=0.2, charging_duration=0.1)

    # Scenario 7: Verify all component contributions sum to total_cost
    def test_scenario_7_component_contributions_sum_to_total_cost(self):
        res = compute_route_cost(
            distance_km=42.5,
            predicted_energy_kwh=7.2,
            traffic_delay_minutes=18.0,
            charging_wait_minutes=12.0,
            charging_duration_minutes=35.0,
        )
        components_sum = (
            res.distance_component
            + res.energy_component
            + res.traffic_component
            + res.charging_wait_component
            + res.charging_duration_component
        )
        self.assertAlmostEqual(res.total_cost, components_sum, places=5)

    # Scenario 8: Test values outside normalization bounds and verify clamping
    def test_scenario_8_out_of_bounds_clamping(self):
        # Sub-minimum clamp (e.g. 0 min bound tested with <= 0)
        norm_low = normalize_value(-10.0, min_bound=0.0, max_bound=100.0)
        self.assertEqual(norm_low, 0.0)

        # Supra-maximum clamp (e.g. 250 km when max is 100 km)
        norm_high = normalize_value(250.0, min_bound=0.0, max_bound=100.0)
        self.assertEqual(norm_high, 1.0)

        # Route cost with extreme values
        res_extreme = compute_route_cost(
            distance_km=500.0,           # > 100 max -> clamp to 1.0
            predicted_energy_kwh=100.0,  # > 20 max -> clamp to 1.0
            traffic_delay_minutes=300.0, # > 60 max -> clamp to 1.0
            charging_wait_minutes=180.0, # > 60 max -> clamp to 1.0
            charging_duration_minutes=240.0, # > 90 max -> clamp to 1.0
        )
        self.assertEqual(res_extreme.total_cost, 1.0)
        for val in res_extreme.normalized_factors.values():
            self.assertEqual(val, 1.0)

    # Scenario 9: Test zero-range normalization case (min == max)
    def test_scenario_9_zero_range_normalization(self):
        # When min == max, no scaling is possible; must return 0.0 without dividing by zero
        zero_range_val = normalize_value(50.0, min_bound=50.0, max_bound=50.0)
        self.assertEqual(zero_range_val, 0.0)

        custom_bounds = NormalizationBounds(
            distance_km=(50.0, 50.0),  # zero range
            energy_kwh=(10.0, 20.0),
            traffic_delay_minutes=(0.0, 60.0),
            charging_wait_minutes=(0.0, 60.0),
            charging_duration_minutes=(0.0, 90.0),
        )
        res = compute_route_cost(
            distance_km=50.0,
            predicted_energy_kwh=15.0,
            bounds=custom_bounds,
        )
        self.assertEqual(res.normalized_factors["distance_norm"], 0.0)
        self.assertEqual(res.distance_component, 0.0)

    # Negative input validation
    def test_negative_input_rejection(self):
        with self.assertRaises(ValueError):
            compute_route_cost(distance_km=-5.0, predicted_energy_kwh=2.0)
        with self.assertRaises(ValueError):
            compute_route_cost(distance_km=15.0, predicted_energy_kwh=-1.0)

    # Route comparison & ranking
    def test_compare_routes_ranking(self):
        candidates = [
            {"id": "Route_A", "distance_km": 50.0, "predicted_energy_kwh": 9.0, "traffic_delay_minutes": 25.0},
            {"id": "Route_B", "distance_km": 20.0, "predicted_energy_kwh": 3.0, "traffic_delay_minutes": 5.0},
            {"id": "Route_C", "distance_km": 35.0, "predicted_energy_kwh": 6.0, "traffic_delay_minutes": 15.0},
        ]
        ranked = compare_routes(candidates)
        # Route B is best (lowest cost), followed by C, then A
        self.assertEqual(ranked[0]["id"], "Route_B")
        self.assertEqual(ranked[1]["id"], "Route_C")
        self.assertEqual(ranked[2]["id"], "Route_A")
        self.assertLess(ranked[0]["total_cost"], ranked[1]["total_cost"])
        self.assertLess(ranked[1]["total_cost"], ranked[2]["total_cost"])

    # Sensitivity analysis helper
    def test_sensitivity_analysis(self):
        base_route = {
            "distance_km": 30.0,
            "predicted_energy_kwh": 8.0,
            "traffic_delay_minutes": 5.0,
            "charging_wait_minutes": 0.0,
            "charging_duration_minutes": 0.0,
        }
        variations = [
            # Eco-focused: heavy weight on energy
            {"distance": 0.10, "energy": 0.60, "traffic_delay": 0.10, "charging_wait": 0.10, "charging_duration": 0.10},
            # Distance-focused: heavy weight on distance
            {"distance": 0.60, "energy": 0.10, "traffic_delay": 0.10, "charging_wait": 0.10, "charging_duration": 0.10},
        ]
        analysis = perform_sensitivity_analysis(base_route, variations)
        self.assertEqual(len(analysis), 2)
        self.assertIn("total_cost", analysis[0])
        self.assertIn("components", analysis[0])


if __name__ == "__main__":
    unittest.main()

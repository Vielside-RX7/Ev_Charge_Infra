"""
Application-Facing EV Energy Prediction Service
================================================
Phase 17: Encapsulated inference layer providing dual-mode AI energy predictions
without modifying production battery simulation or charger recommendation logic.

Modes:
  1. WHOLE-TRIP ENERGY (Mode 1):
     - Predictor: Trained XGBoost Pipeline (models/artifacts/energy_xgboost_model.pkl)
     - Input: Macroscopic trip-level features (distance, duration, speed, elevation, weather, etc.)
     - Output: predicted_trip_energy_kwh (kWh across entire route)

  2. SHORT-HORIZON NEAR-TERM ENERGY (Mode 2):
     - Predictor: Trained Keras LSTM Model (models/artifacts/energy_lstm_model.keras)
     - Input: Most recent 20-timestep telemetry sequence (100s driving observation)
     - Preprocessor: Pre-fitted StandardScaler (models/artifacts/energy_lstm_scaler.pkl)
     - Output: predicted_future_energy_kwh (kWh over next 30s horizon)
     - Constraint: Strictly requires >= 20 timesteps; returns 'insufficient_history' if < 20.
                   Zero data fabrication.

Baseline Preservation:
  - The deterministic 0.150 kWh/km baseline remains the authoritative model for
    battery simulation (consumeEnergyForDistance) and trip feasibility analysis.
  - AI predictions are provided strictly as informational comparisons.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

# Path configurations
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ARTIFACTS_DIR = os.path.join(_BASE_DIR, "models", "artifacts")

XGBOOST_MODEL_PATH = os.path.join(_ARTIFACTS_DIR, "energy_xgboost_model.pkl")
XGBOOST_FEATURES_PATH = os.path.join(_ARTIFACTS_DIR, "energy_xgboost_model_features.json")

LSTM_MODEL_PATH = os.path.join(_ARTIFACTS_DIR, "energy_lstm_model.keras")
LSTM_SCALER_PATH = os.path.join(_ARTIFACTS_DIR, "energy_lstm_scaler.pkl")
LSTM_METADATA_PATH = os.path.join(_ARTIFACTS_DIR, "energy_lstm_model_metadata.json")

# Default values for optional trip features
DEFAULT_VEHICLE_ID = "VIRTUAL_EV_001"
DEFAULT_BATTERY_CAPACITY_KWH = 30.0
DEFAULT_STARTING_SOC_PERCENT = 80.0
DEFAULT_DRIVING_STYLE = "Normal"
DEFAULT_WEATHER_CONDITION = "Clear"
DEFAULT_AMBIENT_TEMPERATURE_C = 28.0
DEFAULT_TRAFFIC_DENSITY = 0.25

STYLE_ENCODING = {
    "Eco": 0.0,
    "Normal": 1.0,
    "Aggressive": 2.0,
}


class EnergyPredictionService:
    """
    Encapsulated application-facing service for EV energy predictions.
    Manages model loading, input validation, feature alignment, and scaling.
    """

    def __init__(self):
        self.xgboost_pipeline = None
        self.xgboost_features_meta = None
        self.xgboost_available = False
        self.xgboost_error = None

        self.lstm_model = None
        self.lstm_scaler = None
        self.lstm_feature_names = None
        self.lstm_available = False
        self.lstm_error = None

        # Initialize both predictors
        self._load_xgboost_predictor()
        self._load_lstm_predictor()

    def _load_xgboost_predictor(self):
        """Loads XGBoost pipeline and feature schema."""
        try:
            if not os.path.exists(XGBOOST_MODEL_PATH):
                raise FileNotFoundError(f"XGBoost artifact not found at {XGBOOST_MODEL_PATH}")

            self.xgboost_pipeline = joblib.load(XGBOOST_MODEL_PATH)
            if os.path.exists(XGBOOST_FEATURES_PATH):
                with open(XGBOOST_FEATURES_PATH, "r") as f:
                    self.xgboost_features_meta = json.load(f)

            self.xgboost_available = True
            self.xgboost_error = None
            print("[EnergyPredictionService] XGBoost whole-trip predictor loaded successfully.")
        except Exception as exc:
            self.xgboost_available = False
            self.xgboost_error = str(exc)
            print(f"[EnergyPredictionService Warning] XGBoost predictor unavailable: {exc}", file=sys.stderr)

    def _load_lstm_predictor(self):
        """Loads Keras LSTM model and pre-fitted StandardScaler."""
        try:
            if not os.path.exists(LSTM_MODEL_PATH):
                raise FileNotFoundError(f"LSTM model artifact not found at {LSTM_MODEL_PATH}")
            if not os.path.exists(LSTM_SCALER_PATH):
                raise FileNotFoundError(f"LSTM scaler artifact not found at {LSTM_SCALER_PATH}")

            # Import tensorflow lazily inside loader to avoid overhead if not used
            import tensorflow as tf

            self.lstm_model = tf.keras.models.load_model(LSTM_MODEL_PATH)
            scaler_bundle = joblib.load(LSTM_SCALER_PATH)
            self.lstm_scaler = scaler_bundle["scaler"]
            self.lstm_feature_names = scaler_bundle.get("feature_names", [])

            self.lstm_available = True
            self.lstm_error = None
            print("[EnergyPredictionService] LSTM near-term predictor loaded successfully.")
        except Exception as exc:
            self.lstm_available = False
            self.lstm_error = str(exc)
            print(f"[EnergyPredictionService Warning] LSTM predictor unavailable: {exc}", file=sys.stderr)

    def get_status(self) -> Dict[str, Any]:
        """Returns the readiness and health status of both AI prediction models."""
        return {
            "xgboost_whole_trip": {
                "available": self.xgboost_available,
                "model_type": "XGBoost Regressor Pipeline",
                "scope": "whole_trip",
                "error": self.xgboost_error,
            },
            "lstm_near_term": {
                "available": self.lstm_available,
                "model_type": "Keras LSTM Recurrent Network (Softplus)",
                "scope": "near_term_30s",
                "required_timesteps": 20,
                "error": self.lstm_error,
            },
            "baseline_model": {
                "name": "Deterministic Physical Baseline",
                "rate_kwh_per_km": 0.150,
                "status": "active_production_baseline",
            },
        }

    def predict_trip_energy(self, trip_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mode 1: Predict whole-trip energy consumption using XGBoost.

        Input trip_data expects:
          - distance_km (required, float > 0)
          - trip_duration_minutes (optional, float)
          - average_speed_kmph (optional, float)
          - elevation_gain_m (optional, float, default 0.0)
          - battery_capacity_kwh (optional, default 30.0)
          - starting_soc_percent (optional, default 80.0)
          - traffic_density_score (optional, default 0.25)
          - ambient_temperature_c (optional, default 28.0)
          - vehicle_id (optional, default 'VIRTUAL_EV_001')
          - weather_condition (optional, default 'Clear')
          - driving_style (optional, default 'Normal')

        Returns:
          Dict containing predicted_trip_energy_kwh and baseline comparison.
        """
        # Resolve distance with aliases
        distance_km = float(
            trip_data.get("distance_km")
            or trip_data.get("trip_distance_km")
            or trip_data.get("distance")
            or 0.0
        )
        baseline_kwh = round(distance_km * 0.150, 3)

        if not self.xgboost_available or self.xgboost_pipeline is None:
            return {
                "status": "unavailable",
                "model": "XGBoost",
                "message": f"XGBoost model unavailable ({self.xgboost_error or 'Artifact not loaded'}).",
                "baseline_trip_energy_kwh": baseline_kwh,
                "predicted_trip_energy_kwh": None,
            }

        if distance_km <= 0.0:
            return {
                "status": "error",
                "model": "XGBoost",
                "message": "Distance must be strictly positive.",
                "baseline_trip_energy_kwh": 0.0,
                "predicted_trip_energy_kwh": 0.0,
            }

        # Calculate or default duration and speed (supporting aliases)
        avg_speed = float(
            trip_data.get("average_speed_kmph")
            or trip_data.get("average_speed_kmh")
            or 45.0
        )
        if "trip_duration_minutes" in trip_data and trip_data["trip_duration_minutes"] is not None:
            duration_min = float(trip_data["trip_duration_minutes"])
        elif "trip_duration_seconds" in trip_data and trip_data["trip_duration_seconds"] is not None:
            duration_min = float(trip_data["trip_duration_seconds"]) / 60.0
        else:
            duration_min = max(1.0, (distance_km / max(10.0, avg_speed)) * 60.0)

        # Assemble DataFrame matching training feature schema
        row = {
            "vehicle_id": str(trip_data.get("vehicle_id", DEFAULT_VEHICLE_ID)),
            "battery_capacity_kwh": float(trip_data.get("battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH)),
            "starting_soc_percent": float(trip_data.get("starting_soc_percent", DEFAULT_STARTING_SOC_PERCENT)),
            "distance_km": distance_km,
            "trip_duration_minutes": duration_min,
            "average_speed_kmph": avg_speed,
            "elevation_gain_m": float(trip_data.get("elevation_gain_m", 0.0)),
            "traffic_density_score": float(trip_data.get("traffic_density_score", DEFAULT_TRAFFIC_DENSITY)),
            "ambient_temperature_c": float(trip_data.get("ambient_temperature_c", DEFAULT_AMBIENT_TEMPERATURE_C)),
            "weather_condition": str(trip_data.get("weather_condition", DEFAULT_WEATHER_CONDITION)),
            "driving_style": str(trip_data.get("driving_style", DEFAULT_DRIVING_STYLE)),
        }

        try:
            df = pd.DataFrame([row])
            raw_pred = self.xgboost_pipeline.predict(df)[0]
            pred_kwh = max(0.01, round(float(raw_pred), 3))

            return {
                "status": "success",
                "model": "XGBoost",
                "scope": "whole_trip",
                "distance_km": distance_km,
                "predicted_trip_energy_kwh": pred_kwh,
                "baseline_trip_energy_kwh": baseline_kwh,
                "difference_from_baseline_kwh": round(pred_kwh - baseline_kwh, 3),
                "unit": "kWh",
            }
        except Exception as exc:
            return {
                "status": "error",
                "model": "XGBoost",
                "message": f"XGBoost inference failed: {exc}",
                "baseline_trip_energy_kwh": baseline_kwh,
                "predicted_trip_energy_kwh": None,
            }

    def predict_near_term_energy(self, telemetry_sequence: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Mode 2: Predict short-horizon (30s) energy consumption using Keras LSTM.

        Input:
          telemetry_sequence: List of recent telemetry dicts (must have >= 20 observations).
          Each observation should supply at minimum:
            - speed_kmph, latitude, longitude, soc_percent, energy_consumed_kwh (or step_energy_kwh)
          Contextual/static fields fall back gracefully to active vehicle/telemetry defaults.

        Returns:
          Dict containing predicted_future_energy_kwh over next 30 seconds,
          or 'insufficient_history' status if len(telemetry_sequence) < 20.
        """
        if not self.lstm_available or self.lstm_model is None or self.lstm_scaler is None:
            return {
                "status": "unavailable",
                "model": "LSTM",
                "message": f"LSTM model unavailable ({self.lstm_error or 'Artifact not loaded'}).",
                "predicted_future_energy_kwh": None,
            }

        num_samples = len(telemetry_sequence) if telemetry_sequence else 0

        # Strict constraint: Require at least 20 ordered observations. Zero data fabrication.
        if num_samples < 20:
            return {
                "status": "insufficient_history",
                "model": "LSTM",
                "message": f"Building telemetry history ({num_samples}/20 timesteps)...",
                "required_timesteps": 20,
                "available_timesteps": num_samples,
                "predicted_future_energy_kwh": None,
            }

        # Take the most recent 20 observations
        recent_20 = telemetry_sequence[-20:]

        try:
            # Construct (20, 12) feature matrix matching exact trained schema:
            # ['speed_kmph', 'latitude', 'longitude', 'soc_percent', 'step_distance_km',
            #  'step_energy_kwh', 'elevation_m', 'elevation_delta_m', 'traffic_density_score',
            #  'ambient_temperature_c', 'battery_capacity_kwh', 'driving_style_encoded']
            matrix = np.zeros((20, 12), dtype=np.float32)

            prev_energy = None
            prev_lat = None
            prev_lon = None

            for i, obs in enumerate(recent_20):
                spd = float(obs.get("speed_kmph", obs.get("speed", 40.0)))
                lat = float(obs.get("latitude", 12.2958))
                lon = float(obs.get("longitude", 76.6394))
                soc = float(obs.get("soc_percent", obs.get("current_soc_percent", 80.0)))
                
                # Step distance (5s interval): spd * (5 / 3600)
                step_dist = float(obs.get("step_distance_km", spd * (5.0 / 3600.0)))

                # Step energy
                if "step_energy_kwh" in obs:
                    step_egy = float(obs["step_energy_kwh"])
                elif "energy_consumed_kwh" in obs:
                    curr_cum = float(obs["energy_consumed_kwh"])
                    step_egy = curr_cum - prev_energy if prev_energy is not None else (step_dist * 0.150)
                    step_egy = max(0.0001, step_egy)
                    prev_energy = curr_cum
                else:
                    step_egy = max(0.0001, step_dist * 0.150)

                elev = float(obs.get("elevation_m", 750.0))
                elev_delta = float(obs.get("elevation_delta_m", 0.0))
                traffic = float(obs.get("traffic_density_score", DEFAULT_TRAFFIC_DENSITY))
                temp = float(obs.get("ambient_temperature_c", DEFAULT_AMBIENT_TEMPERATURE_C))
                capacity = float(obs.get("battery_capacity_kwh", DEFAULT_BATTERY_CAPACITY_KWH))
                style_str = str(obs.get("driving_style", DEFAULT_DRIVING_STYLE))
                style_num = STYLE_ENCODING.get(style_str, 1.0)

                matrix[i] = [
                    spd, lat, lon, soc, step_dist, step_egy,
                    elev, elev_delta, traffic, temp, capacity, style_num
                ]

            # Scale inputs using pre-fitted training scaler
            scaled_matrix = self.lstm_scaler.transform(matrix).reshape(1, 20, 12)

            # Predict future 30-second energy consumption
            raw_pred = self.lstm_model.predict(scaled_matrix, verbose=0)[0][0]
            # softplus output is guaranteed non-negative
            pred_future_kwh = round(float(raw_pred), 5)

            return {
                "status": "success",
                "model": "LSTM",
                "scope": "near_term",
                "predicted_future_energy_kwh": pred_future_kwh,
                "prediction_horizon_seconds": 30,
                "observed_timesteps": 20,
                "observed_duration_seconds": 100,
                "unit": "kWh / next 30s",
            }
        except Exception as exc:
            return {
                "status": "error",
                "model": "LSTM",
                "message": f"LSTM inference error: {exc}",
                "predicted_future_energy_kwh": None,
            }


# Singleton service instance
energy_prediction_service = EnergyPredictionService()


def get_prediction_service() -> EnergyPredictionService:
    """Return the global EnergyPredictionService instance."""
    return energy_prediction_service

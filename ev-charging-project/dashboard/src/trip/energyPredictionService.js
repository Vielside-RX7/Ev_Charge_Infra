/**
 * AI Energy Prediction Client Service
 * 
 * Application-facing frontend interface for requesting AI energy predictions
 * from the encapsulated EnergyPredictionService backend endpoints:
 *   - Mode 1: Whole-trip prediction (XGBoost)
 *   - Mode 2: Short-horizon near-term prediction (Keras LSTM, 30s horizon)
 * 
 * Strict Guardrails:
 *   - Does NOT modify or override the production 0.150 kWh/km simulation baseline.
 *   - Provides non-blocking fallbacks if models or network are unavailable.
 *   - Strictly enforces >= 20 telemetry samples for LSTM inference (zero data fabrication).
 */

import axios from 'axios'

const API_BASE_URL =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) ||
  'http://localhost:8000'

/**
 * Fetches the operational readiness and availability status of the AI energy models.
 * 
 * @returns {Promise<Object>} Status of XGBoost, LSTM, and baseline models
 */
export async function fetchEnergyStatus() {
  try {
    const response = await axios.get(`${API_BASE_URL}/energy/status`, { timeout: 3000 })
    return response.data
  } catch (err) {
    return {
      xgboost_whole_trip: { available: false, error: err.message },
      lstm_near_term: { available: false, error: err.message },
      baseline_model: { name: 'Deterministic Physical Baseline', rate_kwh_per_km: 0.150 },
    }
  }
}

/**
 * Requests whole-trip energy prediction using the trained XGBoost model.
 * 
 * @param {Object} tripParams
 * @param {number} tripParams.distanceKm - Road distance in kilometers
 * @param {number} [tripParams.durationMinutes] - Estimated trip duration in minutes
 * @param {number} [tripParams.averageSpeedKmph] - Estimated average speed in km/h
 * @param {number} [tripParams.elevationGainM] - Estimated elevation gain in meters
 * @param {number} [tripParams.batteryCapacityKwh] - Active vehicle battery capacity in kWh
 * @param {number} [tripParams.startingSocPercent] - Active battery SoC %
 * @param {string} [tripParams.drivingStyle] - Driver profile ('Eco', 'Normal', 'Aggressive')
 * @returns {Promise<Object>} Prediction result with predicted_trip_energy_kwh and baseline comparison
 */
export async function fetchTripEnergyPrediction({
  distanceKm,
  durationMinutes = null,
  averageSpeedKmph = 45.0,
  elevationGainM = 0.0,
  batteryCapacityKwh = 30.0,
  startingSocPercent = 80.0,
  drivingStyle = 'Normal',
} = {}) {
  const dist = Number(distanceKm) || 0.0
  const baselineKwh = parseFloat((dist * 0.150).toFixed(3))

  if (dist <= 0) {
    return {
      status: 'error',
      model: 'XGBoost',
      message: 'Distance must be strictly positive',
      baseline_trip_energy_kwh: 0.0,
      predicted_trip_energy_kwh: null,
    }
  }

  try {
    const payload = {
      distance_km: dist,
      trip_duration_minutes: durationMinutes !== null ? Number(durationMinutes) : null,
      average_speed_kmph: Number(averageSpeedKmph) || 45.0,
      elevation_gain_m: Number(elevationGainM) || 0.0,
      battery_capacity_kwh: Number(batteryCapacityKwh) || 30.0,
      starting_soc_percent: Number(startingSocPercent) || 80.0,
      driving_style: drivingStyle,
    }

    const response = await axios.post(`${API_BASE_URL}/energy/predict/trip`, payload, {
      headers: { 'Content-Type': 'application/json' },
      timeout: 4000,
    })

    return response.data
  } catch (err) {
    // Non-blocking fallback: return baseline while noting AI is unavailable
    return {
      status: 'unavailable',
      model: 'XGBoost',
      scope: 'whole_trip',
      message: err?.response?.data?.detail || err.message || 'AI service offline',
      distance_km: dist,
      baseline_trip_energy_kwh: baselineKwh,
      predicted_trip_energy_kwh: null,
    }
  }
}

/**
 * Requests near-term short-horizon (30s) energy prediction using the trained LSTM model.
 * 
 * @param {Array<Object>} telemetrySamples - List of recent telemetry observations
 * @returns {Promise<Object>} Near-term energy prediction or 'insufficient_history' status
 */
export async function fetchNearTermEnergyPrediction(telemetrySamples = []) {
  const count = Array.isArray(telemetrySamples) ? telemetrySamples.length : 0

  // Strict local safeguard: must have >= 20 observations
  if (count < 20) {
    return {
      status: 'insufficient_history',
      model: 'LSTM',
      scope: 'near_term',
      message: `Building telemetry history (${count}/20 steps)...`,
      required_timesteps: 20,
      available_timesteps: count,
      predicted_future_energy_kwh: null,
    }
  }

  try {
    const payload = {
      telemetry_sequence: telemetrySamples.slice(-20).map((s) => ({
        speed_kmph: Number(s.speed_kmph ?? s.speed ?? 40.0),
        latitude: Number(s.latitude ?? 12.2958),
        longitude: Number(s.longitude ?? 76.6394),
        soc_percent: Number(s.soc_percent ?? s.currentSocPercent ?? 80.0),
        step_energy_kwh: s.step_energy_kwh !== undefined ? Number(s.step_energy_kwh) : undefined,
        energy_consumed_kwh: s.energy_consumed_kwh !== undefined ? Number(s.energy_consumed_kwh) : undefined,
        elevation_m: Number(s.elevation_m ?? 750.0),
        elevation_delta_m: Number(s.elevation_delta_m ?? 0.0),
        traffic_density_score: Number(s.traffic_density_score ?? 0.25),
        ambient_temperature_c: Number(s.ambient_temperature_c ?? 28.0),
        battery_capacity_kwh: Number(s.battery_capacity_kwh ?? 30.0),
        driving_style: s.driving_style || 'Normal',
      })),
    }

    const response = await axios.post(`${API_BASE_URL}/energy/predict/near-term`, payload, {
      headers: { 'Content-Type': 'application/json' },
      timeout: 4000,
    })

    return response.data
  } catch (err) {
    return {
      status: 'unavailable',
      model: 'LSTM',
      scope: 'near_term',
      message: err?.response?.data?.detail || err.message || 'AI service offline',
      predicted_future_energy_kwh: null,
    }
  }
}

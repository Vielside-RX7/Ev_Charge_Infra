import axios from 'axios'

const API_BASE_URL =
  (typeof import.meta !== 'undefined' &&
    import.meta.env &&
    import.meta.env.VITE_API_BASE_URL) ||
  'http://localhost:8000'

/**
 * Converts GeoJSON LineString coordinates [[lon, lat], ...] to Leaflet [[lat, lon], ...]
 */
function geojsonToLeaflet(geometry) {
  if (!geometry?.coordinates || !Array.isArray(geometry.coordinates)) {
    return []
  }
  return geometry.coordinates.map(([lon, lat]) => [lat, lon])
}

/**
 * Normalizes an individual leg from the backend response
 */
function normalizeLeg(leg) {
  if (!leg) return null
  return {
    distanceKm: Number(leg.distance_km) || 0,
    energyKwh: Number(leg.energy_kwh) || 0,
    travelTimeMinutes: Number(leg.travel_time_minutes) || 0,
    polylinePoints: geojsonToLeaflet(leg.geometry),
    isFallback: Boolean(leg.is_fallback),
    geometry: leg.geometry,
  }
}

/**
 * Calls POST /trip/plan with live Virtual EV parameters and user destination.
 * Returns a clean, normalized application-level journey plan state.
 *
 * @param {Object} options
 * @returns {Promise<Object>} Normalized TripPlan
 */
export async function planJourney({
  vehicleId = 'EV-001',
  batteryCapacityKwh = 30.0,
  currentSocPercent = 80.0,
  originLatitude,
  originLongitude,
  destinationLatitude,
  destinationLongitude,
  connectorType = 'CCS2',
  targetSocPercent = 80.0,
  candidateRadiusKm = 25.0,
  reserveSocPercent = 5.0,
  energyConsumptionKwhPerKm = 0.15,
  drivingStyle = 'balanced',
}) {
  if (
    typeof originLatitude !== 'number' ||
    typeof originLongitude !== 'number' ||
    typeof destinationLatitude !== 'number' ||
    typeof destinationLongitude !== 'number'
  ) {
    throw new Error('Valid origin and destination coordinates are required to plan a journey.')
  }

  const payload = {
    vehicle_id: vehicleId,
    origin_latitude: Number(originLatitude),
    origin_longitude: Number(originLongitude),
    destination_latitude: Number(destinationLatitude),
    destination_longitude: Number(destinationLongitude),
    battery_capacity_kwh: Number(batteryCapacityKwh),
    current_soc_percent: Number(currentSocPercent),
    connector_type: connectorType,
    target_soc_percent: Number(targetSocPercent),
    candidate_radius_km: Number(candidateRadiusKm),
    reserve_soc_percent: Number(reserveSocPercent),
    energy_consumption_kwh_per_km: Number(energyConsumptionKwhPerKm),
    driving_style: drivingStyle,
  }

  const response = await axios.post(`${API_BASE_URL}/trip/plan`, payload, {
    headers: { 'Content-Type': 'application/json' },
  })

  const raw = response.data

  // Normalize selected charger details
  let selectedCharger = null
  if (raw.selected_charger) {
    selectedCharger = {
      id: raw.selected_charger.id,
      name: raw.selected_charger.name,
      latitude: raw.selected_charger.latitude,
      longitude: raw.selected_charger.longitude,
      connector: raw.selected_charger.connector,
      reliability: Number(raw.selected_charger.reliability) || 0,
      availability: Number(raw.selected_charger.availability) || 0,
      powerKw: Number(raw.selected_charger.charging_power_kw) || 0,
    }
  }

  // Normalize legs
  const directLeg = normalizeLeg(raw.legs?.direct)
  const originToChargerLeg = normalizeLeg(raw.legs?.origin_to_charger)
  const chargerToDestLeg = normalizeLeg(raw.legs?.charger_to_destination)

  // Construct combined polyline for driving simulation playback
  let fullPolylinePoints = []
  if (raw.decision_type === 'CHARGE' && originToChargerLeg && chargerToDestLeg) {
    fullPolylinePoints = [
      ...originToChargerLeg.polylinePoints,
      ...chargerToDestLeg.polylinePoints,
    ]
  } else if (raw.decision_type === 'DIRECT' && directLeg) {
    fullPolylinePoints = directLeg.polylinePoints
  } else {
    // For INFEASIBLE or invalid journeys, do not construct misleading route lines
    fullPolylinePoints = []
  }

  // Normalize AI Energy Prediction (Change 24)
  let aiEnergyPrediction = null
  if (raw.ai_energy_prediction) {
    aiEnergyPrediction = {
      available: Boolean(raw.ai_energy_prediction.available),
      model: raw.ai_energy_prediction.model || 'XGBoost',
      predictedEnergyKwh: Number(raw.ai_energy_prediction.predicted_energy_kwh) || 0,
      baselineEnergyKwh: Number(raw.ai_energy_prediction.baseline_energy_kwh) || 0,
      deltaKwh: Number(raw.ai_energy_prediction.delta_kwh) || 0,
      deltaPercent: Number(raw.ai_energy_prediction.delta_percent) || 0,
      usedForPlanning: Boolean(raw.ai_energy_prediction.used_for_planning),
      usedForBatteryState: Boolean(raw.ai_energy_prediction.used_for_battery_state),
      message: raw.ai_energy_prediction.message || '',
    }
  }

  return {
    success: Boolean(raw.success),
    decisionType: raw.decision_type, // 'DIRECT' | 'CHARGE' | 'INFEASIBLE'
    explanation: raw.explanation || '',
    candidateCountEvaluated: Number(raw.candidate_count_evaluated) || 0,
    algorithm: raw.algorithm,
    selectedCharger,
    aiEnergyPrediction,
    routeMetrics: {
      totalDistanceKm: Number(raw.route?.total_distance_km) || 0,
      totalEnergyKwh: Number(raw.route?.total_energy_kwh) || 0,
      totalTrafficDelayMinutes: Number(raw.route?.total_traffic_delay_minutes) || 0,
      chargingWaitMinutes: Number(raw.route?.charging_wait_minutes) || 0,
      chargingDurationMinutes: Number(raw.route?.charging_duration_minutes) || 0,
      totalCost: Number(raw.route?.total_cost) || 0,
      costBreakdown: raw.route?.cost_breakdown || null,
      travelTimeMinutes:
        raw.decision_type === 'CHARGE'
          ? (originToChargerLeg?.travelTimeMinutes || 0) +
            (chargerToDestLeg?.travelTimeMinutes || 0) +
            (Number(raw.route?.charging_duration_minutes) || 0) +
            (Number(raw.route?.charging_wait_minutes) || 0)
          : directLeg?.travelTimeMinutes || 0,
    },
    energyAccounting: {
      startingEnergyKwh: Number(raw.energy?.starting_energy_kwh) || 0,
      energyRequiredKwh: Number(raw.energy?.energy_required_kwh) || 0,
      arrivalEnergyKwh: Number(raw.energy?.arrival_energy_kwh) || 0,
      energyAddedKwh: Number(raw.energy?.energy_added_kwh) || 0,
      departureEnergyKwh: Number(raw.energy?.departure_energy_kwh) || 0,
      reserveEnergyKwh: Number(raw.energy?.reserve_energy_kwh) || 0,
    },
    legs: {
      direct: directLeg,
      originToCharger: originToChargerLeg,
      chargerToDestination: chargerToDestLeg,
    },
    fullPolylinePoints,
    raw,
  }
}

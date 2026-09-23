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
 * Normalizes a multi-stop charging stop from the Section 16 backend format.
 */
function normalizeChargingStop(stop) {
  if (!stop) return null
  return {
    stopIndex: Number(stop.stop_index) || 0,
    chargerId: stop.charger_id || '',
    chargerName: stop.charger_name || stop.name || 'Charging Stop',
    latitude: Number(stop.latitude) || 0,
    longitude: Number(stop.longitude) || 0,
    operationalStatus: stop.operational_status || 'AVAILABLE',
    statusConfidence: Number(stop.status_confidence) || 1.0,
    reliability: Number(stop.reliability) || 0.9,
    availability: Number(stop.availability) || 0.8,
    chargingPowerKw: Number(stop.charging_power_kw) || 50,
    energyAddedKwh: Number(stop.energy_added_kwh) || 0,
    chargingDurationMinutes: Number(stop.charging_duration_minutes) || 0,
    waitMinutes: Number(stop.wait_minutes) || 0,
    diversionDistanceKm: Number(stop.diversion_distance_km) || 0,
    diversionPercent: Number(stop.diversion_percent) || 0,
    arrivalEnergyKwh: Number(stop.arrival_energy_kwh) || 0,
    departureEnergyKwh: Number(stop.departure_energy_kwh) || 0,
    trustScore: Number(stop.trust_score) || 0.9,
  }
}

/**
 * Calls POST /trip/plan with live Virtual EV parameters and user destination.
 * Automatically uses the multi-stop planner (multi_stop=true).
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
    // Always invoke the automatic multi-stop planner (Change 28)
    multi_stop: true,
  }

  const response = await axios.post(`${API_BASE_URL}/trip/plan`, payload, {
    headers: { 'Content-Type': 'application/json' },
  })

  const raw = response.data

  // -------------------------------------------------------------------------
  // Section 16 Multi-Stop Response Normalization
  // raw.charging_stops = [] (Section 16 list)
  // raw.legs = [] (Section 16 ordered list of driving legs)
  // -------------------------------------------------------------------------
  const isMultiStop = Array.isArray(raw.charging_stops)

  // Normalize charging stops
  const chargingStops = isMultiStop
    ? raw.charging_stops.map(normalizeChargingStop).filter(Boolean)
    : []

  // Normalize ordered legs array (Section 16 list format from multi-stop planner)
  const multiStopLegs = isMultiStop && Array.isArray(raw.legs)
    ? raw.legs.map(normalizeLeg).filter(Boolean)
    : []

  // Build combined full-route polyline from all driving legs
  let fullPolylinePoints = []
  if (isMultiStop && multiStopLegs.length > 0) {
    for (const leg of multiStopLegs) {
      if (leg.polylinePoints && leg.polylinePoints.length > 0) {
        if (fullPolylinePoints.length === 0) {
          fullPolylinePoints = [...leg.polylinePoints]
        } else {
          // Avoid duplicating the junction waypoint point between consecutive legs
          const lastPoint = fullPolylinePoints[fullPolylinePoints.length - 1]
          const firstPoint = leg.polylinePoints[0]
          const isDuplicate =
            Math.abs(lastPoint[0] - firstPoint[0]) < 1e-5 &&
            Math.abs(lastPoint[1] - firstPoint[1]) < 1e-5
          const pointsToAdd = isDuplicate ? leg.polylinePoints.slice(1) : leg.polylinePoints
          fullPolylinePoints = [...fullPolylinePoints, ...pointsToAdd]
        }
      }
    }
  }

  // Legacy single-stop normalized legs (for backward compatibility with test assertions)
  const legsDirect = normalizeLeg(
    Array.isArray(raw.legs) ? null : raw.legs?.direct
  )
  const legsOriginToCharger = normalizeLeg(
    Array.isArray(raw.legs) ? null : raw.legs?.origin_to_charger
  )
  const legsChargerToDest = normalizeLeg(
    Array.isArray(raw.legs) ? null : raw.legs?.charger_to_destination
  )

  // Legacy single-charger fallback (for backward-compat display in CHARGE mode)
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

  // Derive decisionType consistently
  // Section 16 uses feasible + charging_stop_count; legacy uses decision_type
  let decisionType = raw.decision_type
  if (!decisionType) {
    if (!raw.feasible) {
      decisionType = 'INFEASIBLE'
    } else if ((raw.charging_stop_count || 0) === 0) {
      decisionType = 'DIRECT'
    } else {
      decisionType = 'CHARGE'
    }
  }

  // Total travel time: multi-stop gives total_travel_time_minutes; legacy uses route metrics
  const totalTravelTimeMinutes =
    Number(raw.total_travel_time_minutes) ||
    Number(raw.route?.total_traffic_delay_minutes) ||
    (legsDirect?.travelTimeMinutes || 0) +
      (legsOriginToCharger?.travelTimeMinutes || 0) +
      (legsChargerToDest?.travelTimeMinutes || 0) +
      (Number(raw.route?.charging_duration_minutes) || 0) +
      (Number(raw.route?.charging_wait_minutes) || 0)

  return {
    // Core decision fields
    success: Boolean(raw.success),
    decisionType,
    explanation: raw.explanation || '',
    candidateCountEvaluated: Number(raw.candidate_count_evaluated) || 0,
    algorithm: raw.algorithm,
    feasible: raw.feasible !== false,

    // ── Section 16 Multi-Stop Fields ──────────────────────────────────────
    chargingStopCount: Number(raw.charging_stop_count) || chargingStops.length,
    chargingStops,               // ChargingStopDetail[] normalized
    multiStopLegs,               // JourneyLeg[] normalized (N legs)

    // ── Legacy Compatibility ──────────────────────────────────────────────
    selectedCharger,             // first stop as SelectedChargerInfo (legacy display)
    aiEnergyPrediction,

    routeMetrics: {
      totalDistanceKm: Number(raw.total_distance_km) || Number(raw.route?.total_distance_km) || 0,
      totalEnergyKwh: Number(raw.total_energy_kwh) || Number(raw.route?.total_energy_kwh) || 0,
      totalTrafficDelayMinutes: Number(raw.route?.total_traffic_delay_minutes) || 0,
      chargingWaitMinutes: Number(raw.total_charging_wait_minutes) || Number(raw.route?.charging_wait_minutes) || 0,
      chargingDurationMinutes: Number(raw.total_charging_duration_minutes) || Number(raw.route?.charging_duration_minutes) || 0,
      totalCost: Number(raw.total_cost) || Number(raw.route?.total_cost) || 0,
      costBreakdown: raw.route?.cost_breakdown || null,
      travelTimeMinutes: totalTravelTimeMinutes,
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
      // Section 16 list access (preferred for multi-stop)
      list: multiStopLegs,
      // Legacy named keys (backward compat with Change 20/24/25)
      direct: legsDirect,
      originToCharger: legsOriginToCharger,
      chargerToDestination: legsChargerToDest,
    },
    fullPolylinePoints,
    raw,
  }
}

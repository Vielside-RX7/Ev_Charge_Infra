/**
 * Trip Model & Status Constants
 * 
 * Defines the state structure and status constants for Trip Planning.
 * Separates static vehicle profile and dynamic telemetry from trip configuration.
 */

export const TRIP_STATUS = Object.freeze({
  NO_DESTINATION: 'No Destination',
  DESTINATION_SELECTED: 'Destination Selected',
  ROUTE_LOADING: 'Calculating Route...',
  ROUTE_READY: 'Route Ready',
  ROUTE_ERROR: 'Route Error',
})

export const NAV_STATE = Object.freeze({
  IDLE: 'IDLE',
  PLANNING: 'PLANNING',
  READY: 'READY',
  DRIVING: 'DRIVING',
  DRIVING_LEG_1: 'DRIVING_LEG_1',
  CHARGING: 'CHARGING',
  DRIVING_LEG_2: 'DRIVING_LEG_2',
  PAUSED: 'PAUSED',
  COMPLETED: 'COMPLETED',
  INFEASIBLE: 'INFEASIBLE',
})

export const LEG_TYPE = Object.freeze({
  DIRECT: 'DIRECT',
  ORIGIN_TO_CHARGER: 'ORIGIN_TO_CHARGER',
  CHARGER_TO_DESTINATION: 'CHARGER_TO_DESTINATION',
})

export const BASELINE_CONSUMPTION_KWH_PER_KM = 0.15

/**
 * Calculates estimated energy required for a route distance.
 * 
 * @param {number} distanceKm - Road distance in kilometers
 * @returns {number|null} Estimated trip energy in kWh
 */
export function calculateTripEnergy(distanceKm) {
  if (typeof distanceKm !== 'number' || distanceKm <= 0) {
    return null
  }
  return parseFloat((distanceKm * BASELINE_CONSUMPTION_KWH_PER_KM).toFixed(2))
}

/**
 * Determines whether destination is reachable given current available energy.
 * 
 * @param {number} availableEnergyKwh - Current vehicle remaining energy in kWh
 * @param {number} requiredEnergyKwh - Estimated energy to destination in kWh
 * @returns {boolean|null} True if reachable, false if charging required, null if indeterminable
 */
export function checkDestinationFeasibility(availableEnergyKwh, requiredEnergyKwh) {
  if (
    typeof availableEnergyKwh !== 'number' ||
    typeof requiredEnergyKwh !== 'number'
  ) {
    return null
  }
  return availableEnergyKwh >= requiredEnergyKwh
}

/**
 * Creates a normalized trip state object.
 * 
 * Canonical shape:
 * - origin_latitude: number (derived from live vehicle telemetry)
 * - origin_longitude: number (derived from live vehicle telemetry)
 * - destination_latitude: number | null
 * - destination_longitude: number | null
 * - destination_selected: boolean
 * - trip_status: string ('No Destination' | 'Destination Selected' | 'Calculating Route...' | 'Route Ready' | 'Route Error')
 * - route: Object | null (distance_km, travel_time_minutes, geometry, polylinePoints)
 * - route_status: string ('idle' | 'loading' | 'ready' | 'error')
 * - route_error: string | null
 * - estimated_trip_energy_kwh: number | null
 * - destination_feasible: boolean | null
 * - available_energy_kwh: number | null
 * 
 * @param {Object} params
 * @param {number} params.originLat - Live vehicle latitude
 * @param {number} params.originLon - Live vehicle longitude
 * @param {number|null} [params.destinationLat] - Selected destination latitude
 * @param {number|null} [params.destinationLon] - Selected destination longitude
 * @param {Object|null} [params.route] - Calculated road route data
 * @param {boolean} [params.isRouteLoading] - Route fetching in progress
 * @param {string|null} [params.routeError] - Route calculation error message
 * @param {number|null} [params.availableEnergyKwh] - Current vehicle remaining energy in kWh
 * @returns {Object} Canonical Trip State
 */
export function createTripState({
  originLat,
  originLon,
  destinationLat = null,
  destinationLon = null,
  route = null,
  isRouteLoading = false,
  routeError = null,
  availableEnergyKwh = null,
}) {
  const hasDestination = destinationLat !== null && destinationLon !== null

  let tripStatus = TRIP_STATUS.NO_DESTINATION
  if (hasDestination) {
    if (isRouteLoading) {
      tripStatus = TRIP_STATUS.ROUTE_LOADING
    } else if (routeError) {
      tripStatus = TRIP_STATUS.ROUTE_ERROR
    } else if (route) {
      tripStatus = TRIP_STATUS.ROUTE_READY
    } else {
      tripStatus = TRIP_STATUS.DESTINATION_SELECTED
    }
  }

  // Deterministic energy calculation derived strictly from road route distance
  const estimatedTripEnergy =
    hasDestination && route && typeof route.distance_km === 'number'
      ? calculateTripEnergy(route.distance_km)
      : null

  const destinationFeasible =
    estimatedTripEnergy !== null && typeof availableEnergyKwh === 'number'
      ? checkDestinationFeasibility(availableEnergyKwh, estimatedTripEnergy)
      : null

  return {
    origin_latitude: Number(originLat),
    origin_longitude: Number(originLon),
    destination_latitude: hasDestination ? Number(destinationLat) : null,
    destination_longitude: hasDestination ? Number(destinationLon) : null,
    destination_selected: hasDestination,
    trip_status: tripStatus,
    route: hasDestination ? route : null,
    route_status: !hasDestination
      ? 'idle'
      : isRouteLoading
      ? 'loading'
      : routeError
      ? 'error'
      : route
      ? 'ready'
      : 'idle',
    route_error: hasDestination ? routeError : null,
    estimated_trip_energy_kwh: estimatedTripEnergy,
    destination_feasible: destinationFeasible,
    available_energy_kwh: typeof availableEnergyKwh === 'number' ? availableEnergyKwh : null,
  }
}

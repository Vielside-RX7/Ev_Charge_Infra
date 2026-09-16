/**
 * Vehicle Profile Data Model
 * 
 * Represents relatively static properties of the connected vehicle.
 * Separated from live telemetry (dynamic state like SoC, location, speed).
 * 
 * The vehicle profile is the single source of truth for:
 * - Vehicle identity (vehicle_id, vehicle_name)
 * - Battery capacity (battery_capacity_kwh)
 * - Connector type (connector_type)
 * - Data source (data_source)
 */

export const DEFAULT_VEHICLE_PROFILE = Object.freeze({
  vehicle_id: 'VIRTUAL_EV_001',
  vehicle_name: 'Virtual EV',
  battery_capacity_kwh: 30.0,
  connector_type: 'CCS2',
  data_source: 'Virtual OBD',
})

/**
 * Creates a vehicle profile state object.
 * 
 * @param {Partial<typeof DEFAULT_VEHICLE_PROFILE>} overrides
 * @returns {typeof DEFAULT_VEHICLE_PROFILE}
 */
export function createVehicleProfile(overrides = {}) {
  return { ...DEFAULT_VEHICLE_PROFILE, ...overrides }
}

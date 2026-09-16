/**
 * Virtual EV Data Model & Schema
 * 
 * Defines the authoritative 13-field Standard Vehicle Telemetry interface
 * for the Virtual EV, including physical constants and battery consumption logic.
 */

export const DEFAULT_VIRTUAL_EV = {
  vehicle_id: 'EV-001',
  vehicle_name: 'Tata Nexon EV Max',
  latitude: 12.2958,
  longitude: 76.6394,
  speed_kmph: 0.0,
  battery_capacity_kwh: 30.0,
  current_soc_percent: 80.0,
  remaining_energy_kwh: 24.0,
  energy_consumed_kwh: 0.0,
  estimated_range_km: 160.0, // 24 kWh / 0.15 kWh/km
  vehicle_status: 'Standby (Parked)',
  data_source: 'Virtual OBD',
  connection_status: 'Connected',
  connector_type: 'CCS2',
  timestamp: new Date().toISOString(),
}

/**
 * Creates and normalizes a Standard Vehicle Telemetry state object.
 * Guarantees all 13 canonical fields exist with expected types.
 * 
 * @param {Partial<typeof DEFAULT_VIRTUAL_EV>} overrides - Initial or updated values
 * @returns {typeof DEFAULT_VIRTUAL_EV} Complete, validated telemetry state
 */
export function createStandardTelemetry(overrides = {}) {
  const base = { ...DEFAULT_VIRTUAL_EV, ...overrides }

  const batteryCapacity = Number(base.battery_capacity_kwh) || 30.0
  let soc = Number(base.current_soc_percent)
  let remainingEnergy = Number(base.remaining_energy_kwh)

  // Ensure mathematical consistency between SoC and remaining energy
  if (isNaN(remainingEnergy) && !isNaN(soc)) {
    remainingEnergy = parseFloat(((soc / 100) * batteryCapacity).toFixed(2))
  } else if (!isNaN(remainingEnergy) && isNaN(soc)) {
    soc = parseFloat(((remainingEnergy / batteryCapacity) * 100).toFixed(1))
  } else if (isNaN(remainingEnergy) && isNaN(soc)) {
    soc = 80.0
    remainingEnergy = parseFloat(((soc / 100) * batteryCapacity).toFixed(2))
  }

  soc = Math.max(0.0, Math.min(100.0, soc))
  remainingEnergy = Math.max(0.0, Math.min(batteryCapacity, remainingEnergy))

  const estimatedRange = base.estimated_range_km !== undefined
    ? Number(base.estimated_range_km)
    : parseFloat((remainingEnergy / 0.15).toFixed(1))

  return {
    vehicle_id: String(base.vehicle_id || 'EV-001'),
    vehicle_name: String(base.vehicle_name || 'Tata Nexon EV Max'),
    connector_type: String(base.connector_type || 'CCS2'),
    latitude: Number(base.latitude),
    longitude: Number(base.longitude),
    speed_kmph: Number(base.speed_kmph) || 0.0,
    battery_capacity_kwh: batteryCapacity,
    current_soc_percent: soc,
    remaining_energy_kwh: remainingEnergy,
    energy_consumed_kwh: Number(base.energy_consumed_kwh) || 0.0,
    estimated_range_km: estimatedRange,
    vehicle_status: String(base.vehicle_status || 'Standby (Parked)'),
    data_source: String(base.data_source || 'Virtual OBD'),
    connection_status: String(base.connection_status || 'Connected'),
  }
}

/**
 * Backward-compatible alias for createStandardTelemetry
 * 
 * @param {Partial<typeof DEFAULT_VIRTUAL_EV>} overrides
 * @returns {Object} Normalized telemetry state
 */
export function createVirtualEvState(overrides = {}) {
  return createStandardTelemetry(overrides)
}

export const BASELINE_CONSUMPTION_KWH_PER_KM = 0.15

/**
 * Calculates updated battery telemetry given an incremental distance travelled.
 * 
 * @param {typeof DEFAULT_VIRTUAL_EV} currentTelemetry - Current telemetry state
 * @param {number} incrementalDistanceKm - Incremental distance in km travelled in this step
 * @returns {Partial<typeof DEFAULT_VIRTUAL_EV>} Updated battery and range fields
 */
export function consumeEnergyForDistance(currentTelemetry, incrementalDistanceKm) {
  if (!incrementalDistanceKm || incrementalDistanceKm <= 0) {
    return {}
  }

  const capacity = Number(currentTelemetry.battery_capacity_kwh) || 30.0
  const prevRemaining = Number(currentTelemetry.remaining_energy_kwh) || 0.0
  const prevConsumed = Number(currentTelemetry.energy_consumed_kwh) || 0.0

  // 1 & 2. energy_used = incremental_distance_km * 0.15 kWh/km
  const energyUsed = incrementalDistanceKm * BASELINE_CONSUMPTION_KWH_PER_KM

  // 3. Add energy to energy_consumed_kwh (precise tracking)
  const newConsumed = parseFloat((prevConsumed + energyUsed).toFixed(4))

  // 4. Subtract energy from remaining_energy_kwh (bounded >= 0)
  const newRemaining = Math.max(0.0, parseFloat((prevRemaining - energyUsed).toFixed(4)))

  // 5. Recalculate current_soc_percent from remaining energy and capacity (bounded 0..100)
  const newSoc = capacity > 0
    ? Math.max(0.0, Math.min(100.0, parseFloat(((newRemaining / capacity) * 100).toFixed(1))))
    : 0.0

  // 6. Recalculate estimated_range_km using the same 0.15 kWh/km baseline (bounded >= 0)
  const newRange = Math.max(0.0, parseFloat((newRemaining / BASELINE_CONSUMPTION_KWH_PER_KM).toFixed(1)))

  return {
    remaining_energy_kwh: newRemaining,
    energy_consumed_kwh: newConsumed,
    current_soc_percent: newSoc,
    estimated_range_km: newRange,
  }
}

/**
 * Calculates updated battery telemetry given simulated charging energy added.
 * 
 * @param {typeof DEFAULT_VIRTUAL_EV} currentTelemetry - Current telemetry state
 * @param {number} energyAddedKwh - Incremental charging energy in kWh added
 * @returns {Partial<typeof DEFAULT_VIRTUAL_EV>} Updated battery and range fields
 */
export function chargeEnergy(currentTelemetry, energyAddedKwh) {
  if (!energyAddedKwh || energyAddedKwh <= 0) {
    return {}
  }

  const capacity = Number(currentTelemetry.battery_capacity_kwh) || 30.0
  const prevRemaining = Number(currentTelemetry.remaining_energy_kwh) || 0.0
  const newRemaining = Math.min(capacity, parseFloat((prevRemaining + energyAddedKwh).toFixed(4)))

  const newSoc = capacity > 0
    ? Math.max(0.0, Math.min(100.0, parseFloat(((newRemaining / capacity) * 100).toFixed(1))))
    : 0.0

  const newRange = Math.max(0.0, parseFloat((newRemaining / BASELINE_CONSUMPTION_KWH_PER_KM).toFixed(1)))

  return {
    remaining_energy_kwh: newRemaining,
    current_soc_percent: newSoc,
    estimated_range_km: newRange,
  }
}

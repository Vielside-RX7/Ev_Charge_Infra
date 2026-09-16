/**
 * Virtual OBD Telemetry Provider
 * 
 * Responsibilities:
 * - Acts as an emulated OBD-II telemetry source for the Virtual EV.
 * - Produces and updates telemetry records adhering strictly to the
 *   Standard Vehicle Telemetry contract.
 * - Simulates vehicle events: incremental driving, speed adjustments,
 *   battery discharge via baseline consumption (0.15 kWh/km), and status updates.
 * - Enforces the "Virtual OBD" data source identity and real-time timestamping.
 * 
 * Concept Architecture:
 *   [ Virtual OBD Provider ]
 *             ↓
 *   [ Standard Vehicle Telemetry (13 fields) ]
 *             ↓
 *   [ TelemetryContext (React state layer) ]
 *             ↓
 *   [ Existing application (RecommendationForm, NavigationView, VirtualEvTelemetry) ]
 */

import {
  DEFAULT_VIRTUAL_EV,
  createStandardTelemetry,
  consumeEnergyForDistance,
  chargeEnergy,
} from './virtualEvModel.js'

export const VIRTUAL_OBD_PROVIDER_NAME = 'Virtual OBD'

export const virtualObdProvider = {
  name: VIRTUAL_OBD_PROVIDER_NAME,
  type: 'virtual',

  /**
   * Initializes standard vehicle telemetry for the Virtual EV.
   * 
   * @param {Object} initialOverrides - Optional overrides for initial state
   * @returns {Object} Standard Vehicle Telemetry object
   */
  createInitialTelemetry(initialOverrides = {}) {
    return createStandardTelemetry({
      ...DEFAULT_VIRTUAL_EV,
      ...initialOverrides,
      data_source: VIRTUAL_OBD_PROVIDER_NAME,
      connection_status: 'Connected',
      timestamp: new Date().toISOString(),
    })
  },

  /**
   * Produces an updated Standard Vehicle Telemetry record based on vehicle movement
   * or status updates from the navigation simulator or vehicle controls.
   * 
   * @param {Object} currentTelemetry - The active standard telemetry object
   * @param {Object} updates - Changes to vehicle state (e.g. lat, lon, speed, incremental_distance_km)
   * @returns {Object} Standard Vehicle Telemetry object
   */
  produceTelemetry(currentTelemetry, updates = {}) {
    let batteryUpdates = {}
    if (updates.incremental_distance_km && updates.incremental_distance_km > 0) {
      batteryUpdates = consumeEnergyForDistance(currentTelemetry, updates.incremental_distance_km)
    }

    return createStandardTelemetry({
      ...currentTelemetry,
      ...updates,
      ...batteryUpdates,
      data_source: VIRTUAL_OBD_PROVIDER_NAME,
      connection_status: 'Connected',
      timestamp: new Date().toISOString(),
    })
  },

  /**
   * Produces an updated telemetry record during simulated charging.
   * Increases remaining_energy_kwh and current_soc_percent up to capacity.
   * 
   * @param {Object} currentTelemetry - The active standard telemetry object
   * @param {number} energyAddedKwh - Incremental charging energy in kWh added
   * @param {string} status - Vehicle status string (e.g. 'Charging (Simulated)')
   * @returns {Object} Standard Vehicle Telemetry object
   */
  produceChargingTelemetry(currentTelemetry, energyAddedKwh = 0, status = 'Charging (Simulated)') {
    const chargeUpdates = chargeEnergy(currentTelemetry, energyAddedKwh)
    return createStandardTelemetry({
      ...currentTelemetry,
      ...chargeUpdates,
      speed_kmph: 0.0,
      vehicle_status: status,
      data_source: VIRTUAL_OBD_PROVIDER_NAME,
      connection_status: 'Connected',
      timestamp: new Date().toISOString(),
    })
  },

  /**
   * Restores vehicle telemetry to its captured initial baseline state.
   * Resets speed to 0, vehicle_status to 'Standby (Parked)', and energy_consumed to 0,
   * while strictly restoring the initial coordinates, capacity, and initial SoC.
   * 
   * @param {Object} initialState - The captured baseline telemetry
   * @returns {Object} Standard Vehicle Telemetry object
   */
  resetToInitial(initialState) {
    return createStandardTelemetry({
      ...initialState,
      speed_kmph: 0.0,
      vehicle_status: 'Standby (Parked)',
      energy_consumed_kwh: 0.0,
      data_source: VIRTUAL_OBD_PROVIDER_NAME,
      connection_status: 'Connected',
      timestamp: new Date().toISOString(),
    })
  },
}

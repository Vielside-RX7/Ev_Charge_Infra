/**
 * Virtual EV Trip Recorder
 * 
 * Lightweight in-memory and localStorage recorder for Virtual EV trips.
 * Records telemetry samples during simulated driving and aggregates
 * completed trip features for data collection.
 */

const STORAGE_KEY = 'voltguide_recorded_trips'

class TripRecorder {
  constructor() {
    this.currentTrip = null
    this.completedTrips = this.loadCompletedTrips()
  }

  loadCompletedTrips() {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const data = window.localStorage.getItem(STORAGE_KEY)
        return data ? JSON.parse(data) : []
      }
    } catch (e) {
      console.warn('Unable to load recorded trips from localStorage:', e)
    }
    return []
  }

  saveCompletedTrips() {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this.completedTrips))
      }
    } catch (e) {
      console.warn('Unable to save recorded trips to localStorage:', e)
    }
  }

  startTrip({
    vehicleId = 'VIRTUAL_EV_001',
    batteryCapacityKwh = 30.0,
    startingSocPercent = 20.0,
    distanceKm = 0.0,
    estimatedDurationMinutes = 0.0,
    drivingStyle = 'Normal',
  } = {}) {
    this.currentTrip = {
      trip_id: `TRIP_${Date.now()}`,
      vehicle_id: vehicleId,
      battery_capacity_kwh: Number(batteryCapacityKwh),
      starting_soc_percent: Number(startingSocPercent),
      planned_distance_km: Number(distanceKm),
      estimated_duration_minutes: Number(estimatedDurationMinutes),
      driving_style: drivingStyle,
      start_time: new Date().toISOString(),
      samples: [],
    }
  }

  recordSample({
    latitude,
    longitude,
    speedKmph,
    currentSocPercent,
    energyConsumedKwh,
    timestamp = new Date().toISOString(),
  }) {
    if (!this.currentTrip) return

    this.currentTrip.samples.push({
      latitude,
      longitude,
      speed_kmph: speedKmph,
      soc_percent: currentSocPercent,
      energy_consumed_kwh: energyConsumedKwh,
      timestamp,
    })
  }

  completeTrip({
    finalDistanceKm,
    finalDurationMinutes,
    finalEnergyConsumedKwh,
  }) {
    if (!this.currentTrip) return null

    const samples = this.currentTrip.samples
    const speeds = samples.map((s) => s.speed_kmph).filter((s) => typeof s === 'number' && s > 0)
    const avgSpeed = speeds.length > 0
      ? parseFloat((speeds.reduce((a, b) => a + b, 0) / speeds.length).toFixed(1))
      : 40.0

    const completedRecord = {
      trip_id: this.currentTrip.trip_id,
      vehicle_id: this.currentTrip.vehicle_id,
      battery_capacity_kwh: this.currentTrip.battery_capacity_kwh,
      starting_soc_percent: this.currentTrip.starting_soc_percent,
      distance_km: parseFloat(Number(finalDistanceKm).toFixed(2)),
      trip_duration_minutes: parseFloat(Number(finalDurationMinutes).toFixed(1)),
      average_speed_kmph: avgSpeed,
      driving_style: this.currentTrip.driving_style,
      energy_consumed_kwh: parseFloat(Number(finalEnergyConsumedKwh).toFixed(3)),
      sample_count: samples.length,
      completed_at: new Date().toISOString(),
      status: 'completed',
    }

    this.completedTrips.push(completedRecord)
    this.saveCompletedTrips()
    this.currentTrip = null
    return completedRecord
  }

  resetTrip() {
    // Incomplete trips are discarded upon reset unless explicitly marked
    this.currentTrip = null
  }

  getCompletedTrips() {
    return [...this.completedTrips]
  }

  clearRecordedTrips() {
    this.completedTrips = []
    this.saveCompletedTrips()
  }
}

export const tripRecorder = new TripRecorder()

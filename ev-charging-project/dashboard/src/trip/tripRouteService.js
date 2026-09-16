/**
 * Trip Route Service
 * 
 * Communicates with the backend OSRM routing endpoint to retrieve
 * actual driving road routes between the Virtual EV origin and selected destination.
 */

import axios from 'axios'

const API_BASE_URL =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) ||
  'http://localhost:8000'

/**
 * Fetches the road driving route between vehicle origin and destination.
 * 
 * @param {Object} params
 * @param {number} params.originLat - Live vehicle latitude
 * @param {number} params.originLon - Live vehicle longitude
 * @param {number} params.destinationLat - Target destination latitude
 * @param {number} params.destinationLon - Target destination longitude
 * @returns {Promise<Object>} Route data containing distance_km, travel_time_minutes, geometry, polylinePoints
 */
export async function fetchTripRoute({
  originLat,
  originLon,
  destinationLat,
  destinationLon,
}) {
  if (
    typeof originLat !== 'number' ||
    typeof originLon !== 'number' ||
    typeof destinationLat !== 'number' ||
    typeof destinationLon !== 'number'
  ) {
    throw new Error('Invalid coordinates supplied to fetchTripRoute.')
  }

  const payload = {
    user_lat: Number(originLat),
    user_lon: Number(originLon),
    charger_lat: Number(destinationLat),
    charger_lon: Number(destinationLon),
  }

  const response = await axios.post(`${API_BASE_URL}/route`, payload, {
    headers: { 'Content-Type': 'application/json' },
  })

  const data = response.data

  // Convert GeoJSON LineString coordinates [[lon, lat], ...] to Leaflet [[lat, lon], ...]
  let polylinePoints = []
  if (data?.geometry?.coordinates && Array.isArray(data.geometry.coordinates)) {
    polylinePoints = data.geometry.coordinates.map(([lon, lat]) => [lat, lon])
  } else {
    // Direct line fallback
    polylinePoints = [
      [Number(originLat), Number(originLon)],
      [Number(destinationLat), Number(destinationLon)],
    ]
  }

  return {
    distance_km: Number(data.distance_km),
    travel_time_minutes: Number(data.travel_time_minutes),
    geometry: data.geometry,
    polylinePoints,
    is_fallback: Boolean(data.is_fallback),
  }
}

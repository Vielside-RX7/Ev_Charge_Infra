import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react'
import { virtualObdProvider } from './virtualObdProvider'

const TelemetryContext = createContext(null)

/**
 * TelemetryProvider wraps the application and supplies current vehicle telemetry.
 * 
 * Architecture:
 *   [Telemetry Provider (Virtual OBD or future Real OBD)]
 *                 ↓
 *      [Standard Vehicle Telemetry]
 *                 ↓
 *        [TelemetryContext]
 *                 ↓
 *     [React UI & Application]
 * 
 * Features:
 * 1. Live GPS Initialization: Checks browser Geolocation API on mount once.
 *    If granted, sets starting coordinates and marks locationSource = 'gps'.
 *    Otherwise retains predefined coordinates with locationSource = 'fallback'.
 * 2. Immutable Original Baseline: Keeps initialStateRef to ensure vehicle
 *    can always be reliably restored to original starting state via resetTelemetry().
 */
export function TelemetryProvider({ children, initialValues, provider = virtualObdProvider }) {
  // Captured immutable baseline for Reset functionality
  const initialStateRef = useRef(provider.createInitialTelemetry(initialValues || {}))

  const [telemetry, setTelemetry] = useState(() => initialStateRef.current)
  const [locationSource, setLocationSource] = useState('fallback') // 'gps' | 'fallback'
  const [gpsMessage, setGpsMessage] = useState(null)
  const hasAttemptedGpsRef = useRef(false)

  // Live GPS Initialization on initial mount
  useEffect(() => {
    if (hasAttemptedGpsRef.current) return
    hasAttemptedGpsRef.current = true

    if (typeof navigator !== 'undefined' && 'geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = parseFloat(position.coords.latitude.toFixed(6))
          const lon = parseFloat(position.coords.longitude.toFixed(6))

          // Update immutable baseline with real GPS starting location
          initialStateRef.current = {
            ...initialStateRef.current,
            latitude: lat,
            longitude: lon,
          }

          setLocationSource('gps')
          setGpsMessage(null)

          // Only apply GPS coordinates if vehicle is still parked at initial state
          setTelemetry((prev) => {
            if (prev.vehicle_status === 'Standby (Parked)' && prev.energy_consumed_kwh === 0) {
              return {
                ...prev,
                latitude: lat,
                longitude: lon,
                timestamp: new Date().toISOString(),
              }
            }
            return prev
          })
        },
        (error) => {
          console.warn('GPS initialization fallback (permission denied or unavailable):', error.message)
          setLocationSource('fallback')
          setGpsMessage('Location permission not granted. Using simulation fallback.')
        },
        {
          enableHighAccuracy: true,
          timeout: 5000,
          maximumAge: 60000,
        }
      )
    } else {
      setLocationSource('fallback')
      setGpsMessage('Geolocation not supported by browser. Using simulation fallback.')
    }
  }, [])

  const updateTelemetry = useCallback((updates) => {
    setTelemetry((prev) => provider.produceTelemetry(prev, updates))
  }, [provider])

  const chargeTelemetry = useCallback((energyAddedKwh, status) => {
    setTelemetry((prev) => provider.produceChargingTelemetry(prev, energyAddedKwh, status))
  }, [provider])

  const resetTelemetry = useCallback(() => {
    setTelemetry(provider.resetToInitial(initialStateRef.current))
  }, [provider])

  const value = {
    telemetry,
    setTelemetry,
    updateTelemetry,
    chargeTelemetry,
    resetTelemetry,
    locationSource,
    gpsMessage,
    initialState: initialStateRef.current,
    isConnected: telemetry.connection_status === 'Connected',
    dataSource: telemetry.data_source,
    provider,
    providerName: provider.name,
  }

  return (
    <TelemetryContext.Provider value={value}>
      {children}
    </TelemetryContext.Provider>
  )
}

/**
 * Hook to consume the telemetry layer from any UI component.
 */
export function useTelemetry() {
  const context = useContext(TelemetryContext)
  if (!context) {
    throw new Error('useTelemetry must be used within a TelemetryProvider')
  }
  return context
}

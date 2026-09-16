import React, { createContext, useContext, useState, useCallback } from 'react'
import { createVehicleProfile } from './vehicleProfile'

const VehicleProfileContext = createContext(null)

/**
 * VehicleProfileProvider wraps the application and supplies the active vehicle profile.
 * 
 * The vehicle profile represents static vehicle configuration (identity, capacity,
 * connector type) while live dynamic state (SoC, location, speed) remains in TelemetryContext.
 * 
 * Currently supports a single default Virtual EV profile.
 * Future phases may add multiple saved vehicles or user-configured profiles.
 */
export function VehicleProfileProvider({ children, initialProfile }) {
  const [vehicleProfile, setVehicleProfile] = useState(() =>
    createVehicleProfile(initialProfile || {})
  )

  const updateProfile = useCallback((updates) => {
    setVehicleProfile((prev) => ({ ...prev, ...updates }))
  }, [])

  const value = {
    vehicleProfile,
    setVehicleProfile,
    updateProfile,
  }

  return (
    <VehicleProfileContext.Provider value={value}>
      {children}
    </VehicleProfileContext.Provider>
  )
}

/**
 * Hook to consume the active vehicle profile from any UI component.
 */
export function useVehicleProfile() {
  const context = useContext(VehicleProfileContext)
  if (!context) {
    throw new Error('useVehicleProfile must be used within a VehicleProfileProvider')
  }
  return context
}

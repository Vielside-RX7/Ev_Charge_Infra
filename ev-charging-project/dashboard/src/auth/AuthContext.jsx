import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { useVehicleProfile } from '../telemetry/VehicleProfileContext'
import { useTelemetry } from '../telemetry/TelemetryContext'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const TOKEN_STORAGE_KEY = 'voltguide_auth_token'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const { setVehicleProfile } = useVehicleProfile()
  const { updateTelemetry } = useTelemetry()

  const [token, setToken] = useState(() => {
    try {
      return localStorage.getItem(TOKEN_STORAGE_KEY) || null
    } catch {
      return null
    }
  })

  const [user, setUser] = useState(null)
  const [vehicles, setVehicles] = useState([])
  const [selectedVehicle, setSelectedVehicle] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false)
  const [isAccountModalOpen, setIsAccountModalOpen] = useState(false)
  const [authError, setAuthError] = useState(null)

  // Configure global axios auth header
  const setAuthHeader = useCallback((jwtToken) => {
    if (jwtToken) {
      axios.defaults.headers.common['Authorization'] = `Bearer ${jwtToken}`
      try {
        localStorage.setItem(TOKEN_STORAGE_KEY, jwtToken)
      } catch (err) {
        console.warn('LocalStorage unavailable:', err)
      }
    } else {
      delete axios.defaults.headers.common['Authorization']
      try {
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      } catch (err) {
        console.warn('LocalStorage unavailable:', err)
      }
    }
  }, [])

  // Sync selected vehicle to VehicleProfile and Telemetry
  const syncActiveVehicle = useCallback((vehicle) => {
    if (!vehicle) return
    setSelectedVehicle(vehicle)
    
    // Update static vehicle profile
    setVehicleProfile({
      vehicle_id: `VEHICLE_${vehicle.id}`,
      vehicle_name: vehicle.vehicle_name,
      battery_capacity_kwh: Number(vehicle.battery_capacity_kwh),
      connector_type: vehicle.connector_type,
      data_source: 'Virtual OBD',
    })

    // Update live telemetry layer
    updateTelemetry({
      vehicle_name: vehicle.vehicle_name,
      battery_capacity_kwh: Number(vehicle.battery_capacity_kwh),
      connector_type: vehicle.connector_type,
    })
  }, [setVehicleProfile, updateTelemetry])

  // Hydrate session on mount
  const refreshUser = useCallback(async (authToken = token) => {
    if (!authToken) {
      setUser(null)
      setVehicles([])
      setSelectedVehicle(null)
      setIsLoading(false)
      return null
    }

    try {
      setAuthHeader(authToken)
      const res = await axios.get(`${API_BASE_URL}/auth/me`)
      const data = res.data
      setUser(data.user)
      setVehicles(data.vehicles || [])
      if (data.selected_vehicle) {
        syncActiveVehicle(data.selected_vehicle)
      }
      return data
    } catch (err) {
      console.warn('Auth token expired or invalid:', err?.message)
      setToken(null)
      setAuthHeader(null)
      setUser(null)
      setVehicles([])
      setSelectedVehicle(null)
      return null
    } finally {
      setIsLoading(false)
    }
  }, [token, setAuthHeader, syncActiveVehicle])

  useEffect(() => {
    refreshUser(token)
  }, [])

  // Login handler
  const login = useCallback(async (email, password) => {
    setAuthError(null)
    try {
      const res = await axios.post(`${API_BASE_URL}/auth/login`, {
        email: email.trim(),
        password,
      })
      const data = res.data
      setToken(data.token)
      setAuthHeader(data.token)
      setUser(data.user)
      setVehicles(data.vehicles || [])
      if (data.selected_vehicle) {
        syncActiveVehicle(data.selected_vehicle)
      }
      setIsAuthModalOpen(false)
      return { success: true, user: data.user }
    } catch (err) {
      const msg = err.response?.data?.detail || 'Login failed. Please check credentials.'
      setAuthError(msg)
      return { success: false, error: msg }
    }
  }, [setAuthHeader, syncActiveVehicle])

  // Signup handler
  const signup = useCallback(async (formData) => {
    setAuthError(null)
    try {
      const res = await axios.post(`${API_BASE_URL}/auth/signup`, formData)
      const data = res.data
      setToken(data.token)
      setAuthHeader(data.token)
      setUser(data.user)
      setVehicles(data.vehicles || [])
      if (data.selected_vehicle) {
        syncActiveVehicle(data.selected_vehicle)
      }
      setIsAuthModalOpen(false)
      return { success: true, user: data.user }
    } catch (err) {
      const msg = err.response?.data?.detail || 'Registration failed. Please try again.'
      setAuthError(msg)
      return { success: false, error: msg }
    }
  }, [setAuthHeader, syncActiveVehicle])

  // Logout handler
  const logout = useCallback(async () => {
    try {
      await axios.post(`${API_BASE_URL}/auth/logout`)
    } catch {
      // Ignore network failures on logout
    }
    setToken(null)
    setAuthHeader(null)
    setUser(null)
    setVehicles([])
    setSelectedVehicle(null)
    setIsAccountModalOpen(false)
  }, [setAuthHeader])

  // Select active vehicle
  const selectVehicle = useCallback(async (vehicleId) => {
    try {
      const res = await axios.post(`${API_BASE_URL}/vehicles/${vehicleId}/select`)
      const updatedVehicle = res.data
      setVehicles((prev) =>
        prev.map((v) => ({ ...v, is_selected: v.id === vehicleId }))
      )
      syncActiveVehicle(updatedVehicle)
      return { success: true, vehicle: updatedVehicle }
    } catch (err) {
      return { success: false, error: err.response?.data?.detail || 'Failed to select vehicle' }
    }
  }, [syncActiveVehicle])

  // Add new vehicle profile
  const addVehicle = useCallback(async (vehicleData) => {
    try {
      const res = await axios.post(`${API_BASE_URL}/vehicles`, vehicleData)
      const newV = res.data
      setVehicles((prev) => [...prev, newV])
      if (newV.is_selected || vehicles.length === 0) {
        syncActiveVehicle(newV)
      }
      return { success: true, vehicle: newV }
    } catch (err) {
      return { success: false, error: err.response?.data?.detail || 'Failed to add vehicle' }
    }
  }, [vehicles.length, syncActiveVehicle])

  // Delete vehicle profile
  const deleteVehicle = useCallback(async (vehicleId) => {
    try {
      await axios.delete(`${API_BASE_URL}/vehicles/${vehicleId}`)
      setVehicles((prev) => prev.filter((v) => v.id !== vehicleId))
      // Refresh to sync selection if the deleted vehicle was active
      await refreshUser(token)
      return { success: true }
    } catch (err) {
      return { success: false, error: err.response?.data?.detail || 'Failed to delete vehicle' }
    }
  }, [token, refreshUser])

  const value = {
    token,
    user,
    vehicles,
    selectedVehicle,
    isAuthenticated: Boolean(user && token),
    isLoading,
    authError,
    setAuthError,
    isAuthModalOpen,
    setIsAuthModalOpen,
    isAccountModalOpen,
    setIsAccountModalOpen,
    login,
    signup,
    logout,
    refreshUser,
    selectVehicle,
    addVehicle,
    deleteVehicle,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

import React, { useState } from 'react'
import { useTelemetry } from '../telemetry/TelemetryContext'
import { useVehicleProfile } from '../telemetry/VehicleProfileContext'

export default function RecommendationForm({ onSubmit, isLoading }) {
  const { telemetry } = useTelemetry()
  const { vehicleProfile } = useVehicleProfile()

  const [isOpen, setIsOpen] = useState(false)
  const [formData, setFormData] = useState({
    target_soc_percent: 90.0,
    max_search_radius_km: 25.0,
    top_n: 5,
  })

  const [errors, setErrors] = useState({})

  const handleChange = (e) => {
    const { name, value, type } = e.target
    const parsedValue = type === 'number' ? (value === '' ? '' : parseFloat(value)) : value
    setFormData((prev) => ({ ...prev, [name]: parsedValue }))
    if (errors[name]) {
      setErrors((prev) => ({ ...prev, [name]: '' }))
    }
  }

  const validate = () => {
    const newErrors = {}
    const currentSoc = Number(telemetry.current_soc_percent)
    const targetSoc = Number(formData.target_soc_percent)

    if (formData.target_soc_percent === '' || isNaN(targetSoc) || targetSoc < 0 || targetSoc > 100) {
      newErrors.target_soc_percent = 'Must be between 0% and 100%.'
    } else if (targetSoc <= currentSoc) {
      newErrors.target_soc_percent = `Target (${targetSoc}%) must exceed current SoC (${currentSoc.toFixed(0)}%).`
    }

    if (formData.max_search_radius_km === '' || isNaN(formData.max_search_radius_km) || formData.max_search_radius_km <= 0) {
      newErrors.max_search_radius_km = 'Radius must be > 0 km.'
    }

    if (formData.top_n === '' || isNaN(formData.top_n) || formData.top_n < 1 || formData.top_n > 25) {
      newErrors.top_n = 'Top count must be between 1 and 25.'
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (validate()) {
      const payload = {
        user_lat: Number(telemetry.latitude),
        user_lon: Number(telemetry.longitude),
        battery_capacity_kwh: Number(vehicleProfile.battery_capacity_kwh),
        current_soc_percent: Number(telemetry.current_soc_percent),
        connector_type: vehicleProfile.connector_type,
        target_soc_percent: Number(formData.target_soc_percent),
        max_search_radius_km: Number(formData.max_search_radius_km),
        top_n: Number(formData.top_n),
      }
      onSubmit(payload)
    }
  }

  return (
    <div className="py-4 border-t border-white/5 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <span className="label-subhead block">Nearby Stations</span>
          <p className="label-quiet">Discover and rank standalone chargers in your vicinity</p>
        </div>

        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="btn-secondary px-4 py-1.5 text-[11px] cursor-pointer"
        >
          {isOpen ? 'Close search' : 'Search stations'}
        </button>
      </div>

      {isOpen && (
        <form onSubmit={handleSubmit} className="pt-2 space-y-4 transition-all">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label htmlFor="target_soc_percent" className="label-quiet block mb-1">
                Target SoC (%)
              </label>
              <input
                id="target_soc_percent"
                name="target_soc_percent"
                type="number"
                step="1"
                min="0"
                max="100"
                value={formData.target_soc_percent}
                onChange={handleChange}
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white font-mono tabular-nums focus:outline-none focus:border-white/25 transition"
              />
              {errors.target_soc_percent && (
                <p className="text-[10px] text-rose-400 mt-1">{errors.target_soc_percent}</p>
              )}
            </div>

            <div>
              <label htmlFor="max_search_radius_km" className="label-quiet block mb-1">
                Search Radius (km)
              </label>
              <input
                id="max_search_radius_km"
                name="max_search_radius_km"
                type="number"
                step="1"
                min="1"
                value={formData.max_search_radius_km}
                onChange={handleChange}
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white font-mono tabular-nums focus:outline-none focus:border-white/25 transition"
              />
              {errors.max_search_radius_km && (
                <p className="text-[10px] text-rose-400 mt-1">{errors.max_search_radius_km}</p>
              )}
            </div>

            <div>
              <label htmlFor="top_n" className="label-quiet block mb-1">
                Max Stations
              </label>
              <input
                id="top_n"
                name="top_n"
                type="number"
                step="1"
                min="1"
                max="25"
                value={formData.top_n}
                onChange={handleChange}
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white font-mono tabular-nums focus:outline-none focus:border-white/25 transition"
              />
              {errors.top_n && (
                <p className="text-[10px] text-rose-400 mt-1">{errors.top_n}</p>
              )}
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="btn-primary w-full py-2.5 text-xs font-medium flex items-center justify-center gap-2 cursor-pointer transition disabled:opacity-40"
          >
            {isLoading ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin rounded-full h-3 w-3 border-2 border-black border-t-transparent"></span>
                <span>Ranking Charging Network...</span>
              </span>
            ) : (
              <span>Find Recommended Stations</span>
            )}
          </button>
        </form>
      )}
    </div>
  )
}

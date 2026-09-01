import React, { useState } from 'react'

const CONNECTOR_OPTIONS = ['CCS2', 'Type 2', 'CHAdeMO', 'GB/T']

export default function RecommendationForm({ onSubmit, isLoading }) {
  const [formData, setFormData] = useState({
    user_lat: 12.2958,
    user_lon: 76.6394,
    connector_type: 'CCS2',
    battery_capacity_kwh: 30.0,
    current_soc_percent: 20.0,
    target_soc_percent: 90.0,
    max_search_radius_km: 25.0,
    top_n: 5,
  })

  const [errors, setErrors] = useState({})
  const [geoStatus, setGeoStatus] = useState('')

  const handleChange = (e) => {
    const { name, value, type } = e.target
    const parsedValue = type === 'number' ? (value === '' ? '' : parseFloat(value)) : value
    setFormData((prev) => ({ ...prev, [name]: parsedValue }))
    // Clear field-specific error on change
    if (errors[name]) {
      setErrors((prev) => ({ ...prev, [name]: '' }))
    }
  }

  const handleUseMyLocation = () => {
    if (!navigator.geolocation) {
      setGeoStatus('Geolocation is not supported by your browser.')
      return
    }

    setGeoStatus('Locating...')
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setFormData((prev) => ({
          ...prev,
          user_lat: parseFloat(position.coords.latitude.toFixed(5)),
          user_lon: parseFloat(position.coords.longitude.toFixed(5)),
        }))
        setGeoStatus('Location updated!')
        setErrors((prev) => ({ ...prev, user_lat: '', user_lon: '' }))
        setTimeout(() => setGeoStatus(''), 3000)
      },
      (error) => {
        let msg = 'Unable to retrieve location.'
        if (error.code === error.PERMISSION_DENIED) {
          msg = 'Location permission denied. Please enter coordinates manually.'
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          msg = 'Location information unavailable.'
        } else if (error.code === error.TIMEOUT) {
          msg = 'Location request timed out.'
        }
        setGeoStatus(msg)
      },
      { timeout: 10000, enableHighAccuracy: true }
    )
  }

  const validate = () => {
    const newErrors = {}

    if (formData.user_lat === '' || isNaN(formData.user_lat)) {
      newErrors.user_lat = 'Latitude is required and must be a number.'
    } else if (formData.user_lat < -90 || formData.user_lat > 90) {
      newErrors.user_lat = 'Latitude must be between -90 and 90.'
    }

    if (formData.user_lon === '' || isNaN(formData.user_lon)) {
      newErrors.user_lon = 'Longitude is required and must be a number.'
    } else if (formData.user_lon < -180 || formData.user_lon > 180) {
      newErrors.user_lon = 'Longitude must be between -180 and 180.'
    }

    if (!formData.connector_type) {
      newErrors.connector_type = 'Please select a connector type.'
    }

    if (formData.battery_capacity_kwh === '' || isNaN(formData.battery_capacity_kwh) || formData.battery_capacity_kwh <= 0) {
      newErrors.battery_capacity_kwh = 'Battery capacity must be greater than 0 kWh.'
    }

    if (formData.current_soc_percent === '' || isNaN(formData.current_soc_percent) || formData.current_soc_percent < 0 || formData.current_soc_percent > 100) {
      newErrors.current_soc_percent = 'Current SoC must be between 0% and 100%.'
    }

    if (formData.target_soc_percent === '' || isNaN(formData.target_soc_percent) || formData.target_soc_percent < 0 || formData.target_soc_percent > 100) {
      newErrors.target_soc_percent = 'Target SoC must be between 0% and 100%.'
    } else if (
      formData.current_soc_percent !== '' &&
      !isNaN(formData.current_soc_percent) &&
      formData.target_soc_percent <= formData.current_soc_percent
    ) {
      newErrors.target_soc_percent = `Target SoC (${formData.target_soc_percent}%) must be strictly greater than Current SoC (${formData.current_soc_percent}%).`
    }

    if (formData.max_search_radius_km === '' || isNaN(formData.max_search_radius_km) || formData.max_search_radius_km <= 0) {
      newErrors.max_search_radius_km = 'Search radius must be greater than 0 km.'
    }

    if (formData.top_n === '' || isNaN(formData.top_n) || formData.top_n < 1 || formData.top_n > 25) {
      newErrors.top_n = 'Top N must be between 1 and 25.'
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (validate()) {
      onSubmit(formData)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-md p-6 border border-gray-100">
      <h2 className="text-xl font-bold text-gray-800 mb-4 flex items-center gap-2">
        <span>🚗</span> Trip & Vehicle Parameters
      </h2>

      {/* Location Row */}
      <div className="mb-4">
        <div className="flex items-center justify-between mb-1">
          <label className="block text-sm font-semibold text-gray-700">Driver Coordinates</label>
          <button
            type="button"
            onClick={handleUseMyLocation}
            className="text-xs bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-medium px-2.5 py-1 rounded transition-colors"
          >
            📍 Use My Location
          </button>
        </div>
        {geoStatus && (
          <p className="text-xs text-indigo-600 mb-2 italic">{geoStatus}</p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label htmlFor="user_lat" className="block text-xs text-gray-500 mb-1">Your Latitude</label>
            <input
              id="user_lat"
              name="user_lat"
              type="number"
              step="any"
              value={formData.user_lat}
              onChange={handleChange}
              placeholder="e.g. 12.2958"
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
                errors.user_lat ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
              }`}
            />
            {errors.user_lat && <p className="text-xs text-red-600 mt-1">{errors.user_lat}</p>}
          </div>

          <div>
            <label htmlFor="user_lon" className="block text-xs text-gray-500 mb-1">Your Longitude</label>
            <input
              id="user_lon"
              name="user_lon"
              type="number"
              step="any"
              value={formData.user_lon}
              onChange={handleChange}
              placeholder="e.g. 76.6394"
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
                errors.user_lon ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
              }`}
            />
            {errors.user_lon && <p className="text-xs text-red-600 mt-1">{errors.user_lon}</p>}
          </div>
        </div>
      </div>

      {/* Vehicle Specs Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        <div>
          <label htmlFor="connector_type" className="block text-sm font-semibold text-gray-700 mb-1">
            Connector Standard
          </label>
          <select
            id="connector_type"
            name="connector_type"
            value={formData.connector_type}
            onChange={handleChange}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
          >
            {CONNECTOR_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
          {errors.connector_type && <p className="text-xs text-red-600 mt-1">{errors.connector_type}</p>}
        </div>

        <div>
          <label htmlFor="battery_capacity_kwh" className="block text-sm font-semibold text-gray-700 mb-1">
            Battery Capacity (kWh)
          </label>
          <input
            id="battery_capacity_kwh"
            name="battery_capacity_kwh"
            type="number"
            step="0.1"
            min="1"
            value={formData.battery_capacity_kwh}
            onChange={handleChange}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              errors.battery_capacity_kwh ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
            }`}
          />
          {errors.battery_capacity_kwh && <p className="text-xs text-red-600 mt-1">{errors.battery_capacity_kwh}</p>}
        </div>
      </div>

      {/* SoC Settings Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
        <div>
          <label htmlFor="current_soc_percent" className="block text-sm font-semibold text-gray-700 mb-1">
            Current Battery SoC (%)
          </label>
          <input
            id="current_soc_percent"
            name="current_soc_percent"
            type="number"
            step="1"
            min="0"
            max="100"
            value={formData.current_soc_percent}
            onChange={handleChange}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              errors.current_soc_percent ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
            }`}
          />
          {errors.current_soc_percent && <p className="text-xs text-red-600 mt-1">{errors.current_soc_percent}</p>}
        </div>

        <div>
          <label htmlFor="target_soc_percent" className="block text-sm font-semibold text-gray-700 mb-1">
            Target Battery SoC (%)
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
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              errors.target_soc_percent ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
            }`}
          />
          {errors.target_soc_percent && <p className="text-xs text-red-600 mt-1">{errors.target_soc_percent}</p>}
        </div>
      </div>

      {/* Search Preferences Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
        <div>
          <label htmlFor="max_search_radius_km" className="block text-sm font-semibold text-gray-700 mb-1">
            Max Radius (km)
          </label>
          <input
            id="max_search_radius_km"
            name="max_search_radius_km"
            type="number"
            step="1"
            min="1"
            value={formData.max_search_radius_km}
            onChange={handleChange}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              errors.max_search_radius_km ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
            }`}
          />
          {errors.max_search_radius_km && <p className="text-xs text-red-600 mt-1">{errors.max_search_radius_km}</p>}
        </div>

        <div>
          <label htmlFor="top_n" className="block text-sm font-semibold text-gray-700 mb-1">
            Top Recommendations (1-25)
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
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              errors.top_n ? 'border-red-500 focus:ring-red-200' : 'border-gray-300 focus:ring-blue-200'
            }`}
          />
          {errors.top_n && <p className="text-xs text-red-600 mt-1">{errors.top_n}</p>}
        </div>
      </div>

      {/* Submit Button */}
      <button
        type="submit"
        disabled={isLoading}
        className={`w-full py-3 px-4 rounded-lg font-semibold text-white transition-colors flex items-center justify-center gap-2 ${
          isLoading
            ? 'bg-blue-400 cursor-not-allowed'
            : 'bg-blue-600 hover:bg-blue-700 active:bg-blue-800 shadow-sm'
        }`}
      >
        {isLoading ? (
          <>
            <svg className="animate-spin h-5 w-5 text-white" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span>Finding Best Chargers...</span>
          </>
        ) : (
          <span>⚡ Find Recommended Chargers</span>
        )}
      </button>
    </form>
  )
}

import React, { useState } from 'react'
import axios from 'axios'
import RecommendationForm from './components/RecommendationForm'
import MapView from './components/MapView'
import ResultsList from './components/ResultsList'
import NavigationView from './components/NavigationView'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function App() {
  const [results, setResults] = useState(null)
  const [userLocation, setUserLocation] = useState(null)
  const [selectedChargerId, setSelectedChargerId] = useState(null)
  const [navigatingCharger, setNavigatingCharger] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)

  const handleFormSubmit = async (formData) => {
    setIsLoading(true)
    setError(null)
    setResults(null)
    setSelectedChargerId(null)
    setNavigatingCharger(null)
    setUserLocation({ lat: formData.user_lat, lon: formData.user_lon })

    try {
      const response = await axios.post(`${API_BASE_URL}/recommend`, formData, {
        headers: { 'Content-Type': 'application/json' },
      })
      setResults(response.data)
      if (response.data && response.data.length > 0) {
        setSelectedChargerId(response.data[0].charger_id)
      }
    } catch (err) {
      console.error('API Error:', err)
      if (!err.response) {
        setError({
          title: 'Network Error: Backend Unreachable',
          message: `Unable to connect to the recommendation API at ${API_BASE_URL}.`,
          suggestion: 'Ensure the FastAPI backend is running via `uvicorn api.main:app --port 8000`.',
        })
      } else if (err.response.status === 400) {
        const d = err.response.data.detail
        const isStranded = typeof d === 'object' && d?.error === 'VEHICLE_STRANDED'
        setError({
          title: isStranded ? '🚨 Vehicle Stranded Alert' : 'Request Error (HTTP 400)',
          message: typeof d === 'object' ? d.message : d,
          suggestion: typeof d === 'object' ? d.suggestion : 'Please verify input parameters.',
        })
      } else if (err.response.status === 404) {
        setError({
          title: 'No Stations Found',
          message: err.response.data.detail || 'No charging stations found within the specified search radius.',
          suggestion: 'Try increasing the search radius (e.g. to 35 km) or expanding coordinates.',
        })
      } else if (err.response.status === 422) {
        const details = err.response.data.detail
        let message = 'Invalid request parameters.'
        if (Array.isArray(details)) {
          message = details
            .map((d) => `${d.loc ? d.loc.slice(1).join('.') : ''}: ${d.msg}`)
            .join('; ')
        } else if (typeof details === 'string') {
          message = details
        }
        setError({
          title: 'Validation Error (HTTP 422)',
          message: message,
          suggestion: 'Please verify that current SoC is less than target SoC and all numeric values are positive.',
        })
      } else {
        setError({
          title: `Server Error (${err.response.status})`,
          message: err.response.data.detail || err.message || 'An unexpected error occurred.',
        })
      }
    } finally {
      setIsLoading(false)
    }
  }

  const handleSelectCharger = (chargerId) => {
    setSelectedChargerId(chargerId)
  }

  const handleStartNavigation = (charger) => {
    setNavigatingCharger(charger)
  }

  const handleExitNavigation = () => {
    setNavigatingCharger(null)
  }

  return (
    <div className="min-h-screen bg-slate-100 text-gray-800">
      {/* Full-Screen Live Navigation Mode (Option 1) */}
      {navigatingCharger && (
        <NavigationView
          charger={navigatingCharger}
          userLocation={userLocation}
          onExit={handleExitNavigation}
        />
      )}

      {/* Header */}
      <header className="bg-slate-900 text-white shadow-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-5 flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="text-3xl">⚡</span>
            <div>
              <h1 className="text-2xl font-black tracking-tight text-white">
                VoltGuide AI
              </h1>
              <p className="text-xs text-slate-400">
                Intelligent EV Charging Infrastructure Recommendation Platform • Mysuru
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs bg-slate-800 px-3 py-1.5 rounded-full border border-slate-700">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span className="text-slate-300 font-medium">ML Models Active</span>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          {/* Left Column: Input Form */}
          <div className="lg:col-span-5">
            <RecommendationForm onSubmit={handleFormSubmit} isLoading={isLoading} />
          </div>

          {/* Right Column: Interactive Map & Results List */}
          <div className="lg:col-span-7 space-y-6">
            {results && results.length > 0 && (
              <MapView
                results={results}
                userLocation={userLocation}
                selectedChargerId={selectedChargerId}
                onSelectCharger={handleSelectCharger}
                onStartNavigation={handleStartNavigation}
              />
            )}

            <ResultsList
              results={results}
              isLoading={isLoading}
              error={error}
              selectedChargerId={selectedChargerId}
              onSelectCharger={handleSelectCharger}
              onStartNavigation={handleStartNavigation}
            />
          </div>
        </div>
      </main>
    </div>
  )
}

export default App

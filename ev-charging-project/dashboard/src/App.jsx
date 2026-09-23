import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { TelemetryProvider } from './telemetry/TelemetryContext'
import { VehicleProfileProvider } from './telemetry/VehicleProfileContext'
import { TripProvider, useTrip } from './trip/TripContext'
import VirtualEvTelemetry from './components/VirtualEvTelemetry'
import ActiveTripCard from './components/ActiveTripCard'
import RecommendationForm from './components/RecommendationForm'
import MapView from './components/MapView'
import ResultsList from './components/ResultsList'
import NavigationView from './components/NavigationView'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function CockpitContent({
  results,
  userLocation,
  selectedChargerId,
  isLoading,
  error,
  handleFormSubmit,
  handleSelectCharger,
  handleStartNavigation,
}) {
  const { navState, NAV_STATE, trip } = useTrip()

  const isDrivingActive =
    navState === NAV_STATE.DRIVING ||
    navState === NAV_STATE.DRIVING_LEG_1 ||
    navState === NAV_STATE.DRIVING_LEG_2 ||
    navState === NAV_STATE.CHARGING ||
    navState === NAV_STATE.PAUSED

  return (
    <main className="max-w-[1680px] mx-auto px-6 lg:px-12 py-8 flex-1 w-full">
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 items-start">
        {/* Left Column: Editorial Product Narrative */}
        <div className="lg:col-span-5 space-y-4">
          {/* Subtle Hero Branding when Idle */}
          {!trip.destination_selected && (
            <div className="pb-3 border-b border-white/5 space-y-1">
              <span className="label-subhead text-cockpit-teal">VoltGuide</span>
              <h1 className="text-xl sm:text-2xl font-light text-white tracking-tight">
                Your intelligent EV journey.
              </h1>
            </div>
          )}

          {/* Section 1: Vehicle Battery & Estimated Range */}
          <VirtualEvTelemetry />

          {/* Section 2: Journey Corridor & Active Navigation Cockpit */}
          <ActiveTripCard />

          {/* Section 3: Charging Discovery (Suppressed during active driving or when destination journey is active) */}
          {!isDrivingActive && !trip.destination_selected && (
            <section className="pt-2">
              <RecommendationForm onSubmit={handleFormSubmit} isLoading={isLoading} />
              <ResultsList
                results={results}
                isLoading={isLoading}
                error={error}
                selectedChargerId={selectedChargerId}
                onSelectCharger={handleSelectCharger}
                onStartNavigation={handleStartNavigation}
              />
            </section>
          )}
        </div>

        {/* Right Column: Expansive Environmental Canvas */}
        <div className="lg:col-span-7 lg:sticky lg:top-20">
          <MapView
            results={results}
            userLocation={userLocation}
            selectedChargerId={selectedChargerId}
            onSelectCharger={handleSelectCharger}
            onStartNavigation={handleStartNavigation}
          />
        </div>
      </div>
    </main>
  )
}

function App() {
  const [results, setResults] = useState(null)
  const [userLocation, setUserLocation] = useState(null)
  const [selectedChargerId, setSelectedChargerId] = useState(null)
  const [navigatingCharger, setNavigatingCharger] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState(null)

  // Subtle clock for cockpit header
  const [currentTime, setCurrentTime] = useState(
    new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  )
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))
    }, 1000)
    return () => clearInterval(timer)
  }, [])

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
          title: 'Backend Unreachable',
          message: `Unable to connect to the recommendation API at ${API_BASE_URL}.`,
          suggestion: 'Ensure FastAPI backend is running via `uvicorn api.main:app --port 8000`.',
        })
      } else if (err.response.status === 400) {
        const d = err.response.data.detail
        const isStranded = typeof d === 'object' && d?.error === 'VEHICLE_STRANDED'
        setError({
          title: isStranded ? 'Vehicle Stranded Alert' : 'Request Error (400)',
          message: typeof d === 'object' ? d.message : d,
          suggestion: typeof d === 'object' ? d.suggestion : 'Please verify input parameters.',
        })
      } else if (err.response.status === 404) {
        setError({
          title: 'No Stations Found',
          message: err.response.data.detail || 'No charging stations found within the search radius.',
          suggestion: 'Try increasing the search radius or expanding coordinates.',
        })
      } else if (err.response.status === 422) {
        const details = err.response.data.detail
        let message = 'Invalid request parameters.'
        if (Array.isArray(details)) {
          message = details.map((d) => `${d.loc ? d.loc.slice(1).join('.') : ''}: ${d.msg}`).join('; ')
        } else if (typeof details === 'string') {
          message = details
        }
        setError({
          title: 'Validation Error (422)',
          message: message,
          suggestion: 'Verify that current SoC is less than target SoC and values are positive.',
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
    <VehicleProfileProvider>
      <TelemetryProvider>
        <TripProvider>
          <div className="min-h-screen bg-[#05070A] text-slate-100 font-sans selection:bg-cockpit-teal selection:text-black flex flex-col">
            {/* Full-Screen Standalone Navigation Mode (if triggered directly from card) */}
            {navigatingCharger && (
              <NavigationView
                charger={navigatingCharger}
                userLocation={userLocation}
                onExit={handleExitNavigation}
              />
            )}

            {/* Apple-style Minimalist Brand Bar */}
            <header className="sticky top-0 z-40 seamless-glass border-b border-white/5 px-6 lg:px-12 py-3.5">
              <div className="max-w-[1680px] mx-auto flex items-center justify-between">
                {/* Brand Title */}
                <div className="flex items-center gap-2.5">
                  <span className="font-medium text-sm tracking-tight text-white">
                    VoltGuide
                  </span>
                  <span className="text-slate-600">·</span>
                  <span className="label-quiet">
                    Intelligent EV Navigation
                  </span>
                </div>

                {/* Status & Region */}
                <div className="flex items-center gap-3 text-xs text-slate-400 font-mono tabular-nums">
                  <span>{currentTime}</span>
                  <span className="text-slate-600">·</span>
                  <span className="text-slate-300">Mysuru Region</span>
                </div>
              </div>
            </header>

            {/* Cockpit Content Area */}
            <CockpitContent
              results={results}
              userLocation={userLocation}
              selectedChargerId={selectedChargerId}
              isLoading={isLoading}
              error={error}
              handleFormSubmit={handleFormSubmit}
              handleSelectCharger={handleSelectCharger}
              handleStartNavigation={handleStartNavigation}
            />

            {/* Quiet Footer */}
            <footer className="py-6 px-6 lg:px-12 border-t border-white/5 text-center label-quiet">
              VoltGuide Navigation System · 0.150 kWh/km Deterministic Physics Baseline · XGBoost & LSTM Energy Prediction
            </footer>
          </div>
        </TripProvider>
      </TelemetryProvider>
    </VehicleProfileProvider>
  )
}

export default App

import React, { useState, useEffect, useRef, useMemo } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import L from 'leaflet'
import axios from 'axios'
import { useTelemetry } from '../telemetry/TelemetryContext'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Custom Cockpit Navigation DivIcons
// ---------------------------------------------------------------------------
const createLivePuckIcon = () =>
  L.divIcon({
    className: 'custom-live-puck-marker',
    html: `
      <div class="relative flex items-center justify-center">
        <span class="animate-ping absolute inline-flex h-10 w-10 rounded-full bg-cockpit-teal opacity-40"></span>
        <div class="relative w-7 h-7 rounded-full bg-[#06080C] border-2 border-cockpit-teal flex items-center justify-center shadow-xl">
          <div class="w-2.5 h-2.5 rounded-full bg-cockpit-teal"></div>
        </div>
      </div>
    `,
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    popupAnchor: [0, -20],
  })

const createDestinationIcon = (powerKw) =>
  L.divIcon({
    className: 'custom-dest-marker',
    html: `
      <div class="relative flex flex-col items-center">
        <div class="w-8 h-8 rounded-full bg-[#0E131F] border-2 border-amber-400 flex items-center justify-center text-amber-400 text-xs font-bold shadow-xl">
          ⚡
        </div>
        <div class="bg-[#06080C]/90 text-white font-mono text-[9px] px-1.5 py-0.5 rounded shadow mt-1 whitespace-nowrap border border-white/10">
          ${powerKw ? `${powerKw} kW` : 'Charger'}
        </div>
      </div>
    `,
    iconSize: [36, 50],
    iconAnchor: [18, 25],
    popupAnchor: [0, -25],
  })

// ---------------------------------------------------------------------------
// Map Navigation Controller (Smooth Pan & Follow)
// ---------------------------------------------------------------------------
function NavigationMapController({
  livePosition,
  routePoints,
  isFollowing,
  onUserDrag,
}) {
  const map = useMap()
  const initialFitDone = useRef(false)

  // Fit bounds once on route load
  useEffect(() => {
    if (routePoints && routePoints.length > 0 && !initialFitDone.current) {
      const bounds = L.latLngBounds(routePoints)
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 16 })
      initialFitDone.current = true
    }
  }, [routePoints, map])

  // Listen for user manual drag/pan to disable strict auto-follow
  useEffect(() => {
    const handleDragStart = () => {
      if (onUserDrag) onUserDrag()
    }
    map.on('dragstart', handleDragStart)
    return () => {
      map.off('dragstart', handleDragStart)
    }
  }, [map, onUserDrag])

  // Smooth follow when live position updates and isFollowing is active
  useEffect(() => {
    if (isFollowing && livePosition && livePosition[0] && livePosition[1]) {
      map.panTo([livePosition[0], livePosition[1]], {
        animate: true,
        duration: 0.8,
        easeLinearity: 0.25,
      })
    }
  }, [livePosition, isFollowing, map])

  return null
}

// ---------------------------------------------------------------------------
// Full-Screen Live Navigation View Component
// ---------------------------------------------------------------------------
export default function NavigationView({
  charger,
  userLocation,
  onExit,
}) {
  const { updateTelemetry, resetTelemetry } = useTelemetry()

  const [routeData, setRouteData] = useState(null)
  const [routePoints, setRoutePoints] = useState([])
  const [isLoadingRoute, setIsLoadingRoute] = useState(true)
  const [routeError, setRouteError] = useState(null)

  // Calculate simulated driving speed (km/h) reflecting route movement
  const simulatedSpeed = useMemo(() => {
    const dist = routeData?.distance_km ?? charger?.distance_km
    const timeMin = routeData?.travel_time_minutes ?? charger?.travel_time_minutes
    if (dist && timeMin && timeMin > 0) {
      const spd = Math.round(dist / (timeMin / 60))
      return spd > 0 ? spd : 45.0
    }
    return 45.0
  }, [routeData, charger])

  // Live Location State
  const userLat = userLocation?.lat ?? 12.2958
  const userLon = userLocation?.lon ?? 76.6394
  const initialPos = useMemo(() => [userLat, userLon], [userLat, userLon])

  const [livePosition, setLivePosition] = useState(initialPos)
  const [gpsStatus, setGpsStatus] = useState('initializing')
  const [gpsAccuracy, setGpsAccuracy] = useState(null)
  const [isFollowing, setIsFollowing] = useState(true)

  // Simulation Mode State
  const [isSimulating, setIsSimulating] = useState(false)
  const simulationProgressRef = useRef(0.0)
  const lastSimulationProgressRef = useRef(0.0)
  const simulationTimerRef = useRef(null)

  const totalDistanceKm = useMemo(() => {
    const dist = routeData?.distance_km ?? charger?.distance_km
    return typeof dist === 'number' && dist > 0 ? dist : 5.0
  }, [routeData, charger])

  // 1. Fetch Static Route on Mount
  useEffect(() => {
    let isMounted = true
    setIsLoadingRoute(true)
    setRouteError(null)

    const fetchRoute = async () => {
      try {
        const payload = {
          user_lat: userLat,
          user_lon: userLon,
          charger_id: charger.charger_id,
          charger_lat: charger.latitude,
          charger_lon: charger.longitude,
        }

        const res = await axios.post(`${API_BASE_URL}/route`, payload)
        if (!isMounted) return

        setRouteData(res.data)

        if (res.data?.geometry?.coordinates) {
          const latLngs = res.data.geometry.coordinates.map(([lon, lat]) => [lat, lon])
          setRoutePoints(latLngs)
        } else {
          setRoutePoints([[userLat, userLon], [charger.latitude, charger.longitude]])
        }
      } catch (err) {
        console.error('Failed to fetch route:', err)
        if (isMounted) {
          setRouteError('Could not load detailed road route. Using direct navigation line.')
          setRoutePoints([[userLat, userLon], [charger.latitude, charger.longitude]])
        }
      } finally {
        if (isMounted) setIsLoadingRoute(false)
      }
    }

    fetchRoute()
    return () => {
      isMounted = false
    }
  }, [charger?.charger_id, userLat, userLon])

  // 2. Continuous Geolocation Watcher
  useEffect(() => {
    if (isSimulating) {
      return
    }

    if (!('geolocation' in navigator)) {
      setGpsStatus('unavailable')
      return
    }

    let watchId = null

    try {
      watchId = navigator.geolocation.watchPosition(
        (pos) => {
          const lat = pos.coords.latitude
          const lon = pos.coords.longitude
          setLivePosition([lat, lon])
          setGpsAccuracy(Math.round(pos.coords.accuracy || 10))
          setGpsStatus('live')
        },
        (err) => {
          console.warn('Geolocation error / permission denied:', err.message)
          if (err.code === 1) {
            setGpsStatus('denied')
          } else {
            setGpsStatus('fallback')
          }
        },
        {
          enableHighAccuracy: true,
          maximumAge: 1000,
          timeout: 8000,
        }
      )
    } catch (e) {
      console.warn('Failed to start geolocation watch:', e)
      setGpsStatus('fallback')
    }

    return () => {
      if (watchId !== null) {
        navigator.geolocation.clearWatch(watchId)
      }
    }
  }, [isSimulating])

  // 3. Movement Simulation Engine
  useEffect(() => {
    if (!isSimulating || routePoints.length === 0) {
      if (simulationTimerRef.current) {
        clearInterval(simulationTimerRef.current)
        simulationTimerRef.current = null
      }
      return
    }

    setGpsStatus('simulated')
    const TICK_MS = 100
    const TOTAL_DURATION_MS = 8000
    const STEP_PROGRESS = TICK_MS / TOTAL_DURATION_MS

    lastSimulationProgressRef.current = simulationProgressRef.current
    updateTelemetry({
      vehicle_status: 'Driving',
      speed_kmph: simulatedSpeed,
    })

    simulationTimerRef.current = setInterval(() => {
      simulationProgressRef.current += STEP_PROGRESS
      const maxIdx = routePoints.length - 1

      if (simulationProgressRef.current >= 1.0) {
        simulationProgressRef.current = 1.0
        const progressDelta = Math.max(0, 1.0 - lastSimulationProgressRef.current)
        lastSimulationProgressRef.current = 1.0
        const incrementalDist = progressDelta * totalDistanceKm

        const destPoint = routePoints[maxIdx]
        const destLat = destPoint[0]
        const destLon = destPoint[1]

        setLivePosition([destLat, destLon])
        setIsSimulating(false)
        if (simulationTimerRef.current) {
          clearInterval(simulationTimerRef.current)
          simulationTimerRef.current = null
        }

        updateTelemetry({
          latitude: destLat,
          longitude: destLon,
          speed_kmph: 0.0,
          vehicle_status: 'Destination Reached',
          incremental_distance_km: incrementalDist,
        })
        return
      }

      const p = simulationProgressRef.current
      const progressDelta = Math.max(0, p - lastSimulationProgressRef.current)
      lastSimulationProgressRef.current = p
      const incrementalDist = progressDelta * totalDistanceKm

      const exactIdx = p * maxIdx
      const i = Math.floor(exactIdx)
      const frac = exactIdx - i

      let interpolatedLat, interpolatedLon
      if (i >= maxIdx) {
        interpolatedLat = routePoints[maxIdx][0]
        interpolatedLon = routePoints[maxIdx][1]
      } else {
        const p1 = routePoints[i]
        const p2 = routePoints[i + 1]
        interpolatedLat = p1[0] + frac * (p2[0] - p1[0])
        interpolatedLon = p1[1] + frac * (p2[1] - p1[1])
      }

      setLivePosition([interpolatedLat, interpolatedLon])
      updateTelemetry({
        latitude: interpolatedLat,
        longitude: interpolatedLon,
        speed_kmph: simulatedSpeed,
        vehicle_status: 'Driving',
        incremental_distance_km: incrementalDist,
      })
    }, TICK_MS)

    return () => {
      if (simulationTimerRef.current) {
        clearInterval(simulationTimerRef.current)
      }
    }
  }, [isSimulating, routePoints, simulatedSpeed, totalDistanceKm, updateTelemetry])

  useEffect(() => {
    return () => {
      updateTelemetry({
        speed_kmph: 0.0,
      })
    }
  }, [updateTelemetry])

  const toggleSimulation = () => {
    if (!isSimulating) {
      if (simulationProgressRef.current >= 1.0) {
        simulationProgressRef.current = 0.0
        lastSimulationProgressRef.current = 0.0
      } else {
        lastSimulationProgressRef.current = simulationProgressRef.current
      }
      setIsSimulating(true)
      setIsFollowing(true)
    } else {
      setIsSimulating(false)
      setGpsStatus('fallback')
      updateTelemetry({
        speed_kmph: 0.0,
        vehicle_status: 'Paused',
      })
    }
  }

  const handleResetSimulation = () => {
    setIsSimulating(false)
    if (simulationTimerRef.current) {
      clearInterval(simulationTimerRef.current)
      simulationTimerRef.current = null
    }
    simulationProgressRef.current = 0.0
    lastSimulationProgressRef.current = 0.0
    setGpsStatus('fallback')
    resetTelemetry()
    if (routePoints && routePoints.length > 0) {
      setLivePosition(routePoints[0])
    }
  }

  const handleRecenter = () => {
    setIsFollowing(true)
  }

  const destCoords = [charger.latitude, charger.longitude]
  const distDisplay = routeData?.distance_km ?? charger.distance_km
  const timeDisplay = routeData?.travel_time_minutes ?? charger.travel_time_minutes

  return (
    <div className="fixed inset-0 z-50 bg-[#06080C] flex flex-col font-sans overflow-hidden">
      {/* Top Floating Seamless HUD */}
      <header className="absolute top-5 left-5 right-5 z-[1000] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pointer-events-none">
        {/* Destination Card & Exit Button */}
        <div className="seamless-glass rounded-2xl p-4 text-white flex items-center gap-4 max-w-xl w-full pointer-events-auto shadow-2xl">
          <button
            onClick={onExit}
            className="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 transition flex items-center justify-center text-sm font-light text-white shrink-0 cursor-pointer"
            title="Exit Navigation"
          >
            ✕
          </button>

          <div className="min-w-0 flex-1">
            <span className="text-[10px] text-amber-400 uppercase tracking-widest font-medium block">
              Navigating To
            </span>
            <h1 className="text-base sm:text-lg font-light text-white tracking-tight truncate">
              {charger.name}
            </h1>
            <p className="text-[11px] text-slate-400 truncate">
              {charger.address || charger.city || 'Mysuru'}
            </p>
          </div>

          <div className="text-right shrink-0">
            <div className="text-xl font-light font-mono tabular-nums text-white leading-none">
              {distDisplay} <span className="text-xs text-slate-400">km</span>
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-1">
              ~{timeDisplay} min
            </div>
          </div>
        </div>

        {/* Status & Controller Controls */}
        <div className="seamless-glass rounded-2xl px-4 py-2 flex items-center gap-3 text-xs text-slate-200 pointer-events-auto shadow-2xl">
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                gpsStatus === 'live'
                  ? 'bg-cockpit-teal animate-pulse'
                  : gpsStatus === 'simulated'
                  ? 'bg-blue-400 animate-pulse'
                  : 'bg-amber-400'
              }`}
            ></span>
            <span className="text-[11px] text-slate-300 font-mono">
              {gpsStatus === 'simulated' ? 'Simulating' : 'Active'}
            </span>
          </div>

          <div className="h-4 w-px bg-white/10"></div>

          <button
            onClick={toggleSimulation}
            className="btn-primary px-3 py-1 text-xs cursor-pointer"
          >
            {isSimulating ? 'Pause' : simulationProgressRef.current >= 1.0 ? 'Replay' : 'Drive'}
          </button>

          <button
            onClick={handleResetSimulation}
            className="text-xs text-slate-400 hover:text-white transition cursor-pointer"
            title="Reset to origin"
          >
            Reset
          </button>
        </div>
      </header>

      {/* Main Environmental Map */}
      <div className="flex-1 w-full h-full relative z-0 bg-[#06080C]">
        <MapContainer
          center={livePosition}
          zoom={15}
          scrollWheelZoom={true}
          className="h-full w-full bg-[#06080C]"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            className="dark-tile-layer"
          />

          <NavigationMapController
            livePosition={livePosition}
            routePoints={routePoints}
            isFollowing={isFollowing}
            onUserDrag={() => setIsFollowing(false)}
          />

          {/* Navigation Route Polyline */}
          {routePoints.length > 0 && (
            <>
              <Polyline
                positions={routePoints}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 8,
                  opacity: 0.25,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
              <Polyline
                positions={routePoints}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 3.5,
                  opacity: 0.95,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </>
          )}

          {/* Moving Live Location Puck */}
          {livePosition && (
            <Marker position={livePosition} icon={createLivePuckIcon()} zIndexOffset={3000}>
              <Popup>
                <div className="text-xs p-1">
                  <strong className="block text-white font-medium">Vehicle Position</strong>
                  <span className="text-slate-400 font-mono tabular-nums">
                    {livePosition[0]?.toFixed(4)}°N, {livePosition[1]?.toFixed(4)}°E
                  </span>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Destination Charger Marker */}
          {destCoords && (
            <Marker position={destCoords} icon={createDestinationIcon(charger.charging_power_kw)} zIndexOffset={2000}>
              <Popup>
                <div className="text-xs p-1 min-w-[140px]">
                  <strong className="block text-white font-medium">{charger.name}</strong>
                  <p className="text-slate-400 text-[11px] mt-0.5">{charger.charging_power_kw} kW · {charger.connector_type}</p>
                </div>
              </Popup>
            </Marker>
          )}
        </MapContainer>
      </div>

      {/* Floating Bottom Control Bar */}
      <footer className="absolute bottom-6 left-5 right-5 z-[1000] flex items-center justify-between pointer-events-none">
        <div className="pointer-events-auto">
          {!isFollowing && (
            <button
              onClick={handleRecenter}
              className="seamless-glass hover:bg-white/10 text-white text-xs px-4 py-2 rounded-full shadow-2xl transition cursor-pointer"
            >
              Recenter Guidance
            </button>
          )}
        </div>

        <div className="pointer-events-auto seamless-glass rounded-full px-4 py-2 flex items-center gap-3 text-xs text-slate-300 shadow-2xl">
          <span className="text-[11px] text-slate-400">OSRM Road Corridor</span>
          <button
            onClick={onExit}
            className="text-xs text-white hover:text-slate-300 font-medium cursor-pointer"
          >
            Exit Navigation
          </button>
        </div>
      </footer>
    </div>
  )
}

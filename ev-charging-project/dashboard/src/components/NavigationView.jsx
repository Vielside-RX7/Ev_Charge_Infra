import React, { useState, useEffect, useRef, useMemo } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet'
import L from 'leaflet'
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// ---------------------------------------------------------------------------
// Custom Navigation DivIcons
// ---------------------------------------------------------------------------
const createLivePuckIcon = (heading = 0) =>
  L.divIcon({
    className: 'custom-live-puck-marker',
    html: `
      <div class="relative flex items-center justify-center">
        <span class="animate-ping absolute inline-flex h-10 w-10 rounded-full bg-blue-400 opacity-60"></span>
        <div class="relative w-8 h-8 rounded-full bg-blue-600 border-3 border-white shadow-2xl flex items-center justify-center text-white ring-4 ring-blue-500/30">
          <div class="w-3 h-3 rounded-full bg-white shadow-sm"></div>
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
        <div class="w-9 h-9 rounded-xl bg-emerald-600 border-2 border-white shadow-xl flex items-center justify-center text-white text-sm font-black ring-4 ring-emerald-500/30">
          ⚡
        </div>
        <div class="bg-slate-900 text-white font-bold text-[10px] px-1.5 py-0.5 rounded shadow mt-1 whitespace-nowrap border border-slate-700">
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
  const [routeData, setRouteData] = useState(null)
  const [routePoints, setRoutePoints] = useState([])
  const [isLoadingRoute, setIsLoadingRoute] = useState(true)
  const [routeError, setRouteError] = useState(null)

  // Live Location State
  const userLat = userLocation?.lat ?? 12.2958
  const userLon = userLocation?.lon ?? 76.6394
  const initialPos = useMemo(() => [userLat, userLon], [userLat, userLon])

  const [livePosition, setLivePosition] = useState(initialPos)
  const [gpsStatus, setGpsStatus] = useState('initializing') // initializing, live, denied, fallback, simulated
  const [gpsAccuracy, setGpsAccuracy] = useState(null)
  const [isFollowing, setIsFollowing] = useState(true)

  // Simulation Mode State (interpolates smoothly along the route)
  const [isSimulating, setIsSimulating] = useState(false)
  const simulationProgressRef = useRef(0.0) // 0.0 to 1.0 along the polyline
  const simulationTimerRef = useRef(null)

  // 1. Fetch Static Route on Mount (Runs strictly ONCE per navigation session)
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

        // Convert GeoJSON LineString coordinates [[lon, lat], ...] to Leaflet [[lat, lon], ...]
        if (res.data?.geometry?.coordinates) {
          const latLngs = res.data.geometry.coordinates.map(([lon, lat]) => [lat, lon])
          setRoutePoints(latLngs)
        } else {
          // Fallback straight line
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

  // 2. Continuous Geolocation Watcher (navigator.geolocation.watchPosition)
  useEffect(() => {
    if (isSimulating) {
      return // Hardware GPS paused while user tests simulation mode
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
          console.warn('Geolocation watch error / permission denied:', err.message)
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

  // 3. Movement Simulation Engine (Smooth Interpolation along Road Geometry)
  // Completes a full route traversal in ~8.0 seconds at 60 Hz / 100ms ticks
  useEffect(() => {
    if (!isSimulating || routePoints.length === 0) {
      if (simulationTimerRef.current) {
        clearInterval(simulationTimerRef.current)
        simulationTimerRef.current = null
      }
      return
    }

    setGpsStatus('simulated')
    const TICK_MS = 100 // 10 ticks per second for smooth visual animation
    const TOTAL_DURATION_MS = 8000 // 8.0 seconds total trip preview
    const STEP_PROGRESS = TICK_MS / TOTAL_DURATION_MS // ~0.0125 per tick

    simulationTimerRef.current = setInterval(() => {
      simulationProgressRef.current += STEP_PROGRESS
      if (simulationProgressRef.current >= 1.0) {
        simulationProgressRef.current = 0.0 // Loop smoothly back to start
      }

      const p = simulationProgressRef.current
      const maxIdx = routePoints.length - 1
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
    }, TICK_MS)

    return () => {
      if (simulationTimerRef.current) {
        clearInterval(simulationTimerRef.current)
      }
    }
  }, [isSimulating, routePoints])

  const toggleSimulation = () => {
    if (!isSimulating) {
      setIsSimulating(true)
      setIsFollowing(true)
    } else {
      setIsSimulating(false)
      // When pausing simulation, restore hardware GPS status
      setGpsStatus('initializing')
    }
  }

  const handleRecenter = () => {
    setIsFollowing(true)
  }

  const destCoords = [charger.latitude, charger.longitude]
  const distDisplay = routeData?.distance_km ?? charger.distance_km
  const timeDisplay = routeData?.travel_time_minutes ?? charger.travel_time_minutes

  return (
    <div className="fixed inset-0 z-50 bg-slate-900 flex flex-col font-sans overflow-hidden">
      {/* Top Floating Navigation HUD */}
      <header className="absolute top-4 left-4 right-4 z-[1000] flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pointer-events-none">
        {/* Destination Card & Back Button */}
        <div className="bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl shadow-2xl p-4 text-white flex items-center gap-4 max-w-xl w-full pointer-events-auto">
          <button
            onClick={onExit}
            className="w-10 h-10 rounded-xl bg-slate-800 hover:bg-slate-700 active:scale-95 transition-all flex items-center justify-center text-lg font-bold text-slate-200 border border-slate-600 shrink-0"
            title="Exit Navigation"
          >
            ✕
          </button>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="bg-emerald-500/20 text-emerald-300 text-[10px] font-bold px-2 py-0.5 rounded border border-emerald-500/40 uppercase">
                Navigating
              </span>
              <span className="text-xs text-slate-400 truncate">
                {charger.operator ? `${charger.operator} • ` : ''}{charger.city || 'Mysuru'}
              </span>
            </div>
            <h1 className="text-base sm:text-lg font-black text-white truncate mt-0.5">
              {charger.name}
            </h1>
            <p className="text-xs text-slate-400 truncate">{charger.address || 'Karnataka, India'}</p>
          </div>

          <div className="text-right shrink-0 bg-slate-800/80 px-3 py-2 rounded-xl border border-slate-700">
            <div className="text-lg font-black text-emerald-400 leading-none">
              {distDisplay} <span className="text-xs font-semibold text-slate-300">km</span>
            </div>
            <div className="text-[11px] font-medium text-slate-400 mt-1">
              ~{timeDisplay} min drive
            </div>
          </div>
        </div>

        {/* GPS Status & Simulation Controller Pill */}
        <div className="bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl shadow-2xl px-4 py-2.5 flex items-center gap-3 text-xs text-slate-200 pointer-events-auto">
          <div className="flex items-center gap-2">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                gpsStatus === 'live'
                  ? 'bg-emerald-400 animate-pulse'
                  : gpsStatus === 'simulated'
                  ? 'bg-indigo-400 animate-pulse'
                  : gpsStatus === 'denied'
                  ? 'bg-amber-400'
                  : 'bg-blue-400'
              }`}
            ></span>
            <span className="font-semibold capitalize text-slate-300">
              {gpsStatus === 'live'
                ? `GPS Active (±${gpsAccuracy}m)`
                : gpsStatus === 'simulated'
                ? 'Simulation Mode'
                : gpsStatus === 'denied'
                ? 'GPS Denied (Fixed Point)'
                : 'Tracking Initial Pos'}
            </span>
          </div>

          <div className="h-4 w-px bg-slate-700"></div>

          <button
            onClick={toggleSimulation}
            className={`px-3 py-1 rounded-lg font-bold text-xs transition-all ${
              isSimulating
                ? 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/40'
                : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-600'
            }`}
          >
            {isSimulating ? '⏸ Pause Demo' : '▶ Simulate Drive'}
          </button>
        </div>
      </header>

      {/* Permission Warning Banner if GPS Denied */}
      {gpsStatus === 'denied' && (
        <div className="absolute top-24 left-4 right-4 z-[999] max-w-md mx-auto bg-amber-900/90 border border-amber-600 text-amber-100 text-xs px-4 py-2.5 rounded-xl shadow-xl flex items-center justify-between gap-3">
          <span>
            📍 <strong>Location access was denied.</strong> Navigation is using your initial search position. Use <strong>Simulate Drive</strong> to test live motion.
          </span>
        </div>
      )}

      {/* Main Full-Screen Map Container */}
      <div className="flex-1 w-full h-full relative z-0">
        <MapContainer
          center={livePosition}
          zoom={15}
          scrollWheelZoom={true}
          className="h-full w-full"
        >
          {/* Tile layer */}
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <NavigationMapController
            livePosition={livePosition}
            routePoints={routePoints}
            isFollowing={isFollowing}
            onUserDrag={() => setIsFollowing(false)}
          />

          {/* Static Route Polyline (Glow casing + solid blue core) */}
          {routePoints.length > 0 && (
            <>
              {/* Dark Outline / Casing */}
              <Polyline
                positions={routePoints}
                pathOptions={{
                  color: '#1e3a8a',
                  weight: 8,
                  opacity: 0.6,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
              {/* Vibrant Navigation Route Core */}
              <Polyline
                positions={routePoints}
                pathOptions={{
                  color: '#3b82f6',
                  weight: 5,
                  opacity: 0.95,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </>
          )}

          {/* Moving Live Location Puck Marker */}
          {livePosition && (
            <Marker position={livePosition} icon={createLivePuckIcon()} zIndexOffset={3000}>
              <Popup>
                <div className="text-xs p-1">
                  <strong className="block text-blue-700 font-bold">📍 Your Live Position</strong>
                  <span className="text-gray-500">
                    {livePosition[0]?.toFixed(5)}, {livePosition[1]?.toFixed(5)}
                  </span>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Destination Charger Marker */}
          {destCoords && (
            <Marker position={destCoords} icon={createDestinationIcon(charger.charging_power_kw)} zIndexOffset={2000}>
              <Popup>
                <div className="text-xs p-1">
                  <strong className="block text-emerald-800 font-bold text-sm">{charger.name}</strong>
                  <p className="text-gray-600 mt-0.5">{charger.address || charger.city}</p>
                  <div className="mt-1 pt-1 border-t border-gray-100 flex items-center justify-between">
                    <span className="font-semibold text-emerald-700">{charger.charging_power_kw} kW</span>
                    <span className="text-gray-500">🔌 {charger.connector_type}</span>
                  </div>
                </div>
              </Popup>
            </Marker>
          )}
        </MapContainer>
      </div>

      {/* Floating Bottom Control Bar */}
      <footer className="absolute bottom-6 left-4 right-4 z-[1000] flex items-center justify-between pointer-events-none">
        <div className="pointer-events-auto flex items-center gap-2">
          {!isFollowing && (
            <button
              onClick={handleRecenter}
              className="bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs px-4 py-2.5 rounded-xl shadow-2xl transition-all active:scale-95 flex items-center gap-2 border border-blue-400 ring-4 ring-blue-500/20"
            >
              <span>🎯</span> Recenter Map
            </button>
          )}
        </div>

        <div className="pointer-events-auto bg-slate-900/95 backdrop-blur-md border border-slate-700/80 rounded-2xl shadow-2xl p-3 flex items-center gap-3 text-xs text-slate-300">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            <span className="font-medium text-slate-200">
              {routeData?.is_fallback ? 'Direct Road Route' : 'OSRM Live Routing'}
            </span>
          </div>

          <button
            onClick={onExit}
            className="bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold px-3 py-1.5 rounded-xl border border-slate-600 active:scale-95 transition-all"
          >
            Exit Navigation
          </button>
        </div>
      </footer>
    </div>
  )
}

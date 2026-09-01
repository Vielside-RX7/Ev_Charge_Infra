import React, { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'

// ---------------------------------------------------------------------------
// Custom Leaflet DivIcons using Tailwind CSS
// ---------------------------------------------------------------------------
const createUserIcon = () =>
  L.divIcon({
    className: 'custom-user-marker',
    html: `
      <div class="relative flex items-center justify-center">
        <span class="animate-ping absolute inline-flex h-8 w-8 rounded-full bg-indigo-400 opacity-75"></span>
        <div class="relative w-8 h-8 rounded-full bg-indigo-600 border-2 border-white shadow-lg flex items-center justify-center text-white text-xs font-bold">
          📍
        </div>
      </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -18],
  })

const createChargerIcon = (rank, isCompatible, isSelected) => {
  let bgClass = 'bg-blue-600 text-white border-white'
  let ringClass = isSelected ? 'ring-4 ring-blue-400 scale-125 z-50' : 'ring-2 ring-blue-300'

  if (rank === 1) {
    bgClass = 'bg-amber-500 text-white border-white'
    ringClass = isSelected ? 'ring-4 ring-yellow-400 scale-125 z-50' : 'ring-2 ring-amber-300 shadow-lg'
  } else if (!isCompatible) {
    bgClass = 'bg-amber-600 text-white border-amber-200'
    ringClass = isSelected ? 'ring-4 ring-amber-400 scale-125 z-50' : 'ring-2 ring-dashed ring-amber-400'
  }

  return L.divIcon({
    className: 'custom-charger-marker',
    html: `
      <div class="transition-transform duration-200 ${ringClass} w-8 h-8 rounded-full ${bgClass} border-2 shadow-md flex items-center justify-center text-xs font-black">
        ${rank}
      </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -18],
  })
}

// ---------------------------------------------------------------------------
// Map Controller: Bounds Auto-fitting & Pan on Selection
// ---------------------------------------------------------------------------
function MapController({ results, userLocation, selectedChargerId, markerRefs }) {
  const map = useMap()

  // Auto-fit bounds when results or user location change
  useEffect(() => {
    if (!results || results.length === 0) return

    const points = []
    if (userLocation?.lat && userLocation?.lon) {
      points.push([userLocation.lat, userLocation.lon])
    }

    results.forEach((c) => {
      if (c.latitude && c.longitude) {
        points.push([c.latitude, c.longitude])
      }
    })

    if (points.length > 0) {
      const bounds = L.latLngBounds(points)
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 })
    }
  }, [results, userLocation, map])

  // Pan and open popup when a charger is selected from ResultsList
  useEffect(() => {
    if (!selectedChargerId || !results) return

    const selected = results.find((c) => c.charger_id === selectedChargerId)
    if (selected && selected.latitude && selected.longitude) {
      map.flyTo([selected.latitude, selected.longitude], 15, { animate: true, duration: 0.6 })
      const marker = markerRefs.current[selectedChargerId]
      if (marker) {
        marker.openPopup()
      }
    }
  }, [selectedChargerId, results, map, markerRefs])

  return null
}

// ---------------------------------------------------------------------------
// MapView Component
// ---------------------------------------------------------------------------
export default function MapView({
  results,
  userLocation,
  selectedChargerId,
  onSelectCharger,
}) {
  const markerRefs = useRef({})
  const defaultCenter = [12.2958, 76.6394] // Mysore Center
  const center = userLocation?.lat && userLocation?.lon
    ? [userLocation.lat, userLocation.lon]
    : defaultCenter

  return (
    <div className="bg-white rounded-xl shadow-md border border-gray-200 overflow-hidden mb-6">
      <div className="h-[380px] w-full relative">
        <MapContainer
          center={center}
          zoom={12}
          scrollWheelZoom={false}
          className="h-full w-full z-0"
        >
          {/* OpenStreetMap standard public tile layer with attribution */}
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <MapController
            results={results}
            userLocation={userLocation}
            selectedChargerId={selectedChargerId}
            markerRefs={markerRefs}
          />

          {/* User Location Marker */}
          {userLocation?.lat && userLocation?.lon && (
            <Marker
              position={[userLocation.lat, userLocation.lon]}
              icon={createUserIcon()}
              zIndexOffset={1500}
            >
              <Popup>
                <div className="text-xs p-1">
                  <strong className="block text-indigo-700 text-sm font-bold">📍 Your Location</strong>
                  <span className="text-gray-500">Lat: {userLocation.lat}, Lon: {userLocation.lon}</span>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Ranked Charger Markers */}
          {results &&
            results.map((charger, index) => {
              const rank = index + 1
              const isSelected = selectedChargerId === charger.charger_id
              const zIndexOffset = isSelected ? 2000 : rank === 1 ? 1000 : 500 - rank

              return (
                <Marker
                  key={charger.charger_id}
                  position={[charger.latitude, charger.longitude]}
                  icon={createChargerIcon(rank, charger.compatible, isSelected)}
                  zIndexOffset={zIndexOffset}
                  ref={(ref) => {
                    if (ref) markerRefs.current[charger.charger_id] = ref
                  }}
                  eventHandlers={{
                    click: () => {
                      if (onSelectCharger) {
                        onSelectCharger(charger.charger_id)
                      }
                    },
                  }}
                >
                  <Popup>
                    <div className="text-xs p-1 max-w-[200px]">
                      <div className="flex items-center gap-1 mb-1">
                        <span className={`font-bold px-1.5 py-0.5 rounded text-[10px] text-white ${
                          rank === 1 ? 'bg-amber-500' : 'bg-blue-600'
                        }`}>
                          #{rank}
                        </span>
                        <strong className="text-gray-900 text-xs truncate">{charger.name}</strong>
                      </div>

                      <div className="space-y-0.5 text-gray-600 text-[11px] mb-1.5">
                        <p>Distance: <strong>{charger.distance_km} km</strong></p>
                        <p>Power: <strong>{charger.charging_power_kw} kW</strong></p>
                        <p>Score: <strong>{charger.final_score.toFixed(4)}</strong></p>
                      </div>

                      {!charger.compatible && (
                        <div className="bg-amber-50 border border-amber-200 text-amber-800 text-[10px] p-1 rounded font-medium">
                          ⚠️ May not match your connector standard (Fallback)
                        </div>
                      )}
                    </div>
                  </Popup>
                </Marker>
              )
            })}
        </MapContainer>
      </div>
    </div>
  )
}

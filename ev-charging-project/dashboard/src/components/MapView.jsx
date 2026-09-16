import React, { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap, useMapEvents, Polyline } from 'react-leaflet'
import L from 'leaflet'
import { useTrip } from '../trip/TripContext'
import { useTelemetry } from '../telemetry/TelemetryContext'

// ---------------------------------------------------------------------------
// Refined Automotive Leaflet DivIcons
// ---------------------------------------------------------------------------
const createUserIcon = () =>
  L.divIcon({
    className: 'custom-user-marker',
    html: `
      <div class="relative flex items-center justify-center">
        <span class="animate-ping absolute inline-flex h-7 w-7 rounded-full bg-cockpit-teal opacity-40"></span>
        <div class="relative w-5 h-5 rounded-full bg-[#05070A] border-2 border-cockpit-teal flex items-center justify-center shadow-lg">
          <div class="w-2 h-2 rounded-full bg-cockpit-teal"></div>
        </div>
      </div>
    `,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -16],
  })

const createDestinationIcon = () =>
  L.divIcon({
    className: 'custom-destination-marker',
    html: `
      <div class="relative flex items-center justify-center">
        <div class="relative w-6 h-6 rounded-full bg-[#05070A] border-2 border-white flex items-center justify-center text-white text-[10px] font-medium shadow-xl">
          ⚑
        </div>
      </div>
    `,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    popupAnchor: [0, -14],
  })

const createWaypointChargerIcon = (powerKw) =>
  L.divIcon({
    className: 'custom-waypoint-charger-marker',
    html: `
      <div class="relative flex flex-col items-center">
        <span class="animate-ping absolute -top-0.5 inline-flex h-8 w-8 rounded-full bg-amber-400 opacity-40"></span>
        <div class="relative w-7 h-7 rounded-full bg-[#14120C] border-2 border-amber-400 text-amber-400 flex items-center justify-center text-xs font-bold shadow-2xl z-50">
          ⚡
        </div>
        <div class="bg-[#080A0E]/90 text-amber-300 font-mono text-[9px] font-medium px-2 py-0.5 rounded-full shadow mt-1 whitespace-nowrap border border-amber-500/30 backdrop-blur-md">
          ${powerKw ? `${powerKw} kW` : 'Charge'}
        </div>
      </div>
    `,
    iconSize: [34, 48],
    iconAnchor: [17, 24],
    popupAnchor: [0, -26],
  })

const createChargerIcon = (rank, isCompatible, isSelected) => {
  let borderClass = 'border-white/30 text-slate-300'
  let bgClass = 'bg-[#080B12]'
  let ringClass = isSelected ? 'ring-2 ring-cockpit-teal scale-110 z-50' : ''

  if (rank === 1) {
    borderClass = 'border-amber-400 text-amber-300'
    bgClass = 'bg-[#14120C]'
    ringClass = isSelected ? 'ring-2 ring-amber-400 scale-110 z-50' : 'ring-1 ring-amber-400/20'
  } else if (!isCompatible) {
    borderClass = 'border-slate-700 text-slate-500'
  }

  return L.divIcon({
    className: 'custom-charger-marker',
    html: `
      <div class="transition-transform duration-200 ${ringClass} w-6 h-6 rounded-full ${bgClass} border ${borderClass} flex items-center justify-center text-[10px] font-mono font-medium shadow-md">
        ${rank}
      </div>
    `,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -14],
  })
}

// ---------------------------------------------------------------------------
// Map Click Handler for Destination Selection
// ---------------------------------------------------------------------------
function MapClickHandler({ isSelectingDestination, onSetDestination }) {
  useMapEvents({
    click(e) {
      if (isSelectingDestination && onSetDestination) {
        onSetDestination({ latitude: e.latlng.lat, longitude: e.latlng.lng })
      }
    },
  })
  return null
}

// ---------------------------------------------------------------------------
// Map Controller: Bounds Auto-fitting & Pan on Selection
// ---------------------------------------------------------------------------
function MapController({
  results,
  userLocation,
  selectedChargerId,
  markerRefs,
  journeyPlan,
  tripRoutePoints,
}) {
  const map = useMap()

  useEffect(() => {
    // 1. If journey plan with charging stop exists, fit all waypoints
    if (journeyPlan && journeyPlan.fullPolylinePoints && journeyPlan.fullPolylinePoints.length > 0) {
      const bounds = L.latLngBounds(journeyPlan.fullPolylinePoints)
      map.fitBounds(bounds, { padding: [70, 70], maxZoom: 14 })
      return
    }

    // 2. Otherwise if charger search results exist, fit charger results
    if (results && results.length > 0) {
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
        map.fitBounds(bounds, { padding: [70, 70], maxZoom: 14 })
      }
      return
    }

    // 3. Otherwise if direct trip route exists, fit bounds to the road route
    if (tripRoutePoints && tripRoutePoints.length > 0) {
      const bounds = L.latLngBounds(tripRoutePoints)
      map.fitBounds(bounds, { padding: [70, 70], maxZoom: 14 })
    }
  }, [journeyPlan, results, userLocation, tripRoutePoints, map])

  // Pan and open popup when a charger is selected
  useEffect(() => {
    if (!selectedChargerId || !results) return

    const selected = results.find((c) => c.charger_id === selectedChargerId)
    if (selected && selected.latitude && selected.longitude) {
      map.flyTo([selected.latitude, selected.longitude], 15, { animate: true, duration: 0.5 })
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
  onStartNavigation,
}) {
  const {
    trip,
    isSelectingDestination,
    setDestination,
    clearDestination,
    journeyPlan,
    navState,
    currentLeg,
    NAV_STATE,
  } = useTrip()

  const { telemetry } = useTelemetry()
  const markerRefs = useRef({})

  const effectiveUserLocation = {
    lat: Number(telemetry.latitude) || 12.2958,
    lon: Number(telemetry.longitude) || 76.6394,
  }

  const center = [effectiveUserLocation.lat, effectiveUserLocation.lon]

  const isChargeJourney = journeyPlan?.decisionType === 'CHARGE'
  const isDirectJourney = journeyPlan?.decisionType === 'DIRECT'
  const leg1Points = isChargeJourney ? journeyPlan.legs?.originToCharger?.polylinePoints : null
  const leg2Points = isChargeJourney ? journeyPlan.legs?.chargerToDestination?.polylinePoints : null
  const directPlanPoints = isDirectJourney ? journeyPlan.legs?.direct?.polylinePoints : null
  const fallbackPoints = trip?.route?.polylinePoints

  const isNavigating =
    navState === NAV_STATE.DRIVING ||
    navState === NAV_STATE.DRIVING_LEG_1 ||
    navState === NAV_STATE.DRIVING_LEG_2 ||
    navState === NAV_STATE.CHARGING ||
    navState === NAV_STATE.PAUSED

  return (
    <div className="relative w-full h-[520px] lg:h-[calc(100vh-140px)] min-h-[520px] rounded-3xl overflow-hidden border border-white/5 shadow-2xl bg-[#05070A]">
      {/* Floating Status Bar over Map */}
      <div className="absolute top-4 left-4 right-4 z-[1000] flex items-center justify-between pointer-events-none">
        <div className="seamless-glass px-4 py-2 rounded-full text-xs flex items-center gap-2.5 pointer-events-auto shadow-lg">
          <span className={`w-2 h-2 rounded-full ${isNavigating ? 'bg-cockpit-teal animate-pulse' : 'bg-cockpit-teal'}`}></span>
          <span className="text-slate-200 font-medium text-[11px] tracking-tight">
            {navState === NAV_STATE.CHARGING
              ? `Fast Charging: ${journeyPlan?.selectedCharger?.name || 'Station'}`
              : navState === NAV_STATE.DRIVING_LEG_1
              ? 'En Route · Leg 1 to Charger'
              : navState === NAV_STATE.DRIVING_LEG_2
              ? 'En Route · Leg 2 to Destination'
              : navState === NAV_STATE.DRIVING
              ? 'En Route · Direct Corridor'
              : isChargeJourney
              ? 'Multi-Leg Charging Corridor'
              : isDirectJourney
              ? 'Direct Journey Corridor'
              : 'Mysuru Environmental Canvas'}
          </span>
        </div>

        {isNavigating && (
          <div className="seamless-glass px-4 py-2 rounded-full text-xs font-mono tabular-nums text-white flex items-center gap-3 pointer-events-auto shadow-lg">
            <span>{telemetry.speed_kmph.toFixed(0)} km/h</span>
            <span className="text-slate-600">·</span>
            <span className="text-cockpit-teal">{telemetry.current_soc_percent.toFixed(1)}% SoC</span>
          </div>
        )}

        {isSelectingDestination && (
          <div className="seamless-glass px-5 py-2 rounded-full text-xs text-amber-300 border border-amber-400/20 flex items-center gap-2 pointer-events-auto animate-pulse shadow-lg">
            <span className="w-2 h-2 rounded-full bg-amber-400"></span>
            <span>Tap map to set destination waypoint</span>
          </div>
        )}
      </div>

      <div className={`h-full w-full relative ${isSelectingDestination ? 'cursor-crosshair' : ''}`}>
        <MapContainer
          center={center}
          zoom={12}
          scrollWheelZoom={false}
          className="h-full w-full z-0 bg-[#05070A]"
        >
          {/* CartoDB Dark Matter Automotive Tiles */}
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          />

          <MapClickHandler
            isSelectingDestination={isSelectingDestination}
            onSetDestination={setDestination}
          />

          <MapController
            results={results}
            userLocation={effectiveUserLocation}
            selectedChargerId={selectedChargerId}
            markerRefs={markerRefs}
            journeyPlan={journeyPlan}
            tripRoutePoints={fallbackPoints}
          />

          {/* Render Route Polylines: CHARGE Journey (Leg 1 and Leg 2) */}
          {isChargeJourney && leg1Points && leg1Points.length > 0 && (
            <>
              <Polyline
                positions={leg1Points}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 6,
                  opacity: 0.2,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
              <Polyline
                positions={leg1Points}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 3,
                  opacity: 0.95,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </>
          )}

          {isChargeJourney && leg2Points && leg2Points.length > 0 && (
            <>
              <Polyline
                positions={leg2Points}
                pathOptions={{
                  color: '#38BDF8',
                  weight: 6,
                  opacity: 0.2,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
              <Polyline
                positions={leg2Points}
                pathOptions={{
                  color: '#38BDF8',
                  weight: 3,
                  opacity: 0.95,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </>
          )}

          {/* Render Route Polyline: DIRECT Journey */}
          {((isDirectJourney && directPlanPoints) || (!journeyPlan && fallbackPoints)) && (
            <>
              <Polyline
                positions={directPlanPoints || fallbackPoints}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 6,
                  opacity: 0.2,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
              <Polyline
                positions={directPlanPoints || fallbackPoints}
                pathOptions={{
                  color: '#00D2B4',
                  weight: 3,
                  opacity: 0.95,
                  lineCap: 'round',
                  lineJoin: 'round',
                }}
              />
            </>
          )}

          {/* Vehicle Location Marker */}
          {effectiveUserLocation?.lat && effectiveUserLocation?.lon && (
            <Marker
              position={[effectiveUserLocation.lat, effectiveUserLocation.lon]}
              icon={createUserIcon()}
              zIndexOffset={1500}
            >
              <Popup>
                <div className="text-xs p-2 min-w-[140px]">
                  <strong className="block text-white text-xs font-medium mb-0.5">
                    Vehicle Position
                  </strong>
                  <div className="font-mono tabular-nums text-slate-400 text-[11px]">
                    {effectiveUserLocation.lat.toFixed(4)}°N, {effectiveUserLocation.lon.toFixed(4)}°E
                  </div>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Trip Destination Marker */}
          {trip.destination_selected && trip.destination_latitude && trip.destination_longitude && (
            <Marker
              position={[trip.destination_latitude, trip.destination_longitude]}
              icon={createDestinationIcon()}
              zIndexOffset={1900}
            >
              <Popup>
                <div className="text-xs p-2 min-w-[150px]">
                  <strong className="block text-white text-xs font-medium mb-1">
                    Destination Waypoint
                  </strong>
                  <div className="font-mono tabular-nums text-slate-400 text-[11px]">
                    {trip.destination_latitude.toFixed(4)}°N, {trip.destination_longitude.toFixed(4)}°E
                  </div>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      clearDestination()
                    }}
                    className="mt-2 text-rose-400 hover:text-rose-300 text-[11px] font-medium transition cursor-pointer"
                  >
                    Remove Waypoint
                  </button>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Selected Charger as Waypoint from /trip/plan */}
          {isChargeJourney && journeyPlan.selectedCharger && (
            <Marker
              position={[
                journeyPlan.selectedCharger.latitude,
                journeyPlan.selectedCharger.longitude,
              ]}
              icon={createWaypointChargerIcon(journeyPlan.selectedCharger.powerKw)}
              zIndexOffset={2500}
            >
              <Popup>
                <div className="text-xs p-2 max-w-[230px] space-y-1.5">
                  <div>
                    <span className="label-subhead text-amber-400 block mb-0.5">
                      Waypoint Charge Stop
                    </span>
                    <strong className="block text-white text-sm font-light tracking-tight">
                      {journeyPlan.selectedCharger.name}
                    </strong>
                  </div>

                  <div className="text-slate-300 text-[11px] font-mono tabular-nums space-y-0.5 pt-1 border-t border-white/10">
                    <div>Power: <strong className="text-amber-300">{journeyPlan.selectedCharger.powerKw} kW</strong></div>
                    <div>Wait: <strong>{journeyPlan.routeMetrics.chargingWaitMinutes} min</strong></div>
                    <div>Charge Duration: <strong>{journeyPlan.routeMetrics.chargingDurationMinutes} min</strong></div>
                    <div>Energy Added: <strong className="text-emerald-400">+{journeyPlan.energyAccounting.energyAddedKwh.toFixed(1)} kWh</strong></div>
                  </div>
                </div>
              </Popup>
            </Marker>
          )}

          {/* Ranked Charger Markers from general /recommend */}
          {results &&
            results.map((charger, index) => {
              if (
                isChargeJourney &&
                journeyPlan.selectedCharger &&
                charger.charger_id === journeyPlan.selectedCharger.id
              ) {
                return null
              }

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
                    <div className="text-xs p-2 max-w-[210px] space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className={`font-mono text-[10px] px-1.5 py-0.2 rounded ${
                          rank === 1 ? 'bg-amber-400/20 text-amber-300' : 'bg-white/10 text-slate-300'
                        }`}>
                          #{rank}
                        </span>
                        <strong className="text-white text-xs truncate">{charger.name}</strong>
                      </div>

                      <div className="text-slate-400 text-[11px] font-mono tabular-nums space-y-0.5">
                        <div>{charger.distance_km} km away</div>
                        <div>{charger.charging_power_kw} kW power</div>
                      </div>

                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          if (onStartNavigation) onStartNavigation(charger)
                        }}
                        className="btn-primary w-full py-1.5 text-[11px] mt-2 flex items-center justify-center gap-1 cursor-pointer"
                      >
                        <span>Navigate Here</span>
                      </button>
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

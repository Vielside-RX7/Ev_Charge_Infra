import React, { useState, useEffect } from 'react'
import { useTrip } from '../trip/TripContext'
import { useTelemetry } from '../telemetry/TelemetryContext'
import { tripRecorder } from '../trip/tripRecorder'
import { fetchTripEnergyPrediction, fetchNearTermEnergyPrediction } from '../trip/energyPredictionService'

export default function ActiveTripCard() {
  const {
    trip,
    isSelectingDestination,
    startSelectingDestination,
    cancelSelectingDestination,
    clearDestination,
    journeyPlan,
    isPlanning,
    planError,
    planJourneyAction,
    navState,
    currentLeg,
    legProgressPercent,
    overallTripProgressPercent,
    simulatedChargingProgress,
    chargingEnergyAddedSoFar,
    tripSummary,
    simulationState,
    simulationProgress,
    startSimulation,
    pauseSimulation,
    resumeSimulation,
    resetSimulation,
    NAV_STATE,
    LEG_TYPE,
  } = useTrip()

  const { telemetry } = useTelemetry()

  // AI Energy Prediction state (driver advisory layer)
  const [aiTripPrediction, setAiTripPrediction] = useState(null)
  const [aiNearTermPrediction, setAiNearTermPrediction] = useState(null)
  const [showAiDetails, setShowAiDetails] = useState(false)

  // Fetch whole-trip XGBoost energy prediction when valid route exists
  useEffect(() => {
    let isMounted = true
    const distanceKm = journeyPlan?.routeMetrics?.totalDistanceKm || trip.route?.distance_km
    const durationMinutes = journeyPlan?.routeMetrics?.travelTimeMinutes || trip.route?.travel_time_minutes

    if (trip.destination_selected && distanceKm > 0) {
      fetchTripEnergyPrediction({
        distanceKm,
        durationMinutes,
        batteryCapacityKwh: telemetry.battery_capacity_kwh,
        startingSocPercent: telemetry.current_soc_percent,
      })
        .then((res) => {
          if (isMounted) setAiTripPrediction(res)
        })
        .catch((err) => {
          if (isMounted) {
            setAiTripPrediction({
              status: 'unavailable',
              model: 'XGBoost',
              message: err?.message || 'AI advisory offline',
            })
          }
        })
    } else {
      setAiTripPrediction(null)
    }
    return () => {
      isMounted = false
    }
  }, [
    trip.destination_selected,
    journeyPlan?.routeMetrics?.totalDistanceKm,
    journeyPlan?.routeMetrics?.travelTimeMinutes,
    trip.route?.distance_km,
    trip.route?.travel_time_minutes,
    telemetry.battery_capacity_kwh,
    telemetry.current_soc_percent,
  ])

  // Track telemetry history and evaluate near-term LSTM prediction during simulation
  useEffect(() => {
    let isMounted = true
    const samples = tripRecorder.currentTrip?.samples || []

    if (samples.length < 20) {
      setAiNearTermPrediction({
        status: 'insufficient_history',
        model: 'LSTM',
        message: `Analyzing dynamics (${samples.length}/20)`,
      })
    } else {
      fetchNearTermEnergyPrediction(samples)
        .then((res) => {
          if (isMounted) setAiNearTermPrediction(res)
        })
        .catch((err) => {
          if (isMounted) {
            setAiNearTermPrediction({
              status: 'unavailable',
              model: 'LSTM',
              message: err?.message || 'AI advisory offline',
            })
          }
        })
    }

    return () => {
      isMounted = false
    }
  }, [simulationState, simulationProgress])

  const destLatFormatted = typeof trip.destination_latitude === 'number'
    ? `${trip.destination_latitude.toFixed(3)}°N`
    : null
  const destLonFormatted = typeof trip.destination_longitude === 'number'
    ? `${trip.destination_longitude.toFixed(3)}°E`
    : null

  const isDrivingActive =
    navState === NAV_STATE.DRIVING ||
    navState === NAV_STATE.DRIVING_LEG_1 ||
    navState === NAV_STATE.DRIVING_LEG_2 ||
    navState === NAV_STATE.CHARGING ||
    navState === NAV_STATE.PAUSED

  // -------------------------------------------------------------------------
  // STATE 1: IDLE / NO DESTINATION SELECTED (Spacious Landing Experience)
  // -------------------------------------------------------------------------
  if (!trip.destination_selected) {
    return (
      <section className="py-8 space-y-4">
        <div className="space-y-1.5">
          <span className="label-subhead block">Journey Planning</span>
          <h2 className="display-metric text-white font-light">
            Where would you like to go?
          </h2>
          <p className="label-quiet text-slate-400 max-w-md">
            Select any destination on the regional road network to evaluate physical reachability, energy expenditure, and required charging stops.
          </p>
        </div>

        <div className="pt-2">
          {isSelectingDestination ? (
            <div className="flex items-center gap-4">
              <div className="px-5 py-3 rounded-full bg-amber-400/10 border border-amber-400/20 text-amber-300 text-xs font-medium flex items-center gap-2.5 animate-pulse">
                <span className="w-2 h-2 rounded-full bg-amber-400"></span>
                <span>Click anywhere on the map to set destination waypoint</span>
              </div>
              <button
                type="button"
                onClick={cancelSelectingDestination}
                className="btn-secondary px-5 py-2.5 text-xs cursor-pointer"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={startSelectingDestination}
              className="btn-primary px-7 py-3 text-xs flex items-center gap-2 cursor-pointer shadow-lg shadow-teal-500/10"
            >
              <span>Choose Destination on Map</span>
              <span className="text-sm font-normal">→</span>
            </button>
          )}
        </div>
      </section>
    )
  }

  // -------------------------------------------------------------------------
  // STATE 2: ACTIVE CHARGING SIMULATION STATE
  // -------------------------------------------------------------------------
  if (navState === NAV_STATE.CHARGING) {
    const chargerPower = journeyPlan?.selectedCharger?.powerKw || 50
    const plannedEnergy = journeyPlan?.energyAccounting?.energyAddedKwh || 0
    const durationMin = journeyPlan?.routeMetrics?.chargingDurationMinutes || 20

    return (
      <section className="py-6 space-y-6">
        <div className="flex items-center justify-between">
          <span className="label-subhead text-amber-400 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-ping"></span>
            Simulated Fast Charging
          </span>
          <button
            type="button"
            onClick={clearDestination}
            className="text-slate-400 hover:text-rose-400 transition text-[11px] cursor-pointer"
          >
            Cancel Trip
          </button>
        </div>

        <div className="space-y-2">
          <h2 className="display-metric text-white font-light">
            {journeyPlan?.selectedCharger?.name || 'Fast Charger'}
          </h2>
          <p className="label-quiet text-amber-300/90 font-mono">
            {chargerPower} kW DC Fast Charge · Planned +{plannedEnergy.toFixed(1)} kWh transfer
          </p>
        </div>

        {/* Large Typography Charging Readout */}
        <div className="grid grid-cols-2 gap-8 items-baseline py-4 border-y border-white/5">
          <div>
            <span className="label-subhead block mb-1">Energy Transferred</span>
            <div className="flex items-baseline gap-1.5">
              <span className="display-huge tabular-nums text-emerald-400 font-light">
                +{chargingEnergyAddedSoFar.toFixed(1)}
              </span>
              <span className="text-xl font-light text-slate-400">kWh</span>
            </div>
            <p className="label-quiet mt-1">
              Target: +{plannedEnergy.toFixed(1)} kWh
            </p>
          </div>

          <div>
            <span className="label-subhead block mb-1">Vehicle Battery</span>
            <div className="flex items-baseline gap-1.5">
              <span className="display-huge tabular-nums text-white font-light">
                {telemetry.current_soc_percent.toFixed(0)}
              </span>
              <span className="text-xl font-light text-slate-400">%</span>
            </div>
            <p className="label-quiet mt-1 font-mono">
              {telemetry.remaining_energy_kwh.toFixed(1)} kWh available
            </p>
          </div>
        </div>

        {/* Seamless Charging Progress Bar */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs label-quiet font-mono">
            <span>Charging Progress ({durationMin} min simulation)</span>
            <span className="text-amber-300 font-semibold">{simulatedChargingProgress}%</span>
          </div>
          <div className="w-full bg-white/5 rounded-full h-[4px] overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-amber-400 to-emerald-400 rounded-full transition-all duration-200 ease-out"
              style={{ width: `${simulatedChargingProgress}%` }}
            ></div>
          </div>
          <p className="label-quiet text-[10px] text-slate-400">
            Resumes Leg 2 to destination automatically upon completion.
          </p>
        </div>

        {/* Pause / Resume Controls during charging */}
        <div className="flex items-center gap-3 pt-2">
          {navState === NAV_STATE.CHARGING && (
            <button
              type="button"
              onClick={pauseSimulation}
              className="btn-secondary px-6 py-2.5 text-xs flex items-center gap-2 cursor-pointer text-amber-300"
            >
              <span>⏸ Pause Charging</span>
            </button>
          )}
        </div>
      </section>
    )
  }

  // -------------------------------------------------------------------------
  // STATE 3: ACTIVE DRIVING HUD (Clean, Immersive Navigation Readout)
  // -------------------------------------------------------------------------
  if (isDrivingActive) {
    const isLeg1 = currentLeg === LEG_TYPE.ORIGIN_TO_CHARGER
    const isLeg2 = currentLeg === LEG_TYPE.CHARGER_TO_DESTINATION

    return (
      <section className="py-6 space-y-6">
        {/* Navigation Corridor Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${navState === NAV_STATE.PAUSED ? 'bg-amber-400' : 'bg-cockpit-teal animate-pulse'}`}></span>
            <span className="label-subhead text-white">
              {navState === NAV_STATE.PAUSED
                ? 'Navigation Paused'
                : isLeg1
                ? 'En Route · Leg 1 to Charger'
                : isLeg2
                ? 'En Route · Leg 2 to Destination'
                : 'En Route · Direct Corridor'}
            </span>
          </div>

          <button
            type="button"
            onClick={clearDestination}
            className="text-slate-400 hover:text-rose-400 transition text-[11px] cursor-pointer"
          >
            Cancel Trip
          </button>
        </div>

        {/* Waypoint Corridor Typography */}
        <div className="display-metric text-white font-light flex items-center gap-2.5 flex-wrap">
          <span className={isLeg1 ? 'text-cockpit-teal' : 'text-slate-400'}>Mysuru</span>
          {journeyPlan?.decisionType === 'CHARGE' && journeyPlan.selectedCharger && (
            <>
              <span className="text-slate-600 text-lg">→</span>
              <span className={isLeg1 ? 'text-amber-300 font-normal' : 'text-slate-400'}>
                {journeyPlan.selectedCharger.name}
              </span>
            </>
          )}
          <span className="text-slate-600 text-lg">→</span>
          <span className={isLeg2 || navState === NAV_STATE.DRIVING ? 'text-white' : 'text-slate-400'}>
            Destination
          </span>
        </div>

        {/* Primary Driving Telemetry Display */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 items-baseline py-4 border-y border-white/5">
          <div>
            <span className="label-subhead block mb-1">Vehicle Speed</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-white font-light">
                {telemetry.speed_kmph.toFixed(0)}
              </span>
              <span className="text-xs text-slate-400">km/h</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Battery State</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-cockpit-teal font-light">
                {telemetry.current_soc_percent.toFixed(1)}
              </span>
              <span className="text-xs text-slate-400">%</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Energy Remaining</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-white font-light">
                {telemetry.remaining_energy_kwh.toFixed(1)}
              </span>
              <span className="text-xs text-slate-400">kWh</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Est Range</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-white font-light">
                {telemetry.estimated_range_km.toFixed(0)}
              </span>
              <span className="text-xs text-slate-400">km</span>
            </div>
          </div>
        </div>

        {/* Leg Progress Bar */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs label-quiet font-mono">
            <span>
              {isLeg1 ? 'Leg 1 Progress (to Charger)' : isLeg2 ? 'Leg 2 Progress (to Destination)' : 'Trip Progress'}
            </span>
            <span className="text-white">{legProgressPercent}%</span>
          </div>
          <div className="w-full bg-white/5 rounded-full h-[3px] overflow-hidden">
            <div
              className="h-full bg-cockpit-teal rounded-full transition-all duration-200 ease-out"
              style={{ width: `${legProgressPercent}%` }}
            ></div>
          </div>
        </div>

        {/* Driving Controls */}
        <div className="flex items-center gap-3 pt-2">
          {navState === NAV_STATE.PAUSED ? (
            <button
              type="button"
              onClick={resumeSimulation}
              className="btn-primary px-6 py-2.5 text-xs flex items-center gap-2 cursor-pointer"
            >
              <span>▶ Resume Drive</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={pauseSimulation}
              className="btn-secondary px-6 py-2.5 text-xs flex items-center gap-2 cursor-pointer text-amber-300"
            >
              <span>⏸ Pause</span>
            </button>
          )}

          <button
            type="button"
            onClick={clearDestination}
            className="btn-secondary px-5 py-2.5 text-xs cursor-pointer text-rose-300 hover:text-rose-200"
          >
            End Drive
          </button>
        </div>
      </section>
    )
  }

  // -------------------------------------------------------------------------
  // STATE 4: COMPLETED TRIP SUMMARY
  // -------------------------------------------------------------------------
  if (navState === NAV_STATE.COMPLETED && tripSummary) {
    return (
      <section className="py-8 space-y-6">
        <div className="space-y-1">
          <span className="label-subhead text-emerald-400 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            Journey Completed
          </span>
          <h2 className="display-large text-white font-light tracking-tight">
            Arrived Safely
          </h2>
          <p className="label-quiet">
            Simulation concluded across road network with authoritative physics tracking.
          </p>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 items-baseline py-6 border-y border-white/5">
          <div>
            <span className="label-subhead block mb-1">Distance Driven</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-white font-light">
                {tripSummary.totalDistanceKm.toFixed(1)}
              </span>
              <span className="text-xs text-slate-400">km</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Energy Consumed</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-white font-light">
                {tripSummary.finalEnergyConsumedKwh.toFixed(2)}
              </span>
              <span className="text-xs text-slate-400">kWh</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Final Battery</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-emerald-400 font-light">
                {tripSummary.finalSocPercent.toFixed(1)}
              </span>
              <span className="text-xs text-slate-400">%</span>
            </div>
          </div>

          <div>
            <span className="label-subhead block mb-1">Charge Added</span>
            <div className="flex items-baseline gap-1">
              <span className="display-metric tabular-nums text-amber-300 font-light">
                {tripSummary.chargingEnergyAddedKwh > 0 ? `+${tripSummary.chargingEnergyAddedKwh.toFixed(1)}` : '0.0'}
              </span>
              <span className="text-xs text-slate-400">kWh</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-4 pt-2">
          <button
            type="button"
            onClick={clearDestination}
            className="btn-primary px-7 py-3 text-xs flex items-center gap-2 cursor-pointer"
          >
            <span>Plan Another Journey</span>
            <span>→</span>
          </button>
          <button
            type="button"
            onClick={startSimulation}
            className="btn-secondary px-6 py-3 text-xs cursor-pointer text-slate-300"
          >
            Drive Again
          </button>
        </div>
      </section>
    )
  }

  // -------------------------------------------------------------------------
  // STATE 5: PLANNED JOURNEY STATE (Ready to drive / Decision analysis)
  // -------------------------------------------------------------------------
  return (
    <section className="py-6 space-y-6">
      {/* Route Corridor Header */}
      <div>
        <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
          <span className="label-subhead">Route Corridor</span>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => planJourneyAction()}
              disabled={isPlanning}
              className="text-cockpit-teal hover:underline text-[11px] font-medium cursor-pointer transition disabled:opacity-40"
            >
              {isPlanning ? 'Evaluating...' : 'Recalculate'}
            </button>
            <span className="text-slate-600">·</span>
            <button
              type="button"
              onClick={startSelectingDestination}
              className="text-slate-400 hover:text-white transition text-[11px] cursor-pointer"
            >
              Change
            </button>
            <span className="text-slate-600">·</span>
            <button
              type="button"
              onClick={clearDestination}
              className="text-slate-400 hover:text-rose-400 transition text-[11px] cursor-pointer"
            >
              Clear
            </button>
          </div>
        </div>

        {/* Waypoint Typography Display */}
        <div className="display-metric text-white font-light flex items-center gap-2.5 flex-wrap">
          <span>Mysuru</span>
          {journeyPlan?.decisionType === 'CHARGE' && journeyPlan.selectedCharger && (
            <>
              <span className="text-slate-600 text-lg">→</span>
              <span className="text-amber-300 font-normal">
                {journeyPlan.selectedCharger.name}
              </span>
            </>
          )}
          <span className="text-slate-600 text-lg">→</span>
          <span className="text-white">
            {destLatFormatted ? `Target (${destLatFormatted}, ${destLonFormatted})` : 'Destination'}
          </span>
        </div>
      </div>

      {/* Loading State */}
      {isPlanning && (
        <div className="py-6 flex items-center gap-3 text-xs text-slate-400">
          <div className="animate-spin rounded-full h-4 w-4 border-2 border-cockpit-teal border-t-transparent"></div>
          <span>Evaluating direct reachability and charging stops via /trip/plan...</span>
        </div>
      )}

      {/* Planning Error */}
      {planError && !isPlanning && (
        <div className="p-4 rounded-2xl bg-rose-950/20 border border-rose-800/40 text-xs text-rose-300 space-y-1">
          <div className="flex items-center justify-between">
            <strong className="font-semibold">Planning Notice</strong>
            <button
              onClick={() => planJourneyAction()}
              className="underline text-rose-200 hover:text-white cursor-pointer"
            >
              Retry
            </button>
          </div>
          <p className="label-quiet text-rose-200/90">{planError}</p>
        </div>
      )}

      {/* INFEASIBLE Plan Result */}
      {journeyPlan?.decisionType === 'INFEASIBLE' && !isPlanning && (
        <div className="p-6 rounded-2xl bg-rose-950/20 border border-rose-800/30 space-y-3">
          <div className="flex items-center gap-2 text-rose-400 font-medium text-xs">
            <span className="w-2 h-2 rounded-full bg-rose-400"></span>
            <span className="label-subhead text-rose-400">Trip Infeasible</span>
          </div>
          <h3 className="text-xl font-light text-white tracking-tight">
            Cannot Reach Destination with Current Battery
          </h3>
          <p className="text-xs text-slate-300 leading-relaxed max-w-lg">
            {journeyPlan.explanation || 'No route or charging station is reachable within your remaining battery capacity.'}
          </p>
          <div className="pt-2">
            <button
              type="button"
              onClick={startSelectingDestination}
              className="btn-secondary px-5 py-2 text-xs cursor-pointer text-white"
            >
              Choose Closer Waypoint
            </button>
          </div>
        </div>
      )}

      {/* DIRECT Plan Result */}
      {journeyPlan?.decisionType === 'DIRECT' && !isPlanning && (
        <div className="space-y-6">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-400"></span>
            <span className="label-subhead text-emerald-400">Direct Route · Reachable Without Charging</span>
          </div>

          {/* Large Metric Composition */}
          <div className="grid grid-cols-3 gap-6 items-baseline py-4 border-y border-white/5">
            <div>
              <span className="label-subhead block mb-1">Route Distance</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {journeyPlan.routeMetrics.totalDistanceKm.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400">km</span>
              </div>
            </div>

            <div>
              <span className="label-subhead block mb-1">Estimated Time</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {Math.round(journeyPlan.routeMetrics.travelTimeMinutes)}
                </span>
                <span className="text-xs text-slate-400">min</span>
              </div>
            </div>

            <div>
              <span className="label-subhead block mb-1">Energy Required</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {journeyPlan.routeMetrics.totalEnergyKwh.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400">kWh</span>
              </div>
              <span className="label-quiet block text-[10px] mt-0.5 font-mono">0.150 kWh/km baseline</span>
            </div>
          </div>

          <div className="flex items-center justify-between text-xs label-quiet">
            <span>
              Arrival battery reserve:{' '}
              <strong className="text-emerald-400 font-mono">
                +{journeyPlan.energyAccounting.arrivalEnergyKwh.toFixed(1)} kWh
              </strong>{' '}
              remaining
            </span>
          </div>
        </div>
      )}

      {/* CHARGE Plan Result */}
      {journeyPlan?.decisionType === 'CHARGE' && journeyPlan.selectedCharger && !isPlanning && (
        <div className="space-y-6">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
            <span className="label-subhead text-amber-400">Charging Stop Recommended</span>
          </div>

          {/* Recommended Charger Editorial Surface */}
          <div className="p-6 rounded-2xl bg-amber-950/15 border border-amber-500/20 space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <span className="label-subhead text-amber-400 block mb-1">Recommended Charge Stop</span>
                <h3 className="display-metric text-white font-light">
                  {journeyPlan.selectedCharger.name}
                </h3>
              </div>
              <span className="px-3 py-1 rounded-full bg-amber-400/10 text-amber-300 font-mono text-xs font-semibold border border-amber-500/30 shrink-0">
                ⚡ {journeyPlan.selectedCharger.powerKw} kW Fast Charger
              </span>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 pt-3 border-t border-white/5 text-xs font-mono tabular-nums">
              <div>
                <span className="label-quiet block text-[10px] font-sans">Wait Time</span>
                <strong className="text-white text-sm">{journeyPlan.routeMetrics.chargingWaitMinutes} min</strong>
              </div>
              <div>
                <span className="label-quiet block text-[10px] font-sans">Charge Duration</span>
                <strong className="text-white text-sm">{journeyPlan.routeMetrics.chargingDurationMinutes} min</strong>
              </div>
              <div>
                <span className="label-quiet block text-[10px] font-sans">Planned Energy</span>
                <strong className="text-amber-300 text-sm">+{journeyPlan.energyAccounting.energyAddedKwh.toFixed(1)} kWh</strong>
              </div>
              <div>
                <span className="label-quiet block text-[10px] font-sans">Reliability</span>
                <strong className="text-emerald-400 text-sm">{(journeyPlan.selectedCharger.reliability * 100).toFixed(0)}%</strong>
              </div>
            </div>
          </div>

          {/* Journey Totals */}
          <div className="grid grid-cols-3 gap-6 items-baseline py-4 border-y border-white/5">
            <div>
              <span className="label-subhead block mb-1">Total Distance</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {journeyPlan.routeMetrics.totalDistanceKm.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400">km</span>
              </div>
            </div>

            <div>
              <span className="label-subhead block mb-1">Total Duration</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {Math.round(journeyPlan.routeMetrics.travelTimeMinutes)}
                </span>
                <span className="text-xs text-slate-400">min</span>
              </div>
              <span className="label-quiet block text-[10px] mt-0.5">Includes drive + charging</span>
            </div>

            <div>
              <span className="label-subhead block mb-1">Total Energy</span>
              <div className="flex items-baseline gap-1">
                <span className="display-large tabular-nums text-white font-light">
                  {journeyPlan.routeMetrics.totalEnergyKwh.toFixed(1)}
                </span>
                <span className="text-xs text-slate-400">kWh</span>
              </div>
              <span className="label-quiet block text-[10px] mt-0.5 font-mono">0.150 kWh/km baseline</span>
            </div>
          </div>
        </div>
      )}

      {/* Primary Action Button: Start Active Drive */}
      {journeyPlan?.decisionType !== 'INFEASIBLE' && (journeyPlan || trip.route) && (
        <div className="pt-2 flex items-center gap-4">
          <button
            type="button"
            onClick={startSimulation}
            className="btn-primary px-8 py-3 text-xs flex items-center gap-2 cursor-pointer shadow-lg shadow-teal-500/10 font-medium"
          >
            <span>▶ Start Active Drive</span>
          </button>

          <button
            type="button"
            onClick={resetSimulation}
            disabled={
              overallTripProgressPercent === 0 &&
              telemetry.vehicle_status === 'Standby (Parked)' &&
              telemetry.energy_consumed_kwh === 0
            }
            className="text-xs text-slate-400 hover:text-white transition disabled:opacity-30 cursor-pointer ml-auto"
          >
            Reset
          </button>
        </div>
      )}

      {/* AI Energy Intelligence (Driver Advisory Presentation) */}
      {journeyPlan?.decisionType !== 'INFEASIBLE' && (journeyPlan || trip.route) && (
        <div className="pt-4 border-t border-white/5 space-y-3">
          <div className="flex items-center justify-between">
            <span className="label-subhead">AI Energy Intelligence</span>
            <button
              type="button"
              onClick={() => setShowAiDetails(!showAiDetails)}
              className="text-slate-400 hover:text-white text-[11px] transition cursor-pointer"
            >
              {showAiDetails ? 'Hide details' : 'Model details'}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-6 text-xs">
            <div>
              <span className="label-quiet block text-[10px]">XGBoost Journey Prediction</span>
              <div className="text-2xl font-light text-white font-mono tabular-nums mt-1">
                {journeyPlan?.aiEnergyPrediction?.predictedEnergyKwh !== undefined &&
                journeyPlan?.aiEnergyPrediction?.predictedEnergyKwh !== null
                  ? `${journeyPlan.aiEnergyPrediction.predictedEnergyKwh.toFixed(1)} kWh`
                  : aiTripPrediction?.predicted_trip_energy_kwh !== undefined &&
                    aiTripPrediction?.predicted_trip_energy_kwh !== null
                  ? `${aiTripPrediction.predicted_trip_energy_kwh.toFixed(1)} kWh`
                  : 'Evaluating...'}
              </div>
              {journeyPlan?.aiEnergyPrediction && (
                <p className="label-quiet mt-0.5 font-mono text-[10px]">
                  {journeyPlan.aiEnergyPrediction.deltaKwh >= 0 ? '+' : ''}
                  {journeyPlan.aiEnergyPrediction.deltaKwh.toFixed(2)} kWh ({journeyPlan.aiEnergyPrediction.deltaPercent >= 0 ? '+' : ''}
                  {journeyPlan.aiEnergyPrediction.deltaPercent.toFixed(1)}%) vs physical
                </p>
              )}
            </div>

            <div>
              <span className="label-quiet block text-[10px]">Near-Term Forecast (LSTM 30s)</span>
              <div className="text-2xl font-light text-sky-400 font-mono tabular-nums mt-1">
                {aiNearTermPrediction?.status === 'success'
                  ? `${aiNearTermPrediction.predicted_future_energy_kwh.toFixed(3)} kWh`
                  : 'Stable'}
              </div>
              <p className="label-quiet mt-0.5 text-[10px]">
                {aiNearTermPrediction?.status === 'success'
                  ? 'Sequential telemetry dynamics'
                  : 'Building telemetry buffer'}
              </p>
            </div>
          </div>

          {showAiDetails && (
            <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 text-[10px] text-slate-400 leading-relaxed">
              <span className="text-cockpit-teal font-semibold">Authoritative Separation: </span>
              Physical baseline model (0.150 kWh/km) governs hard battery feasibility and Virtual EV state.
              AI prediction informs candidate cost ranking without mutating live battery telemetry.
            </div>
          )}
        </div>
      )}
    </section>
  )
}

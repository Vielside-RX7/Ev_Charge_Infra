import React, { createContext, useContext, useState, useMemo, useCallback, useEffect, useRef } from 'react'
import axios from 'axios'
import { useTelemetry } from '../telemetry/TelemetryContext'
import { useVehicleProfile } from '../telemetry/VehicleProfileContext'
import { TRIP_STATUS, NAV_STATE, LEG_TYPE, createTripState } from './tripModel.js'
import { tripRecorder } from './tripRecorder.js'
import { planJourney } from './tripPlanningService.js'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const TripContext = createContext(null)

/**
 * TripProvider wraps the application and supplies active trip state, journey planning,
 * and the authoritative Change 23 / Change 28 Navigation State Machine.
 *
 * Navigation State Machine (N-Leg Multi-Stop):
 *   IDLE -> PLANNING -> READY -> DRIVING -> COMPLETED              (Direct, 0 stops)
 *   IDLE -> PLANNING -> READY -> DRIVING_LEG_1 -> CHARGING ->
 *           DRIVING_LEG_2 -> [CHARGING -> DRIVING_LEG_N ...] ->
 *           COMPLETED                                              (Multi-Stop, N stops)
 *   Pause / Resume allowed during any DRIVING_LEG_* or CHARGING state.
 *   Reset restores vehicle state, route progress, and stops all timers.
 *
 * Change 28 Extension:
 *   The runner now iterates over journeyPlan.multiStopLegs[] and
 *   journeyPlan.chargingStops[] to execute each drive-charge cycle dynamically.
 *   currentLegIndex tracks which driving leg is active (0-indexed).
 *   activeChargingStopIndex tracks which charging stop is active (0-indexed).
 */
export function TripProvider({ children }) {
  const { telemetry, updateTelemetry, chargeTelemetry, resetTelemetry } = useTelemetry()
  const { vehicleProfile } = useVehicleProfile()

  // Selected destination coordinates { latitude, longitude } or null
  const [destination, setDestinationState] = useState(null)

  // Destination selection mode on map
  const [isSelectingDestination, setIsSelectingDestination] = useState(false)

  // Journey Plan state from backend POST /trip/plan
  const [journeyPlan, setJourneyPlan] = useState(null)
  const [isPlanning, setIsPlanning] = useState(false)
  const [planError, setPlanError] = useState(null)

  // Route state fallback and points cache for map & simulation
  const [routeData, setRouteData] = useState(null)

  // Explicit Change 23 / 28 Navigation State Machine
  const [navState, setNavState] = useState(NAV_STATE.IDLE)
  const [currentLeg, setCurrentLeg] = useState(null) // LEG_TYPE or null
  const [legProgressPercent, setLegProgressPercent] = useState(0) // 0..100
  const [overallTripProgressPercent, setOverallTripProgressPercent] = useState(0) // 0..100

  // Multi-stop leg index tracking (Change 28)
  const [currentLegIndex, setCurrentLegIndex] = useState(0) // 0..N (index into multiStopLegs)
  const [activeChargingStopIndex, setActiveChargingStopIndex] = useState(0) // 0..N-1

  // Simulation execution tracking
  const [simulationProgress, setSimulationProgress] = useState(0.0) // 0.0 .. 1.0 overall progress
  const [simulatedChargingProgress, setSimulatedChargingProgress] = useState(0) // 0..100% during CHARGING
  const [chargingEnergyAddedSoFar, setChargingEnergyAddedSoFar] = useState(0.0) // kWh added during charge

  // Completion summary metrics
  const [tripSummary, setTripSummary] = useState(null)

  // Timers and operational refs
  const driveTimerRef = useRef(null)
  const chargeTimerRef = useRef(null)
  const currentLegRef = useRef(null)
  const pausedStateRef = useRef(null)
  const chargeStepRef = useRef(0)

  // Active telemetry reference to prevent stale closures
  const telemetryRef = useRef(telemetry)
  useEffect(() => {
    telemetryRef.current = telemetry
  }, [telemetry])

  // Progress references across legs
  const legProgressRef = useRef(0.0) // 0.0 .. 1.0 within current active leg
  const lastLegProgressRef = useRef(0.0)
  const tripStartTimeRef = useRef(null)

  // Multi-stop planner: save leg/stop sequence refs for resume after pause
  const legSequenceRef = useRef([]) // [{leg, stop?}] computed at startSimulation
  const currentLegIdxRef = useRef(0)

  // Clear all running timers on unmount
  useEffect(() => {
    return () => {
      if (driveTimerRef.current) clearInterval(driveTimerRef.current)
      if (chargeTimerRef.current) clearInterval(chargeTimerRef.current)
    }
  }, [])

  // Call POST /trip/plan using live Virtual EV telemetry + vehicle profile
  const executePlanJourney = useCallback(
    async (customOptions = {}) => {
      if (!destination || typeof destination.latitude !== 'number' || typeof destination.longitude !== 'number') {
        setJourneyPlan(null)
        setRouteData(null)
        setNavState(NAV_STATE.IDLE)
        setCurrentLeg(null)
        return null
      }

      setIsPlanning(true)
      setPlanError(null)
      setNavState(NAV_STATE.PLANNING)

      try {
        const currentTelem = telemetryRef.current
        const plan = await planJourney({
          vehicleId: vehicleProfile?.vehicle_id || 'EV-001',
          batteryCapacityKwh: currentTelem.battery_capacity_kwh || vehicleProfile?.battery_capacity_kwh || 30.0,
          currentSocPercent: currentTelem.current_soc_percent,
          originLatitude: currentTelem.latitude,
          originLongitude: currentTelem.longitude,
          destinationLatitude: destination.latitude,
          destinationLongitude: destination.longitude,
          connectorType: vehicleProfile?.connector_type || 'CCS2',
          targetSocPercent: customOptions.targetSocPercent || 80.0,
          candidateRadiusKm: customOptions.candidateRadiusKm || 25.0,
          reserveSocPercent: customOptions.reserveSocPercent || 5.0,
          energyConsumptionKwhPerKm: customOptions.energyConsumptionKwhPerKm || 0.15,
          drivingStyle: customOptions.drivingStyle || 'balanced',
        })

        setJourneyPlan(plan)

        if (plan.decisionType === 'INFEASIBLE') {
          setRouteData(null)
          setNavState(NAV_STATE.INFEASIBLE)
          setCurrentLeg(null)
        } else {
          setRouteData({
            distance_km: plan.routeMetrics.totalDistanceKm,
            travel_time_minutes: plan.routeMetrics.travelTimeMinutes,
            polylinePoints: plan.fullPolylinePoints,
          })
          setNavState(NAV_STATE.READY)
          // For multi-stop, always start at leg 1; for direct, use DIRECT
          setCurrentLeg(plan.decisionType === 'DIRECT' ? LEG_TYPE.DIRECT : LEG_TYPE.ORIGIN_TO_CHARGER)
        }

        return plan
      } catch (err) {
        console.error('Failed to plan journey via /trip/plan:', err)
        const msg =
          err?.response?.data?.detail || err?.message || 'Unable to compute optimal journey plan'
        setPlanError(typeof msg === 'object' ? msg.message || JSON.stringify(msg) : msg)
        setJourneyPlan(null)
        setRouteData(null)
        setNavState(NAV_STATE.INFEASIBLE)
        setCurrentLeg(null)
        return null
      } finally {
        setIsPlanning(false)
      }
    },
    [destination, vehicleProfile]
  )

  // Clear plan when destination is reset to null
  useEffect(() => {
    if (!destination) {
      setJourneyPlan(null)
      setPlanError(null)
      setIsPlanning(false)
      setRouteData(null)
      setNavState(NAV_STATE.IDLE)
      setCurrentLeg(null)
      setLegProgressPercent(0)
      setOverallTripProgressPercent(0)
    }
  }, [destination])

  // Derived canonical Trip State
  const trip = useMemo(() => {
    const baseState = createTripState({
      originLat: telemetry.latitude,
      originLon: telemetry.longitude,
      destinationLat: destination ? destination.latitude : null,
      destinationLon: destination ? destination.longitude : null,
      route: routeData,
      isRouteLoading: isPlanning,
      routeError: planError,
      availableEnergyKwh: telemetry.remaining_energy_kwh,
    })

    baseState.destination_name = destination?.name || null
    baseState.destination_address = destination?.address || null
    baseState.destination_place_id = destination?.placeId || null

    if (journeyPlan) {
      baseState.destination_feasible = journeyPlan.decisionType === 'DIRECT'
      baseState.charging_required = journeyPlan.decisionType === 'CHARGE'
      baseState.is_infeasible = journeyPlan.decisionType === 'INFEASIBLE'
      baseState.estimated_trip_energy_kwh = journeyPlan.routeMetrics.totalEnergyKwh
      baseState.journeyPlan = journeyPlan
    }

    return baseState
  }, [telemetry.latitude, telemetry.longitude, telemetry.remaining_energy_kwh, destination, routeData, isPlanning, planError, journeyPlan])

  // Helper: Cleans all active driving/charging interval timers
  const stopAllTimers = () => {
    if (driveTimerRef.current) {
      clearInterval(driveTimerRef.current)
      driveTimerRef.current = null
    }
    if (chargeTimerRef.current) {
      clearInterval(chargeTimerRef.current)
      chargeTimerRef.current = null
    }
  }

  // ---------------------------------------------------------------------------
  // DRIVING LEG SIMULATOR
  // Progresses the Virtual EV along road polyline points, applying incremental
  // physics-based discharge (0.15 kWh/km) and updating telemetry continuously.
  // ---------------------------------------------------------------------------
  const runDriveLeg = useCallback(
    ({
      legType,
      points,
      legDistanceKm,
      legTravelTimeMin,
      totalTripDistanceKm,
      priorDistanceTraveledKm = 0,
      onLegComplete,
    }) => {
      stopAllTimers()

      if (!points || points.length === 0) {
        if (onLegComplete) onLegComplete()
        return
      }

      currentLegRef.current = legType
      setCurrentLeg(legType)
      const maxIdx = points.length - 1
      const safeLegDist = Math.max(0.1, Number(legDistanceKm) || 1.0)
      const simulatedSpeed = Math.max(
        35.0,
        Math.min(65.0, Math.round((safeLegDist / ((legTravelTimeMin || 10) / 60)) * 1.1))
      )

      updateTelemetry({
        vehicle_status: 'Driving',
        speed_kmph: simulatedSpeed,
      })

      const TICK_MS = 100
      const LEG_DURATION_MS = 5000
      const STEP_PROGRESS = TICK_MS / LEG_DURATION_MS

      driveTimerRef.current = setInterval(() => {
        legProgressRef.current += STEP_PROGRESS
        const clampedLegP = Math.min(1.0, legProgressRef.current)

        const progressDelta = Math.max(0, clampedLegP - lastLegProgressRef.current)
        lastLegProgressRef.current = clampedLegP
        const incrementalDist = progressDelta * safeLegDist

        const exactIdx = clampedLegP * maxIdx
        const i = Math.floor(exactIdx)
        const frac = exactIdx - i

        let currentLat, currentLon
        if (i >= maxIdx) {
          currentLat = points[maxIdx][0]
          currentLon = points[maxIdx][1]
        } else {
          const p1 = points[i]
          const p2 = points[i + 1]
          currentLat = p1[0] + frac * (p2[0] - p1[0])
          currentLon = p1[1] + frac * (p2[1] - p1[1])
        }

        const currentTotalTraveledKm = priorDistanceTraveledKm + clampedLegP * safeLegDist
        const overallP = totalTripDistanceKm > 0
          ? Math.min(1.0, currentTotalTraveledKm / totalTripDistanceKm)
          : clampedLegP

        setLegProgressPercent(Math.round(clampedLegP * 100))
        setOverallTripProgressPercent(Math.round(overallP * 100))
        setSimulationProgress(overallP)

        updateTelemetry({
          latitude: parseFloat(currentLat.toFixed(6)),
          longitude: parseFloat(currentLon.toFixed(6)),
          speed_kmph: simulatedSpeed,
          vehicle_status: 'Driving',
          incremental_distance_km: incrementalDist,
        })

        const freshTelem = telemetryRef.current
        tripRecorder.recordSample({
          latitude: currentLat,
          longitude: currentLon,
          speedKmph: simulatedSpeed,
          currentSocPercent: freshTelem.current_soc_percent,
          energyConsumedKwh: freshTelem.energy_consumed_kwh,
        })

        if (clampedLegP >= 1.0) {
          stopAllTimers()
          if (onLegComplete) {
            onLegComplete()
          }
        }
      }, TICK_MS)
    },
    [updateTelemetry]
  )

  // ---------------------------------------------------------------------------
  // SIMULATED CHARGING
  // Adds planned charging energy over ~3.5 seconds, then calls onChargingComplete.
  // Supports deterministic pause and resume via chargeStepRef.
  // ---------------------------------------------------------------------------
  const runChargingSimulation = useCallback(
    (chargingStop, onChargingComplete, isResume = false) => {
      stopAllTimers()
      setNavState(NAV_STATE.CHARGING)

      const chargerName = chargingStop?.chargerName || chargingStop?.name || 'Fast Charger'
      const energyToAdd = Number(chargingStop?.energyAddedKwh) || 10.0

      updateTelemetry({
        speed_kmph: 0.0,
        vehicle_status: `Charging at ${chargerName}`,
      })

      const TICK_MS = 100
      const CHARGE_DURATION_MS = 3500
      const totalSteps = Math.round(CHARGE_DURATION_MS / TICK_MS)
      const energyPerStep = energyToAdd / totalSteps

      if (!isResume) {
        chargeStepRef.current = 0
        setSimulatedChargingProgress(0)
        setChargingEnergyAddedSoFar(0.0)
      }

      chargeTimerRef.current = setInterval(() => {
        chargeStepRef.current += 1
        const step = chargeStepRef.current
        const pct = Math.min(100, Math.round((step / totalSteps) * 100))
        const addedNow = Math.min(energyToAdd, parseFloat((step * energyPerStep).toFixed(3)))

        setSimulatedChargingProgress(pct)
        setChargingEnergyAddedSoFar(addedNow)

        chargeTelemetry(energyPerStep, `Charging (${addedNow.toFixed(1)} / ${energyToAdd.toFixed(1)} kWh)`)

        if (step >= totalSteps) {
          stopAllTimers()
          chargeStepRef.current = 0
          updateTelemetry({
            speed_kmph: 0.0,
            vehicle_status: `Charging Complete · Departing ${chargerName}`,
          })
          if (onChargingComplete) {
            onChargingComplete()
          }
        }
      }, TICK_MS)
    },
    [chargeTelemetry, updateTelemetry]
  )

  // ---------------------------------------------------------------------------
  // DESTINATION ARRIVAL
  // ---------------------------------------------------------------------------
  const handleDestinationArrival = useCallback(
    (plan) => {
      stopAllTimers()
      setNavState(NAV_STATE.COMPLETED)
      setCurrentLeg(null)
      setLegProgressPercent(100)
      setOverallTripProgressPercent(100)
      setSimulationProgress(1.0)

      const freshTelem = telemetryRef.current
      const totalDist = Number(plan.routeMetrics?.totalDistanceKm) || 0
      const energyUsed = Number(freshTelem.energy_consumed_kwh) || (totalDist * 0.15)
      const totalChargingAdded = (plan.chargingStops || []).reduce((acc, s) => acc + (Number(s.energyAddedKwh) || 0), 0)
      const finalDuration = tripStartTimeRef.current
        ? Math.max(1, Math.round((Date.now() - tripStartTimeRef.current) / 1000))
        : Math.round(plan.routeMetrics?.travelTimeMinutes || 15)

      tripRecorder.completeTrip({
        finalDistanceKm: totalDist,
        finalDurationMinutes: finalDuration / 60,
        finalEnergyConsumedKwh: energyUsed,
      })

      updateTelemetry({
        speed_kmph: 0.0,
        vehicle_status: 'Destination Reached',
      })

      setTripSummary({
        totalDistanceKm: totalDist,
        finalEnergyConsumedKwh: energyUsed,
        finalSocPercent: freshTelem.current_soc_percent,
        finalRemainingEnergyKwh: freshTelem.remaining_energy_kwh,
        chargingEnergyAddedKwh: totalChargingAdded,
        chargingStopCount: plan.chargingStopCount || 0,
        tripDurationSeconds: finalDuration,
      })

      // Persist completed journey to backend trip ledger
      const stopsSummary = (plan.chargingStops || [])
        .map((s, idx) => `Stop ${idx + 1}: ${s.chargerName || 'Station'} (${s.chargingPowerKw || 50} kW)`)
        .join(', ')

      const destinationName = destination?.name || (destination ? `${destination.latitude.toFixed(4)}, ${destination.longitude.toFixed(4)}` : 'Destination')

      axios.post(`${API_BASE_URL}/trips/history`, {
        origin_name: 'Mysuru Origin',
        origin_lat: Number(freshTelem.latitude) || 12.2958,
        origin_lon: Number(freshTelem.longitude) || 76.6394,
        dest_name: destinationName,
        dest_lat: Number(destination?.latitude) || 12.2958,
        dest_lon: Number(destination?.longitude) || 76.6394,
        total_distance_km: totalDist,
        total_energy_kwh: energyUsed,
        total_travel_time_minutes: finalDuration / 60,
        charging_stop_count: plan.chargingStopCount || 0,
        charging_stops_summary: stopsSummary || 'Direct route (0 stops)',
        vehicle_name: vehicleProfile?.vehicle_name || 'Tata Nexon EV Max',
        status: 'COMPLETED',
      }).catch((err) => {
        console.warn('Trip history background sync notice:', err?.message)
      })
    },
    [updateTelemetry, destination, vehicleProfile]
  )

  // ---------------------------------------------------------------------------
  // MULTI-STOP N-LEG RUNNER
  // Recursively chains: DriveLeg[i] → ChargingStop[i] → DriveLeg[i+1] → ...
  // Works for 0 stops (DIRECT), 1 stop, or N stops.
  // ---------------------------------------------------------------------------
  const runLegSequence = useCallback(
    (plan, legIdx, priorDistKm) => {
      const legs = plan.multiStopLegs || []
      const stops = plan.chargingStops || []
      const totalDist = Number(plan.routeMetrics?.totalDistanceKm) || 5.0

      if (legIdx >= legs.length) {
        // All legs done → Arrived
        handleDestinationArrival(plan)
        return
      }

      const leg = legs[legIdx]
      currentLegIdxRef.current = legIdx
      setCurrentLegIndex(legIdx)

      // Determine legType label for UI
      const isLast = legIdx === legs.length - 1
      let legType
      if (legs.length === 1) {
        legType = LEG_TYPE.DIRECT
      } else if (legIdx === 0) {
        legType = LEG_TYPE.ORIGIN_TO_CHARGER
      } else if (isLast) {
        legType = LEG_TYPE.CHARGER_TO_DESTINATION
      } else {
        legType = LEG_TYPE.ORIGIN_TO_CHARGER // intermediate driving leg
      }

      // NAV_STATE for driving
      const drivingNavState = legs.length === 1
        ? NAV_STATE.DRIVING
        : legIdx === 0
        ? NAV_STATE.DRIVING_LEG_1
        : NAV_STATE.DRIVING_LEG_2

      setNavState(drivingNavState)
      legProgressRef.current = 0.0
      lastLegProgressRef.current = 0.0
      setLegProgressPercent(0)

      runDriveLeg({
        legType,
        points: leg.polylinePoints || [],
        legDistanceKm: leg.distanceKm,
        legTravelTimeMin: leg.travelTimeMinutes,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: priorDistKm,
        onLegComplete: () => {
          // Is there a charging stop after this driving leg?
          const stopAfterThisLeg = stops[legIdx]
          if (stopAfterThisLeg && !isLast) {
            setActiveChargingStopIndex(legIdx)
            runChargingSimulation(stopAfterThisLeg, () => {
              runLegSequence(plan, legIdx + 1, priorDistKm + leg.distanceKm)
            })
          } else {
            // No charging stop (final leg or direct) → go to next leg or arrival
            runLegSequence(plan, legIdx + 1, priorDistKm + leg.distanceKm)
          }
        },
      })
    },
    [runDriveLeg, runChargingSimulation, handleDestinationArrival]
  )

  // ---------------------------------------------------------------------------
  // START SIMULATION / NAVIGATION
  // ---------------------------------------------------------------------------
  const startSimulation = useCallback(() => {
    if (!journeyPlan || journeyPlan.decisionType === 'INFEASIBLE') return

    stopAllTimers()
    tripStartTimeRef.current = Date.now()
    setTripSummary(null)
    setCurrentLegIndex(0)
    setActiveChargingStopIndex(0)

    const totalDist = Number(journeyPlan.routeMetrics?.totalDistanceKm) || 5.0
    const travelTimeMin = Number(journeyPlan.routeMetrics?.travelTimeMinutes) || 15

    const currentTelem = telemetryRef.current
    tripRecorder.startTrip({
      vehicleId: currentTelem.vehicle_id || vehicleProfile?.vehicle_id || 'EV-001',
      batteryCapacityKwh: currentTelem.battery_capacity_kwh,
      startingSocPercent: currentTelem.current_soc_percent,
      distanceKm: totalDist,
      estimatedDurationMinutes: travelTimeMin,
      drivingStyle: 'Normal',
    })

    setOverallTripProgressPercent(0)
    setSimulationProgress(0.0)

    // Use multi-stop leg sequence if available; fall back to legacy 2-leg path
    const multiLegs = journeyPlan.multiStopLegs || []
    if (multiLegs.length > 0) {
      runLegSequence(journeyPlan, 0, 0)
    } else {
      // Legacy direct / single-stop fallback
      const isCharge = journeyPlan.decisionType === 'CHARGE'
      if (!isCharge) {
        setNavState(NAV_STATE.DRIVING)
        setCurrentLeg(LEG_TYPE.DIRECT)
        legProgressRef.current = 0.0
        lastLegProgressRef.current = 0.0
        setLegProgressPercent(0)

        const directPoints = journeyPlan.legs?.direct?.polylinePoints || journeyPlan.fullPolylinePoints || []
        runDriveLeg({
          legType: LEG_TYPE.DIRECT,
          points: directPoints,
          legDistanceKm: journeyPlan.legs?.direct?.distanceKm || totalDist,
          legTravelTimeMin: journeyPlan.legs?.direct?.travelTimeMinutes || 10,
          totalTripDistanceKm: totalDist,
          priorDistanceTraveledKm: 0,
          onLegComplete: () => handleDestinationArrival(journeyPlan),
        })
      } else {
        setNavState(NAV_STATE.DRIVING_LEG_1)
        setCurrentLeg(LEG_TYPE.ORIGIN_TO_CHARGER)
        legProgressRef.current = 0.0
        lastLegProgressRef.current = 0.0
        setLegProgressPercent(0)

        const leg1Points = journeyPlan.legs?.originToCharger?.polylinePoints || []
        const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
        const leg1Time = Number(journeyPlan.legs?.originToCharger?.travelTimeMinutes) || 10

        const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
        const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
        const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10

        // Use selectedCharger as the charging stop object for legacy path
        const legacyStop = journeyPlan.selectedCharger
          ? { chargerName: journeyPlan.selectedCharger.name, energyAddedKwh: journeyPlan.energyAccounting?.energyAddedKwh || 10 }
          : null

        runDriveLeg({
          legType: LEG_TYPE.ORIGIN_TO_CHARGER,
          points: leg1Points,
          legDistanceKm: leg1Dist,
          legTravelTimeMin: leg1Time,
          totalTripDistanceKm: totalDist,
          priorDistanceTraveledKm: 0,
          onLegComplete: () => {
            runChargingSimulation(legacyStop, () => {
              setNavState(NAV_STATE.DRIVING_LEG_2)
              setCurrentLeg(LEG_TYPE.CHARGER_TO_DESTINATION)
              legProgressRef.current = 0.0
              lastLegProgressRef.current = 0.0
              setLegProgressPercent(0)

              runDriveLeg({
                legType: LEG_TYPE.CHARGER_TO_DESTINATION,
                points: leg2Points,
                legDistanceKm: leg2Dist,
                legTravelTimeMin: leg2Time,
                totalTripDistanceKm: totalDist,
                priorDistanceTraveledKm: leg1Dist,
                onLegComplete: () => handleDestinationArrival(journeyPlan),
              })
            })
          },
        })
      }
    }
  }, [journeyPlan, vehicleProfile, runLegSequence, runDriveLeg, runChargingSimulation, handleDestinationArrival])

  // ---------------------------------------------------------------------------
  // PAUSE / RESUME
  // For multi-stop journeys, resume restarts from the current leg index.
  // ---------------------------------------------------------------------------
  const pauseSimulation = useCallback(() => {
    stopAllTimers()
    pausedStateRef.current = navState
    setNavState(NAV_STATE.PAUSED)
    updateTelemetry({
      speed_kmph: 0.0,
      vehicle_status: 'Paused',
    })
  }, [navState, updateTelemetry])

  const resumeSimulation = useCallback(() => {
    if (navState !== NAV_STATE.PAUSED || !journeyPlan) return

    const previousState = pausedStateRef.current || NAV_STATE.DRIVING
    setNavState(previousState)

    const multiLegs = journeyPlan.multiStopLegs || []
    const totalDist = Number(journeyPlan.routeMetrics?.totalDistanceKm) || 5.0

    if (multiLegs.length > 0) {
      // Resume multi-stop from the leg we paused on
      const resumeLegIdx = currentLegIdxRef.current
      const priorDist = multiLegs
        .slice(0, resumeLegIdx)
        .reduce((acc, l) => acc + (l.distanceKm || 0), 0)

      if (previousState === NAV_STATE.CHARGING) {
        const stop = journeyPlan.chargingStops?.[resumeLegIdx - 1] || null
        runChargingSimulation(
          stop,
          () => runLegSequence(journeyPlan, resumeLegIdx, priorDist),
          true /* isResume */
        )
      } else {
        runLegSequence(journeyPlan, resumeLegIdx, priorDist)
      }
      return
    }

    // Legacy 2-leg resume
    const isCharge = journeyPlan.decisionType === 'CHARGE'
    if (previousState === NAV_STATE.DRIVING) {
      runDriveLeg({
        legType: LEG_TYPE.DIRECT,
        points: journeyPlan.legs?.direct?.polylinePoints || journeyPlan.fullPolylinePoints || [],
        legDistanceKm: journeyPlan.legs?.direct?.distanceKm || totalDist,
        legTravelTimeMin: journeyPlan.legs?.direct?.travelTimeMinutes || 10,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => handleDestinationArrival(journeyPlan),
      })
    } else if (previousState === NAV_STATE.DRIVING_LEG_1) {
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10
      const legacyStop = journeyPlan.selectedCharger
        ? { chargerName: journeyPlan.selectedCharger.name, energyAddedKwh: journeyPlan.energyAccounting?.energyAddedKwh || 10 }
        : null

      runDriveLeg({
        legType: LEG_TYPE.ORIGIN_TO_CHARGER,
        points: journeyPlan.legs?.originToCharger?.polylinePoints || [],
        legDistanceKm: leg1Dist,
        legTravelTimeMin: journeyPlan.legs?.originToCharger?.travelTimeMinutes || 10,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => {
          runChargingSimulation(legacyStop, () => {
            setNavState(NAV_STATE.DRIVING_LEG_2)
            setCurrentLeg(LEG_TYPE.CHARGER_TO_DESTINATION)
            legProgressRef.current = 0.0
            lastLegProgressRef.current = 0.0
            setLegProgressPercent(0)
            runDriveLeg({
              legType: LEG_TYPE.CHARGER_TO_DESTINATION,
              points: leg2Points,
              legDistanceKm: leg2Dist,
              legTravelTimeMin: leg2Time,
              totalTripDistanceKm: totalDist,
              priorDistanceTraveledKm: leg1Dist,
              onLegComplete: () => handleDestinationArrival(journeyPlan),
            })
          })
        },
      })
    } else if (previousState === NAV_STATE.CHARGING) {
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10
      const legacyStop = journeyPlan.selectedCharger
        ? { chargerName: journeyPlan.selectedCharger.name, energyAddedKwh: journeyPlan.energyAccounting?.energyAddedKwh || 10 }
        : null

      runChargingSimulation(
        legacyStop,
        () => {
          setNavState(NAV_STATE.DRIVING_LEG_2)
          setCurrentLeg(LEG_TYPE.CHARGER_TO_DESTINATION)
          legProgressRef.current = 0.0
          lastLegProgressRef.current = 0.0
          setLegProgressPercent(0)
          runDriveLeg({
            legType: LEG_TYPE.CHARGER_TO_DESTINATION,
            points: leg2Points,
            legDistanceKm: leg2Dist,
            legTravelTimeMin: leg2Time,
            totalTripDistanceKm: totalDist,
            priorDistanceTraveledKm: leg1Dist,
            onLegComplete: () => handleDestinationArrival(journeyPlan),
          })
        },
        true /* isResume */
      )
    } else if (previousState === NAV_STATE.DRIVING_LEG_2) {
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      runDriveLeg({
        legType: LEG_TYPE.CHARGER_TO_DESTINATION,
        points: journeyPlan.legs?.chargerToDestination?.polylinePoints || [],
        legDistanceKm: journeyPlan.legs?.chargerToDestination?.distanceKm || (totalDist * 0.5),
        legTravelTimeMin: journeyPlan.legs?.chargerToDestination?.travelTimeMinutes || 10,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: leg1Dist,
        onLegComplete: () => handleDestinationArrival(journeyPlan),
      })
    }
  }, [navState, journeyPlan, runLegSequence, runDriveLeg, runChargingSimulation, handleDestinationArrival])

  // ---------------------------------------------------------------------------
  // RESET
  // ---------------------------------------------------------------------------
  const resetSimulation = useCallback(() => {
    stopAllTimers()
    legProgressRef.current = 0.0
    lastLegProgressRef.current = 0.0
    tripStartTimeRef.current = null
    pausedStateRef.current = null
    chargeStepRef.current = 0
    currentLegIdxRef.current = 0

    setCurrentLegIndex(0)
    setActiveChargingStopIndex(0)
    setNavState(journeyPlan ? (journeyPlan.decisionType === 'INFEASIBLE' ? NAV_STATE.INFEASIBLE : NAV_STATE.READY) : NAV_STATE.IDLE)
    setCurrentLeg(journeyPlan ? (journeyPlan.decisionType === 'CHARGE' ? LEG_TYPE.ORIGIN_TO_CHARGER : LEG_TYPE.DIRECT) : null)
    setLegProgressPercent(0)
    setOverallTripProgressPercent(0)
    setSimulationProgress(0.0)
    setSimulatedChargingProgress(0)
    setChargingEnergyAddedSoFar(0.0)
    setTripSummary(null)

    tripRecorder.resetTrip()
    resetTelemetry()
  }, [journeyPlan, resetTelemetry])

  // ---------------------------------------------------------------------------
  // Destination Controls
  // ---------------------------------------------------------------------------
  const setDestination = useCallback((coords) => {
    if (!coords || typeof coords.latitude !== 'number' || typeof coords.longitude !== 'number') {
      return
    }
    stopAllTimers()
    legProgressRef.current = 0.0
    lastLegProgressRef.current = 0.0
    tripStartTimeRef.current = null
    pausedStateRef.current = null
    chargeStepRef.current = 0
    currentLegIdxRef.current = 0

    setJourneyPlan(null)
    setRouteData(null)

    setNavState(NAV_STATE.IDLE)
    setCurrentLeg(null)
    setCurrentLegIndex(0)
    setActiveChargingStopIndex(0)
    setLegProgressPercent(0)
    setOverallTripProgressPercent(0)
    setSimulationProgress(0.0)
    setSimulatedChargingProgress(0)
    setChargingEnergyAddedSoFar(0.0)
    setTripSummary(null)
    setPlanError(null)
    setIsPlanning(false)
    setJourneyPlan(null)
    setRouteData(null)

    setDestinationState({
      latitude: parseFloat(coords.latitude.toFixed(6)),
      longitude: parseFloat(coords.longitude.toFixed(6)),
      name: coords.name || coords.displayName || null,
      address: coords.address || coords.formattedAddress || null,
      placeId: coords.placeId || null,
    })
    setIsSelectingDestination(false)
  }, [])

  const clearDestination = useCallback(() => {
    stopAllTimers()
    legProgressRef.current = 0.0
    lastLegProgressRef.current = 0.0
    tripStartTimeRef.current = null
    pausedStateRef.current = null
    chargeStepRef.current = 0
    currentLegIdxRef.current = 0

    setNavState(NAV_STATE.IDLE)
    setCurrentLeg(null)
    setCurrentLegIndex(0)
    setActiveChargingStopIndex(0)
    setLegProgressPercent(0)
    setOverallTripProgressPercent(0)
    setSimulationProgress(0.0)
    setSimulatedChargingProgress(0)
    setChargingEnergyAddedSoFar(0.0)
    setTripSummary(null)

    setDestinationState(null)
    setIsSelectingDestination(false)
    setJourneyPlan(null)
    setPlanError(null)
    setIsPlanning(false)
    setRouteData(null)

    tripRecorder.resetTrip()
    updateTelemetry({
      speed_kmph: 0.0,
      vehicle_status: 'Standby (Parked)',
    })
  }, [updateTelemetry])

  const startSelectingDestination = useCallback(() => {
    setIsSelectingDestination(true)
  }, [])

  const cancelSelectingDestination = useCallback(() => {
    setIsSelectingDestination(false)
  }, [])

  // Backward compatibility alias for simulationState string
  const simulationState = useMemo(() => {
    if (navState === NAV_STATE.DRIVING || navState === NAV_STATE.DRIVING_LEG_1 || navState === NAV_STATE.DRIVING_LEG_2) {
      return 'driving'
    }
    if (navState === NAV_STATE.CHARGING) return 'charging'
    if (navState === NAV_STATE.PAUSED) return 'paused'
    if (navState === NAV_STATE.COMPLETED) return 'completed'
    if (navState === NAV_STATE.READY) return 'ready'
    return 'idle'
  }, [navState])

  // Active charging stop being simulated (for UI display during CHARGING state)
  const activeChargingStop = useMemo(() => {
    if (!journeyPlan?.chargingStops) return null
    return journeyPlan.chargingStops[activeChargingStopIndex] || null
  }, [journeyPlan, activeChargingStopIndex])

  const value = {
    trip,
    destination,
    isSelectingDestination,
    setIsSelectingDestination,
    startSelectingDestination,
    cancelSelectingDestination,
    setDestination,
    clearDestination,
    // Journey Planning State & Action
    journeyPlan,
    isPlanning,
    planError,
    planJourneyAction: executePlanJourney,
    // Change 23 / 28 Navigation State Machine & Multi-Leg Tracking
    navState,
    currentLeg,
    currentLegIndex,
    activeChargingStopIndex,
    activeChargingStop,
    legProgressPercent,
    overallTripProgressPercent,
    simulatedChargingProgress,
    chargingEnergyAddedSoFar,
    tripSummary,
    // Simulation controls & legacy compatibility
    simulationState,
    simulationProgress,
    startSimulation,
    pauseSimulation,
    resumeSimulation,
    resetSimulation,
    NAV_STATE,
    LEG_TYPE,
    TRIP_STATUS,
  }

  return <TripContext.Provider value={value}>{children}</TripContext.Provider>
}

/**
 * Hook to consume trip state, journey plans, and navigation controls from any component.
 */
export function useTrip() {
  const context = useContext(TripContext)
  if (!context) {
    throw new Error('useTrip must be used within a TripProvider')
  }
  return context
}

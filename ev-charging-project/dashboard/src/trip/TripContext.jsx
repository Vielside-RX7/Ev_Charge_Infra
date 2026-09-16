import React, { createContext, useContext, useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useTelemetry } from '../telemetry/TelemetryContext'
import { useVehicleProfile } from '../telemetry/VehicleProfileContext'
import { TRIP_STATUS, NAV_STATE, LEG_TYPE, createTripState } from './tripModel.js'
import { tripRecorder } from './tripRecorder.js'
import { planJourney } from './tripPlanningService.js'

const TripContext = createContext(null)

/**
 * TripProvider wraps the application and supplies active trip state, journey planning,
 * and the authoritative Change 23 Navigation State Machine.
 * 
 * Navigation State Machine:
 *   IDLE -> PLANNING -> READY -> DRIVING -> COMPLETED (Direct)
 *   IDLE -> PLANNING -> READY -> DRIVING_LEG_1 -> CHARGING -> DRIVING_LEG_2 -> COMPLETED (Charge)
 *   Pause / Resume allowed during DRIVING, DRIVING_LEG_1, DRIVING_LEG_2.
 *   Reset restores vehicle state, route progress, and stops all timers.
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

  // Explicit Change 23 Navigation State Machine
  const [navState, setNavState] = useState(NAV_STATE.IDLE)
  const [currentLeg, setCurrentLeg] = useState(null) // 'DIRECT' | 'ORIGIN_TO_CHARGER' | 'CHARGER_TO_DESTINATION' | null
  const [legProgressPercent, setLegProgressPercent] = useState(0) // 0..100
  const [overallTripProgressPercent, setOverallTripProgressPercent] = useState(0) // 0..100

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
  const pausedStateRef = useRef(null) // remembers whether paused from DRIVING, DRIVING_LEG_1, DRIVING_LEG_2, CHARGING
  const chargeStepRef = useRef(0) // tracks simulated charging step for clean pause/resume

  // Active telemetry reference to prevent stale closures and excessive hook re-triggers
  const telemetryRef = useRef(telemetry)
  useEffect(() => {
    telemetryRef.current = telemetry
  }, [telemetry])

  // Progress references across legs
  const legProgressRef = useRef(0.0) // 0.0 .. 1.0 within current active leg
  const lastLegProgressRef = useRef(0.0)
  const tripStartTimeRef = useRef(null)

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
          // Strictly avoid rendering misleading route data when journey is infeasible
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
          setCurrentLeg(plan.decisionType === 'CHARGE' ? LEG_TYPE.ORIGIN_TO_CHARGER : LEG_TYPE.DIRECT)
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

  // Automatically trigger /trip/plan when destination changes
  useEffect(() => {
    if (destination) {
      executePlanJourney()
    } else {
      setJourneyPlan(null)
      setPlanError(null)
      setIsPlanning(false)
      setRouteData(null)
      setNavState(NAV_STATE.IDLE)
      setCurrentLeg(null)
      setLegProgressPercent(0)
      setOverallTripProgressPercent(0)
    }
  }, [destination, executePlanJourney])

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
  // STEP 3, 4, 5: DRIVING LEG SIMULATOR
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

      // Duration: 5.0 seconds per leg for responsive interactive demo
      const TICK_MS = 100
      const LEG_DURATION_MS = 5000
      const STEP_PROGRESS = TICK_MS / LEG_DURATION_MS

      driveTimerRef.current = setInterval(() => {
        legProgressRef.current += STEP_PROGRESS
        const clampedLegP = Math.min(1.0, legProgressRef.current)

        // Incremental distance calculation for authoritative physics baseline
        const progressDelta = Math.max(0, clampedLegP - lastLegProgressRef.current)
        lastLegProgressRef.current = clampedLegP
        const incrementalDist = progressDelta * safeLegDist

        // Interpolate along road coordinates
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

        // Calculate overall trip progress
        const currentTotalTraveledKm = priorDistanceTraveledKm + clampedLegP * safeLegDist
        const overallP = totalTripDistanceKm > 0
          ? Math.min(1.0, currentTotalTraveledKm / totalTripDistanceKm)
          : clampedLegP

        setLegProgressPercent(Math.round(clampedLegP * 100))
        setOverallTripProgressPercent(Math.round(overallP * 100))
        setSimulationProgress(overallP)

        // Telemetry update
        updateTelemetry({
          latitude: parseFloat(currentLat.toFixed(6)),
          longitude: parseFloat(currentLon.toFixed(6)),
          speed_kmph: simulatedSpeed,
          vehicle_status: 'Driving',
          incremental_distance_km: incrementalDist,
        })

        // Record live sample for advisory LSTM analysis (read fresh telemetry via ref to avoid stale closures)
        const freshTelem = telemetryRef.current
        tripRecorder.recordSample({
          latitude: currentLat,
          longitude: currentLon,
          speedKmph: simulatedSpeed,
          currentSocPercent: freshTelem.current_soc_percent,
          energyConsumedKwh: freshTelem.energy_consumed_kwh,
        })

        // Leg Completion
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
  // STEP 7, 8: SIMULATED CHARGING
  // Adds planned charging energy over ~3.5 seconds, then resumes to Leg 2.
  // Supports deterministic pause and resume via chargeStepRef.
  // ---------------------------------------------------------------------------
  const runChargingSimulation = useCallback(
    (plan, onChargingComplete, isResume = false) => {
      stopAllTimers()
      setNavState(NAV_STATE.CHARGING)
      setCurrentLeg(LEG_TYPE.ORIGIN_TO_CHARGER)

      const chargerName = plan.selectedCharger?.name || 'Fast Charger'
      const energyToAdd = Number(plan.energyAccounting?.energyAddedKwh) || 10.0

      updateTelemetry({
        speed_kmph: 0.0,
        vehicle_status: `Charging at ${chargerName}`,
      })

      const TICK_MS = 100
      const CHARGE_DURATION_MS = 3500 // 3.5 seconds simulated charge demo
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

        // Add incremental charge to authoritative telemetry
        chargeTelemetry(energyPerStep, `Charging (${addedNow.toFixed(1)} / ${energyToAdd.toFixed(1)} kWh)`)

        if (step >= totalSteps) {
          stopAllTimers()
          chargeStepRef.current = 0
          updateTelemetry({
            speed_kmph: 0.0,
            vehicle_status: 'Charging Complete · Ready for Leg 2',
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
  // STEP 10: DESTINATION ARRIVAL
  // Stops vehicle, marks COMPLETED, logs final summary with live telemetry.
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
      const chargingEnergy = Number(plan.energyAccounting?.energyAddedKwh) || 0
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
        chargingEnergyAddedKwh: plan.decisionType === 'CHARGE' ? chargingEnergy : 0.0,
        tripDurationSeconds: finalDuration,
      })
    },
    [updateTelemetry]
  )

  // ---------------------------------------------------------------------------
  // START SIMULATION / NAVIGATION
  // Initiates navigation state machine according to authoritative decisionType.
  // ---------------------------------------------------------------------------
  const startSimulation = useCallback(() => {
    if (!journeyPlan || journeyPlan.decisionType === 'INFEASIBLE') return

    stopAllTimers()
    tripStartTimeRef.current = Date.now()
    setTripSummary(null)

    const isCharge = journeyPlan.decisionType === 'CHARGE'
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

    if (!isCharge) {
      // ---------------- DIRECT JOURNEY ----------------
      setNavState(NAV_STATE.DRIVING)
      setCurrentLeg(LEG_TYPE.DIRECT)
      legProgressRef.current = 0.0
      lastLegProgressRef.current = 0.0
      setLegProgressPercent(0)
      setOverallTripProgressPercent(0)
      setSimulationProgress(0.0)

      const directPoints = journeyPlan.legs?.direct?.polylinePoints || journeyPlan.fullPolylinePoints || []
      const legDist = Number(journeyPlan.legs?.direct?.distanceKm) || totalDist
      const legTime = Number(journeyPlan.legs?.direct?.travelTimeMinutes) || 10

      runDriveLeg({
        legType: LEG_TYPE.DIRECT,
        points: directPoints,
        legDistanceKm: legDist,
        legTravelTimeMin: legTime,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => {
          handleDestinationArrival(journeyPlan)
        },
      })
    } else {
      // ---------------- CHARGE JOURNEY ----------------
      // Leg 1: Origin -> Charger
      setNavState(NAV_STATE.DRIVING_LEG_1)
      setCurrentLeg(LEG_TYPE.ORIGIN_TO_CHARGER)
      legProgressRef.current = 0.0
      lastLegProgressRef.current = 0.0
      setLegProgressPercent(0)
      setOverallTripProgressPercent(0)
      setSimulationProgress(0.0)

      const leg1Points = journeyPlan.legs?.originToCharger?.polylinePoints || []
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      const leg1Time = Number(journeyPlan.legs?.originToCharger?.travelTimeMinutes) || 10

      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10

      runDriveLeg({
        legType: LEG_TYPE.ORIGIN_TO_CHARGER,
        points: leg1Points,
        legDistanceKm: leg1Dist,
        legTravelTimeMin: leg1Time,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => {
          // Reached Charger -> transition to CHARGING
          runChargingSimulation(journeyPlan, () => {
            // Charging Finished -> transition to DRIVING_LEG_2
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
              onLegComplete: () => {
                handleDestinationArrival(journeyPlan)
              },
            })
          })
        },
      })
    }
  }, [journeyPlan, vehicleProfile, runDriveLeg, runChargingSimulation, handleDestinationArrival])

  // ---------------------------------------------------------------------------
  // STEP 11: PAUSE / RESUME
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

    const isCharge = journeyPlan.decisionType === 'CHARGE'
    const totalDist = Number(journeyPlan.routeMetrics?.totalDistanceKm) || 5.0

    if (previousState === NAV_STATE.DRIVING) {
      const directPoints = journeyPlan.legs?.direct?.polylinePoints || journeyPlan.fullPolylinePoints || []
      const legDist = Number(journeyPlan.legs?.direct?.distanceKm) || totalDist
      const legTime = Number(journeyPlan.legs?.direct?.travelTimeMinutes) || 10

      runDriveLeg({
        legType: LEG_TYPE.DIRECT,
        points: directPoints,
        legDistanceKm: legDist,
        legTravelTimeMin: legTime,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => handleDestinationArrival(journeyPlan),
      })
    } else if (previousState === NAV_STATE.DRIVING_LEG_1) {
      const leg1Points = journeyPlan.legs?.originToCharger?.polylinePoints || []
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      const leg1Time = Number(journeyPlan.legs?.originToCharger?.travelTimeMinutes) || 10

      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10

      runDriveLeg({
        legType: LEG_TYPE.ORIGIN_TO_CHARGER,
        points: leg1Points,
        legDistanceKm: leg1Dist,
        legTravelTimeMin: leg1Time,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: 0,
        onLegComplete: () => {
          runChargingSimulation(journeyPlan, () => {
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
      // Seamlessly resume simulated charging from the captured step
      const leg1Dist = Number(journeyPlan.legs?.originToCharger?.distanceKm) || (totalDist * 0.5)
      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10

      runChargingSimulation(
        journeyPlan,
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
      const leg2Points = journeyPlan.legs?.chargerToDestination?.polylinePoints || []
      const leg2Dist = Number(journeyPlan.legs?.chargerToDestination?.distanceKm) || (totalDist * 0.5)
      const leg2Time = Number(journeyPlan.legs?.chargerToDestination?.travelTimeMinutes) || 10

      runDriveLeg({
        legType: LEG_TYPE.CHARGER_TO_DESTINATION,
        points: leg2Points,
        legDistanceKm: leg2Dist,
        legTravelTimeMin: leg2Time,
        totalTripDistanceKm: totalDist,
        priorDistanceTraveledKm: leg1Dist,
        onLegComplete: () => handleDestinationArrival(journeyPlan),
      })
    }
  }, [navState, journeyPlan, runDriveLeg, runChargingSimulation, handleDestinationArrival])

  // ---------------------------------------------------------------------------
  // STEP 12: RESET
  // Restores original Virtual EV state, resets all progress, stops intervals.
  // ---------------------------------------------------------------------------
  const resetSimulation = useCallback(() => {
    stopAllTimers()
    legProgressRef.current = 0.0
    lastLegProgressRef.current = 0.0
    tripStartTimeRef.current = null
    pausedStateRef.current = null
    chargeStepRef.current = 0

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

    // Immediately clear old plan and route to prevent stale Plan A leaking into Plan B
    setJourneyPlan(null)
    setRouteData(null)

    setNavState(NAV_STATE.PLANNING)
    setCurrentLeg(null)
    setLegProgressPercent(0)
    setOverallTripProgressPercent(0)
    setSimulationProgress(0.0)
    setSimulatedChargingProgress(0)
    setChargingEnergyAddedSoFar(0.0)
    setTripSummary(null)

    setDestinationState({
      latitude: parseFloat(coords.latitude.toFixed(6)),
      longitude: parseFloat(coords.longitude.toFixed(6)),
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

    setNavState(NAV_STATE.IDLE)
    setCurrentLeg(null)
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

    // Stop active drive, reset recorder, and return vehicle to parked standby
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
    if (navState === NAV_STATE.CHARGING) {
      return 'charging'
    }
    if (navState === NAV_STATE.PAUSED) {
      return 'paused'
    }
    if (navState === NAV_STATE.COMPLETED) {
      return 'completed'
    }
    if (navState === NAV_STATE.READY) {
      return 'ready'
    }
    return 'idle'
  }, [navState])

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
    // Change 23 Navigation State Machine & Multi-Leg Tracking
    navState,
    currentLeg,
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

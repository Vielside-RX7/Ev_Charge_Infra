import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export default function StationConsole({ defaultStationId = '7', onNavigateToDriver }) {
  const [stationId, setStationId] = useState(defaultStationId)
  const [telemetry, setTelemetry] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isActionLoading, setIsActionLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selectedFaultCode, setSelectedFaultCode] = useState('OVERCURRENT_TRIP')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastActionMessage, setLastActionMessage] = useState(null)

  const pollingRef = useRef(null)

  // Fetch live telemetry from provider abstraction
  const fetchTelemetry = async (silent = false) => {
    if (!silent) setIsLoading(true)
    try {
      const response = await axios.get(`${API_BASE_URL}/station/${stationId}/telemetry`)
      setTelemetry(response.data)
      setError(null)
    } catch (err) {
      console.error('Failed to fetch station telemetry:', err)
      setError('Unable to reach station telemetry backend. Please ensure FastAPI server is running.')
    } finally {
      if (!silent) setIsLoading(false)
    }
  }

  // Polling loop for real-time telemetry stream
  useEffect(() => {
    fetchTelemetry(false)

    if (autoRefresh) {
      pollingRef.current = setInterval(() => {
        fetchTelemetry(true)
      }, 1000)
    }

    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [stationId, autoRefresh])

  // Execute simulation state machine transition
  const handleSimulationAction = async (action, faultCode = null) => {
    setIsActionLoading(true)
    setLastActionMessage(null)
    try {
      const payload = {
        action,
        fault_code: faultCode || selectedFaultCode,
      }
      const response = await axios.post(`${API_BASE_URL}/station/${stationId}/simulate`, payload)
      setTelemetry(response.data)
      setLastActionMessage({
        type: 'success',
        text: `Executed action: ${action.replace('_', ' ')}`,
      })
    } catch (err) {
      console.error('Simulation action error:', err)
      setLastActionMessage({
        type: 'error',
        text: err.response?.data?.detail || 'Failed to execute station action',
      })
    } finally {
      setIsActionLoading(false)
    }
  }

  // Format seconds to mm:ss or hh:mm:ss
  const formatDuration = (totalSeconds) => {
    if (!totalSeconds || totalSeconds < 0) return '00:00'
    const hrs = Math.floor(totalSeconds / 3600)
    const mins = Math.floor((totalSeconds % 3600) / 60)
    const secs = Math.floor(totalSeconds % 60)
    if (hrs > 0) {
      return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
    }
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }

  // Format ISO timestamp
  const formatTime = (isoString) => {
    if (!isoString) return '—'
    try {
      const d = new Date(isoString)
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch {
      return '—'
    }
  }

  const isCharging = telemetry?.state === 'CHARGING'
  const isFaulted = telemetry?.fault || telemetry?.state === 'FAULTED' || telemetry?.state === 'OUT_OF_SERVICE'
  const isComplete = telemetry?.state === 'COMPLETE'
  const isAvailable = telemetry?.state === 'AVAILABLE' && !isFaulted

  return (
    <div className="min-h-screen bg-[#05070A] text-slate-100 font-sans selection:bg-cockpit-teal selection:text-black flex flex-col">
      {/* Top Industrial HMI Bar */}
      <header className="sticky top-0 z-40 seamless-glass border-b border-white/5 px-6 lg:px-12 py-3.5">
        <div className="max-w-[1680px] mx-auto flex flex-wrap items-center justify-between gap-4">
          {/* Brand & Console Identity */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm tracking-tight text-white">VoltGuide</span>
              <span className="text-slate-600">/</span>
              <span className="text-xs uppercase tracking-wider font-mono text-cockpit-teal font-medium">
                Station Console
              </span>
            </div>
            <span className="text-slate-600">·</span>
            <div className="flex items-center gap-2 bg-white/5 px-2.5 py-1 rounded-full border border-white/5 text-xs font-mono">
              <span className="text-slate-400">STATION #{telemetry?.station_id || stationId}</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-200 font-medium">{telemetry?.station_name || 'Grand Mercure Mysore'}</span>
            </div>
          </div>

          {/* Telemetry Status & Mode Badges */}
          <div className="flex items-center gap-4 text-xs font-mono">
            {/* Live Source Badge */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-900/80 border border-slate-700/50">
              <span className="text-slate-400">HARDWARE STATE:</span>
              <span
                className={`font-semibold tracking-wider ${
                  telemetry?.source === 'HARDWARE'
                    ? 'text-amber-400'
                    : 'text-indigo-400'
                }`}
              >
                {telemetry?.source === 'HARDWARE' ? 'REAL (ESP32)' : 'SIMULATED'}
              </span>
            </div>

            {/* Electrical Telemetry Quality Badge */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-900/80 border border-slate-700/50">
              <span className="text-slate-400">ELECTRICAL:</span>
              <span
                className={`font-semibold tracking-wider ${
                  telemetry?.telemetry_quality === 'MEASURED'
                    ? 'text-emerald-400'
                    : telemetry?.telemetry_quality === 'NOMINAL'
                    ? 'text-amber-400'
                    : 'text-indigo-400'
                }`}
              >
                {telemetry?.telemetry_quality || 'SIMULATED'}
              </span>
            </div>

            {/* Connection Status */}
            <div className="flex items-center gap-2 text-slate-300">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span>ONLINE</span>
            </div>

            {/* Auto Refresh Toggle */}
            <button
              type="button"
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`px-2 py-0.5 rounded text-[11px] border transition-colors ${
                autoRefresh
                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                  : 'bg-white/5 border-white/10 text-slate-400'
              }`}
            >
              {autoRefresh ? '1s LIVE STREAM' : 'PAUSED'}
            </button>

            {/* Return to Driver Journey */}
            <button
              type="button"
              onClick={onNavigateToDriver}
              className="btn-secondary text-xs px-3 py-1 ml-2 font-sans font-medium"
            >
              Driver Cockpit →
            </button>
          </div>
        </div>
      </header>

      {/* Main Console Grid */}
      <main className="max-w-[1680px] mx-auto px-6 lg:px-12 py-8 flex-1 w-full space-y-8">
        {error && (
          <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-sm flex items-center justify-between">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => fetchTelemetry(false)}
              className="text-xs underline hover:text-white"
            >
              Retry
            </button>
          </div>
        )}

        {lastActionMessage && (
          <div
            className={`p-3 rounded-xl border text-xs font-mono flex items-center justify-between ${
              lastActionMessage.type === 'success'
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'
                : 'bg-rose-500/10 border-rose-500/20 text-rose-300'
            }`}
          >
            <span>{lastActionMessage.text}</span>
            <button
              type="button"
              onClick={() => setLastActionMessage(null)}
              className="opacity-70 hover:opacity-100"
            >
              ✕
            </button>
          </div>
        )}

        {/* Top Status Banner: State Hierarchy & Safety Eligibility */}
        <section
          className={`p-6 rounded-2xl border transition-all duration-300 ${
            isCharging
              ? 'bg-teal-950/20 border-teal-500/30 shadow-[0_0_30px_-10px_rgba(0,210,180,0.15)]'
              : isFaulted
              ? 'bg-rose-950/20 border-rose-500/30 shadow-[0_0_30px_-10px_rgba(239,68,68,0.2)]'
              : isComplete
              ? 'bg-sky-950/20 border-sky-500/30 shadow-[0_0_30px_-10px_rgba(56,189,248,0.15)]'
              : 'seamless-surface border-white/5'
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-6">
            {/* Primary State Indicator */}
            <div className="space-y-1">
              <span className="label-subhead text-slate-400">Live Station State</span>
              <div className="flex items-center gap-3">
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full ${
                    isCharging
                      ? 'bg-teal-400 animate-pulse'
                      : isFaulted
                      ? 'bg-rose-500'
                      : isComplete
                      ? 'bg-sky-400'
                      : 'bg-emerald-400'
                  }`}
                />
                <h1 className="text-2xl sm:text-3xl font-light tracking-tight text-white font-mono">
                  {telemetry?.state || 'AVAILABLE'}
                </h1>
                {telemetry?.fault_code && (
                  <span className="px-2.5 py-0.5 rounded bg-rose-500/20 border border-rose-500/40 text-rose-300 text-xs font-mono">
                    {telemetry.fault_code}
                  </span>
                )}
              </div>
            </div>

            {/* Change 27 Authoritative Operational Safety Gate Status */}
            <div className="flex flex-wrap items-center gap-6 text-xs font-mono">
              <div className="space-y-0.5">
                <span className="text-slate-400 text-[11px] block">OPERATIONAL GATE</span>
                <span
                  className={`font-semibold ${
                    telemetry?.eligible_for_planning ? 'text-emerald-400' : 'text-rose-400'
                  }`}
                >
                  {telemetry?.eligible_for_planning ? 'ELIGIBLE · PASSED' : 'HARD-EXCLUDED · FAULT'}
                </span>
              </div>

              <div className="space-y-0.5">
                <span className="text-slate-400 text-[11px] block">GATE CONFIDENCE</span>
                <span className="text-slate-200">
                  {telemetry?.status_confidence ? `${(telemetry.status_confidence * 100).toFixed(0)}%` : '100%'}
                </span>
              </div>

              <div className="space-y-0.5">
                <span className="text-slate-400 text-[11px] block">TRUST SCORE</span>
                <span className="text-slate-200">
                  {telemetry?.trust_score ? (telemetry.trust_score * 100).toFixed(1) : '93.3'}%
                </span>
              </div>

              <div className="space-y-0.5">
                <span className="text-slate-400 text-[11px] block">LAST TELEMETRY UPDATE</span>
                <span className="text-slate-300 tabular-nums">
                  {formatTime(telemetry?.timestamp)}
                </span>
              </div>
            </div>
          </div>

          {telemetry?.rejection_reason && (
            <div className="mt-4 pt-3 border-t border-rose-500/20 text-xs text-rose-300 font-mono">
              ⚠️ Safety Exclusion Note: {telemetry.rejection_reason}
            </div>
          )}
        </section>

        {/* Middle Section: Electrical Telemetry & Session Gauges */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          {/* Electrical Telemetry (7 Columns) */}
          <div className="lg:col-span-7 space-y-6">
            <div className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-6">
              <div className="flex flex-wrap items-center justify-between border-b border-white/5 pb-3 gap-2">
                <div className="flex items-center gap-2.5">
                  <h2 className="text-sm uppercase tracking-wider font-mono text-slate-300 font-medium">
                    Electrical Telemetry
                  </h2>
                  {telemetry?.telemetry_quality === 'NOMINAL' ? (
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 font-medium">
                      Nominal / Estimated
                    </span>
                  ) : telemetry?.telemetry_quality === 'MEASURED' ? (
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-medium">
                      Measured · INA219 Live
                    </span>
                  ) : (
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 font-medium">
                      Simulated Stream
                    </span>
                  )}
                </div>
                <span className="text-[11px] font-mono text-slate-400">
                  {telemetry?.telemetry_quality === 'NOMINAL' ? 'Load Rating Baseline' : 'Bus Voltage & Shunt Current'}
                </span>
              </div>

              {/* 4 Core Electrical Metric Tiles */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {/* Voltage */}
                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="label-quiet">
                    {telemetry?.telemetry_quality === 'NOMINAL' ? 'NOMINAL VOLTAGE' : 'BUS VOLTAGE'}
                  </span>
                  <div className="text-2xl font-light font-mono text-white tabular-nums">
                    {telemetry?.voltage_v?.toFixed(1) || '0.0'}
                    <span className="text-xs text-slate-400 ml-1 font-sans">V</span>
                  </div>
                  <div className="w-full bg-slate-800/80 rounded-full h-1 mt-2 overflow-hidden">
                    <div
                      className="bg-teal-400 h-full rounded-full transition-all duration-300"
                      style={{ width: `${Math.min(100, ((telemetry?.voltage_v || 0) / 500) * 100)}%` }}
                    />
                  </div>
                </div>

                {/* Current */}
                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="label-quiet">
                    {telemetry?.telemetry_quality === 'NOMINAL' ? 'NOMINAL CURRENT' : 'CURRENT'}
                  </span>
                  <div className="text-2xl font-light font-mono text-white tabular-nums">
                    {telemetry?.current_a?.toFixed(1) || '0.0'}
                    <span className="text-xs text-slate-400 ml-1 font-sans">A</span>
                  </div>
                  <div className="w-full bg-slate-800/80 rounded-full h-1 mt-2 overflow-hidden">
                    <div
                      className="bg-teal-400 h-full rounded-full transition-all duration-300"
                      style={{ width: `${Math.min(100, ((telemetry?.current_a || 0) / 200) * 100)}%` }}
                    />
                  </div>
                </div>

                {/* Active Power */}
                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="label-quiet">
                    {telemetry?.telemetry_quality === 'NOMINAL' ? 'NOMINAL POWER' : 'ACTIVE POWER'}
                  </span>
                  <div className="text-2xl font-light font-mono text-white tabular-nums">
                    {telemetry?.power_kw ? telemetry.power_kw.toFixed(2) : ((telemetry?.power_w || 0) / 1000).toFixed(2)}
                    <span className="text-xs text-slate-400 ml-1 font-sans">kW</span>
                  </div>
                  <div className="w-full bg-slate-800/80 rounded-full h-1 mt-2 overflow-hidden">
                    <div
                      className="bg-teal-400 h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${Math.min(
                          100,
                          (((telemetry?.power_w || 0) / 1000) / (telemetry?.rated_power_kw || 120)) * 100
                        )}%`,
                      }}
                    />
                  </div>
                </div>

                {/* Energy Delivered */}
                <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="label-quiet">
                    {telemetry?.telemetry_quality === 'NOMINAL' ? 'ESTIMATED ENERGY' : 'ENERGY DELIVERED'}
                  </span>
                  <div className="text-2xl font-light font-mono text-white tabular-nums">
                    {telemetry?.energy_kwh
                      ? telemetry.energy_kwh.toFixed(3)
                      : ((telemetry?.energy_wh || 0) / 1000).toFixed(3)}
                    <span className="text-xs text-slate-400 ml-1 font-sans">kWh</span>
                  </div>
                  <div className="text-[10px] text-slate-400 font-mono">
                    {telemetry?.energy_wh?.toFixed(1) || '0.0'} Wh
                  </div>
                </div>
              </div>

              {/* Hardware Quality Callout when in NOMINAL mode */}
              {telemetry?.telemetry_quality === 'NOMINAL' && (
                <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs font-mono text-amber-200/90 flex items-start gap-2.5">
                  <span className="text-base leading-none">ℹ️</span>
                  <div className="space-y-0.5">
                    <span className="font-semibold text-amber-300 block">Demonstration Mode (No Physical INA219 Current Sensor):</span>
                    <p className="text-[11px] text-amber-200/80">
                      Physical station state, MOSFET load actuation, and fault button are REAL from hardware.
                      Electrical voltage/current metrics are NOMINAL estimates until physical sensor integration.
                    </p>
                  </div>
                </div>
              )}

              {/* Contactor Relay & Contactor State */}
              <div className="p-4 rounded-xl bg-slate-900/40 border border-white/5 flex flex-wrap items-center justify-between gap-4 text-xs font-mono">
                <div className="flex items-center gap-3">
                  <div
                    className={`h-2.5 w-2.5 rounded-full ${
                      telemetry?.relay_closed ? 'bg-teal-400 animate-ping' : 'bg-slate-600'
                    }`}
                  />
                  <div>
                    <span className="text-slate-400">LOAD ACTUATOR / MOSFET: </span>
                    <span
                      className={`font-semibold ${
                        telemetry?.relay_closed ? 'text-teal-400' : 'text-slate-300'
                      }`}
                    >
                      {telemetry?.relay_closed ? 'CLOSED (LOAD ENERGIZED)' : 'OPEN (LOAD ISOLATED)'}
                    </span>
                  </div>
                </div>

                <div className="text-slate-400">
                  Hardware Bus Interface: <span className="text-slate-200">ESP32 GPIO / MOSFET</span>
                </div>
              </div>
            </div>

            {/* Current Session Telemetry */}
            <div className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-4">
              <div className="flex items-center justify-between border-b border-white/5 pb-3">
                <h2 className="text-sm uppercase tracking-wider font-mono text-slate-300 font-medium">
                  Current Charging Session
                </h2>
                <span className="text-xs font-mono text-slate-400">
                  {telemetry?.session_id || 'NO ACTIVE SESSION'}
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs font-mono">
                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5">
                  <span className="text-slate-400 block text-[11px]">SESSION STATE</span>
                  <span className="text-white font-medium">
                    {isCharging ? 'IN PROGRESS' : isComplete ? 'COMPLETED' : 'IDLE / READY'}
                  </span>
                </div>

                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5">
                  <span className="text-slate-400 block text-[11px]">ELAPSED DURATION</span>
                  <span className="text-white font-medium text-sm tabular-nums">
                    {formatDuration(telemetry?.session_duration_seconds)}
                  </span>
                </div>

                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5">
                  <span className="text-slate-400 block text-[11px]">SESSION START</span>
                  <span className="text-slate-300">{formatTime(telemetry?.session_start_time)}</span>
                </div>

                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5">
                  <span className="text-slate-400 block text-[11px]">SESSION END</span>
                  <span className="text-slate-300">{formatTime(telemetry?.session_end_time)}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Charger Specifications & Hardware Diagnostics (5 Columns) */}
          <div className="lg:col-span-5 space-y-6">
            {/* Charger Info Card */}
            <div className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-4">
              <div className="border-b border-white/5 pb-3">
                <h2 className="text-sm uppercase tracking-wider font-mono text-slate-300 font-medium">
                  Station Hardware Specifications
                </h2>
              </div>

              <div className="space-y-3 text-xs">
                <div className="flex justify-between py-1.5 border-b border-white/[0.04]">
                  <span className="text-slate-400">Station Identity</span>
                  <span className="text-white font-mono font-medium">
                    #{telemetry?.station_id || stationId} · {telemetry?.station_name || 'Grand Mercure Mysore'}
                  </span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-white/[0.04]">
                  <span className="text-slate-400">Connector Standard</span>
                  <span className="text-white font-mono font-medium">
                    {telemetry?.connector_type || 'CCS (Type 2)'}
                  </span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-white/[0.04]">
                  <span className="text-slate-400">Rated Charging Power</span>
                  <span className="text-white font-mono font-medium">
                    {telemetry?.rated_power_kw || 120} kW High-Speed DC
                  </span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-white/[0.04]">
                  <span className="text-slate-400">Operator Network</span>
                  <span className="text-slate-300">{telemetry?.operator || 'Zeon Charging'}</span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-white/[0.04]">
                  <span className="text-slate-400">Location</span>
                  <span className="text-slate-300 text-right max-w-[200px] truncate">
                    {telemetry?.address || 'New Sayyaji Rao Road'}, {telemetry?.city || 'Mysuru'}
                  </span>
                </div>
                <div className="flex justify-between py-1.5">
                  <span className="text-slate-400">Predicted ML Reliability</span>
                  <span className="text-emerald-400 font-mono font-semibold">
                    {telemetry?.reliability ? `${(telemetry.reliability * 100).toFixed(1)}%` : '93.3%'}
                  </span>
                </div>
              </div>
            </div>

            {/* Hardware Health & Sensor Diagnostics */}
            <div className="p-6 rounded-2xl seamless-surface border border-white/5 space-y-4">
              <div className="border-b border-white/5 pb-3">
                <h2 className="text-sm uppercase tracking-wider font-mono text-slate-300 font-medium">
                  Hardware Health & Diagnostics
                </h2>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="text-slate-400 text-[11px]">CONTROLLER (ESP32)</span>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`h-2 w-2 rounded-full ${
                        telemetry?.controller_connected ? 'bg-emerald-400' : 'bg-rose-400'
                      }`}
                    />
                    <span className="text-slate-200">
                      {telemetry?.controller_connected ? 'CONNECTED (REAL)' : 'DISCONNECTED'}
                    </span>
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-white/[0.02] border border-white/5 space-y-1">
                  <span className="text-slate-400 text-[11px]">CURRENT SENSOR</span>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`h-2 w-2 rounded-full ${
                        telemetry?.telemetry_connected ? 'bg-emerald-400' : 'bg-amber-400'
                      }`}
                    />
                    <span className="text-slate-200">
                      {telemetry?.telemetry_connected ? 'INA219 ACTIVE' : 'NOMINAL MODE'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="text-[11px] text-slate-400 font-mono flex items-center justify-between pt-1">
                <span>Heartbeat: {formatTime(telemetry?.last_heartbeat)}</span>
                <span className="text-emerald-400">Link: Wi-Fi HTTP</span>
              </div>
            </div>
          </div>
        </div>


        {/* Bottom Section: Test / Simulation Controls (Dev Console) */}
        <section className="p-6 rounded-2xl seamless-glass border border-white/10 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-white/5 pb-4">
            <div>
              <h2 className="text-base font-light text-white tracking-tight flex items-center gap-2">
                <span className="text-cockpit-teal font-mono font-medium text-xs uppercase px-2 py-0.5 rounded bg-teal-500/10 border border-teal-500/20">
                  DEV CONSOLE
                </span>
                Hardware State Machine & Telemetry Simulation Controls
              </h2>
              <p className="text-xs text-slate-400 mt-1">
                Operates through the authoritative station provider abstraction. Injects simulated physical events
                prior to ESP32 hardware attachment.
              </p>
            </div>

            {/* Quick Fault Code Selector */}
            <div className="flex items-center gap-2 text-xs font-mono">
              <span className="text-slate-400">Fault Injection Type:</span>
              <select
                value={selectedFaultCode}
                onChange={(e) => setSelectedFaultCode(e.target.value)}
                className="bg-slate-900 border border-white/10 rounded-lg px-2.5 py-1 text-slate-200 text-xs focus:outline-none focus:border-teal-400"
              >
                <option value="OVERCURRENT_TRIP">Overcurrent Trip (135A Limit)</option>
                <option value="INA219_COMM_LOSS">INA219 I2C Comm Loss</option>
                <option value="RELAY_WELD_DETECTED">Relay Contact Weld Detected</option>
                <option value="EMERGENCY_STOP_PRESSED">Emergency Stop Actuated</option>
                <option value="UNDERVOLTAGE_LOCKOUT">Undervoltage Lockout (&lt;320V)</option>
              </select>
            </div>
          </div>

          {/* Action Control Buttons */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <button
              type="button"
              disabled={isActionLoading || isCharging}
              onClick={() => handleSimulationAction('START_CHARGING')}
              className={`p-3 rounded-xl border text-xs font-mono font-semibold transition-all ${
                isCharging
                  ? 'bg-slate-800/40 border-slate-700/40 text-slate-500 cursor-not-allowed'
                  : 'bg-teal-500/10 border-teal-500/30 text-teal-300 hover:bg-teal-500/20 hover:border-teal-400 active:scale-[0.98]'
              }`}
            >
              ▶ START CHARGING
            </button>

            <button
              type="button"
              disabled={isActionLoading || !isCharging}
              onClick={() => handleSimulationAction('STOP_CHARGING')}
              className={`p-3 rounded-xl border text-xs font-mono font-semibold transition-all ${
                !isCharging
                  ? 'bg-slate-800/40 border-slate-700/40 text-slate-500 cursor-not-allowed'
                  : 'bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20 hover:border-amber-400 active:scale-[0.98]'
              }`}
            >
              ⏹ STOP CHARGING
            </button>

            <button
              type="button"
              disabled={isActionLoading || !isCharging}
              onClick={() => handleSimulationAction('COMPLETE_SESSION')}
              className={`p-3 rounded-xl border text-xs font-mono font-semibold transition-all ${
                !isCharging
                  ? 'bg-slate-800/40 border-slate-700/40 text-slate-500 cursor-not-allowed'
                  : 'bg-sky-500/10 border-sky-500/30 text-sky-300 hover:bg-sky-500/20 hover:border-sky-400 active:scale-[0.98]'
              }`}
            >
              ✓ COMPLETE SESSION
            </button>

            <button
              type="button"
              disabled={isActionLoading || isFaulted}
              onClick={() => handleSimulationAction('SIMULATE_FAULT', selectedFaultCode)}
              className={`p-3 rounded-xl border text-xs font-mono font-semibold transition-all ${
                isFaulted
                  ? 'bg-slate-800/40 border-slate-700/40 text-slate-500 cursor-not-allowed'
                  : 'bg-rose-500/10 border-rose-500/30 text-rose-300 hover:bg-rose-500/20 hover:border-rose-400 active:scale-[0.98]'
              }`}
            >
              ⚠ SIMULATE FAULT
            </button>

            <button
              type="button"
              disabled={isActionLoading || (!isFaulted && !isComplete)}
              onClick={() => handleSimulationAction('RESTORE_AVAILABLE')}
              className={`p-3 rounded-xl border text-xs font-mono font-semibold transition-all ${
                !isFaulted && !isComplete
                  ? 'bg-slate-800/40 border-slate-700/40 text-slate-500 cursor-not-allowed'
                  : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20 hover:border-emerald-400 active:scale-[0.98]'
              }`}
            >
              ↺ RESTORE AVAILABLE
            </button>
          </div>
        </section>
      </main>

      {/* Industrial Footer */}
      <footer className="py-4 px-6 lg:px-12 border-t border-white/5 text-center label-quiet font-mono text-[11px]">
        VoltGuide Infrastructure Node · Station ID #7 · INA219 Telemetry Interface · Authoritative Safety Gate (Change 27)
      </footer>
    </div>
  )
}

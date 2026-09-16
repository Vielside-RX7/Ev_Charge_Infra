import React, { useState } from 'react'
import { useTelemetry } from '../telemetry/TelemetryContext'
import { useVehicleProfile } from '../telemetry/VehicleProfileContext'

export default function VirtualEvTelemetry() {
  const { telemetry, isConnected } = useTelemetry()
  const { vehicleProfile } = useVehicleProfile()
  const [showDiagnostics, setShowDiagnostics] = useState(false)

  const soc = Number(telemetry.current_soc_percent) || 0
  const capacity = Number(telemetry.battery_capacity_kwh) || Number(vehicleProfile?.battery_capacity_kwh) || 30.0
  const remainingKwh = (soc / 100) * capacity
  const range = Number(telemetry.estimated_range_km) || 0
  const speed = Number(telemetry.speed_kmph) || 0
  const status = telemetry.vehicle_status || 'Standby'

  // Dynamic subtle battery tint
  const batteryColor = soc > 20 ? 'text-white' : soc > 10 ? 'text-amber-300' : 'text-rose-400'
  const barColor = soc > 20 ? 'bg-white/80' : soc > 10 ? 'bg-amber-400' : 'bg-rose-500'

  return (
    <section className="pt-2 pb-6 space-y-6">
      {/* Discreet Vehicle Meta */}
      <div className="flex items-center justify-between text-xs text-slate-400">
        <div className="flex items-center gap-2">
          <span className="text-slate-200 font-medium tracking-tight">
            {vehicleProfile?.vehicle_name || 'Virtual EV'}
          </span>
          <span className="text-slate-600">·</span>
          <span className="label-quiet">{vehicleProfile?.connector_type || 'CCS2'}</span>
          <span className="text-slate-600">·</span>
          <span className="label-quiet font-mono">{capacity} kWh</span>
        </div>

        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-cockpit-teal' : 'bg-slate-600'}`}></span>
          <span className="label-quiet">{isConnected ? 'Virtual OBD Live' : 'Offline'}</span>
          <button
            type="button"
            onClick={() => setShowDiagnostics(!showDiagnostics)}
            className="ml-2 text-slate-400 hover:text-white transition cursor-pointer text-[10px]"
            title="Toggle Vehicle Diagnostics"
          >
            {showDiagnostics ? 'Hide details' : 'Diagnostics'}
          </button>
        </div>
      </div>

      {/* Hero Display Metrics: Battery & Estimated Range */}
      <div className="grid grid-cols-2 gap-8 items-end">
        {/* Battery Block */}
        <div>
          <span className="label-subhead block mb-2">Battery State</span>
          <div className="flex items-baseline gap-1.5">
            <span className={`display-huge tabular-nums ${batteryColor}`}>
              {soc.toFixed(0)}
            </span>
            <span className="text-2xl sm:text-3xl font-light text-slate-400">%</span>
          </div>
          <p className="label-quiet mt-2 font-mono tabular-nums">
            {remainingKwh.toFixed(1)} kWh remaining
          </p>

          {/* Minimal Ultra-Thin Progress Line */}
          <div className="w-full bg-white/5 rounded-full h-[3px] mt-3 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ease-out ${barColor}`}
              style={{ width: `${Math.min(100, Math.max(0, soc))}%` }}
            ></div>
          </div>
        </div>

        {/* Range & Motion Block */}
        <div>
          <span className="label-subhead block mb-2">Estimated Range</span>
          <div className="flex items-baseline gap-1.5">
            <span className="display-huge tabular-nums text-white">
              {range.toFixed(0)}
            </span>
            <span className="text-2xl sm:text-3xl font-light text-slate-400">km</span>
          </div>
          <div className="flex items-center gap-2 mt-2 label-quiet">
            <span className="font-mono tabular-nums text-slate-200">{speed.toFixed(0)} km/h</span>
            <span className="text-slate-600">·</span>
            <span className={status.toLowerCase() === 'driving' ? 'text-cockpit-teal' : 'text-slate-400'}>
              {status}
            </span>
            <span className="text-slate-600">·</span>
            <span className="font-mono text-slate-400">0.15 kWh/km</span>
          </div>

          <div className="w-full bg-white/5 rounded-full h-[3px] mt-3 overflow-hidden">
            <div
              className="h-full rounded-full bg-cockpit-teal/80 transition-all duration-500"
              style={{ width: `${Math.min(100, (range / 200) * 100)}%` }}
            ></div>
          </div>
        </div>
      </div>

      {/* Progressive Disclosure: Deep OBD Diagnostics */}
      {showDiagnostics && (
        <div className="pt-3 pb-1 border-t border-white/5 grid grid-cols-3 gap-4 text-xs font-mono tabular-nums text-slate-400 transition-all">
          <div>
            <span className="label-quiet block text-[10px]">Odometer</span>
            <span className="text-slate-200 text-xs">{Number(telemetry.odometer_km || 0).toFixed(1)} km</span>
          </div>
          <div>
            <span className="label-quiet block text-[10px]">Coordinates</span>
            <span className="text-slate-200 text-xs">
              {Number(telemetry.latitude).toFixed(3)}°, {Number(telemetry.longitude).toFixed(3)}°
            </span>
          </div>
          <div>
            <span className="label-quiet block text-[10px]">Consumed</span>
            <span className="text-slate-200 text-xs">{Number(telemetry.energy_consumed_kwh || 0).toFixed(2)} kWh</span>
          </div>
        </div>
      )}
    </section>
  )
}

import React, { useEffect } from 'react'

export default function ResultsList({
  results,
  isLoading,
  error,
  selectedChargerId,
  onSelectCharger,
  onStartNavigation,
}) {
  useEffect(() => {
    if (selectedChargerId) {
      const cardEl = document.getElementById(`charger-card-${selectedChargerId}`)
      if (cardEl) {
        cardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }
    }
  }, [selectedChargerId])

  if (isLoading) {
    return (
      <div className="py-8 text-center text-xs text-slate-400">
        <div className="animate-spin rounded-full h-5 w-5 border-2 border-cockpit-teal border-t-transparent mx-auto mb-2"></div>
        <p className="label-quiet">Evaluating optimal charging stations across the region...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="py-4 text-xs text-amber-300">
        <div className="flex items-center gap-2 font-medium mb-1">
          <span>⚠️</span> {error.title || 'Error evaluating stations'}
        </div>
        <p className="label-quiet text-slate-400">{error.message}</p>
      </div>
    )
  }

  if (!results || results.length === 0) {
    return null
  }

  const optimalStop = results[0]
  const otherStops = results.slice(1)

  return (
    <div className="space-y-6 pt-4 border-t border-white/5">
      {/* Primary Recommended Station Presentation */}
      {optimalStop && (
        <div
          id={`charger-card-${optimalStop.charger_id}`}
          onClick={() => onSelectCharger && onSelectCharger(optimalStop.charger_id)}
          className="cursor-pointer group space-y-3"
        >
          <div className="flex items-center justify-between">
            <span className="label-subhead text-amber-400 flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
              Recommended Station
            </span>
            <span className="label-quiet font-mono">
              Score {optimalStop.final_score.toFixed(3)}
            </span>
          </div>

          <div>
            <h3 className="display-metric text-white font-light group-hover:text-amber-200 transition">
              {optimalStop.name}
            </h3>
            <p className="label-quiet text-slate-400 mt-0.5">
              {optimalStop.operator ? `${optimalStop.operator} · ` : ''}
              {optimalStop.address || optimalStop.city || 'Mysuru'}
            </p>
          </div>

          {/* Clean Metric Row */}
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm font-light text-slate-200 font-mono tabular-nums pt-1">
            <span>
              <strong className="font-medium text-white">{optimalStop.charging_power_kw} kW</strong>
              <span className="label-quiet ml-1">power</span>
            </span>
            <span className="text-slate-600">·</span>
            <span>
              <strong className="font-medium text-white">{optimalStop.distance_km} km</strong>
              <span className="label-quiet ml-1">away</span>
            </span>
            <span className="text-slate-600">·</span>
            <span>
              <strong className="font-medium text-white">{optimalStop.estimated_charging_time_minutes} min</strong>
              <span className="label-quiet ml-1">duration</span>
            </span>
            <span className="text-slate-600">·</span>
            <span>
              <strong className="font-medium text-white">{(optimalStop.reliability * 100).toFixed(0)}%</strong>
              <span className="label-quiet ml-1">reliability</span>
            </span>
          </div>

          <div className="pt-2">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                if (onStartNavigation) onStartNavigation(optimalStop)
              }}
              className="btn-primary px-7 py-2.5 text-xs flex items-center gap-2 cursor-pointer shadow-sm"
            >
              <span>Navigate to {optimalStop.name.split(' ')[0]}</span>
              <span>→</span>
            </button>
          </div>
        </div>
      )}

      {/* Alternative Ranked Candidates */}
      {otherStops.length > 0 && (
        <div className="pt-4 border-t border-white/5 space-y-3">
          <span className="label-subhead block">
            Alternative Stations ({otherStops.length})
          </span>

          <div className="divide-y divide-white/5">
            {otherStops.map((charger, idx) => {
              const rank = idx + 2
              const isSelected = selectedChargerId === charger.charger_id

              return (
                <div
                  key={charger.charger_id || idx}
                  id={`charger-card-${charger.charger_id}`}
                  onClick={() => onSelectCharger && onSelectCharger(charger.charger_id)}
                  className={`py-3 flex items-center justify-between gap-4 cursor-pointer transition ${
                    isSelected ? 'opacity-100' : 'opacity-70 hover:opacity-100'
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-slate-400 text-xs font-mono">#{rank}</span>
                      <h4 className="text-sm font-normal text-white truncate">{charger.name}</h4>
                    </div>
                    <p className="label-quiet font-mono tabular-nums mt-0.5">
                      {charger.distance_km} km · {charger.charging_power_kw} kW · {charger.estimated_charging_time_minutes} min
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (onStartNavigation) onStartNavigation(charger)
                    }}
                    className="text-xs text-cockpit-teal hover:underline cursor-pointer shrink-0"
                  >
                    Select →
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

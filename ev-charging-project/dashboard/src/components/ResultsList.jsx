import React, { useEffect } from 'react'

export default function ResultsList({
  results,
  isLoading,
  error,
  selectedChargerId,
  onSelectCharger,
}) {
  // Smooth scroll into view when selected from MapView
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
      <div className="bg-white rounded-xl shadow-md p-8 border border-gray-100 flex flex-col items-center justify-center min-h-[300px]">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
        <p className="text-gray-600 font-medium text-sm">Evaluating charging stations across Mysore...</p>
        <p className="text-gray-400 text-xs mt-1">Running reliability forecasting and occupancy models</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-red-700">
        <div className="flex items-center gap-2 font-bold text-lg mb-2">
          <span>⚠️</span> {error.title || 'Error Loading Recommendations'}
        </div>
        <p className="text-sm">{error.message}</p>
        {error.suggestion && (
          <p className="text-xs text-red-600 mt-2 font-medium bg-red-100/60 p-2 rounded">
            💡 Tip: {error.suggestion}
          </p>
        )}
      </div>
    )
  }

  if (!results || results.length === 0) {
    return (
      <div className="bg-white rounded-xl shadow-md p-8 border border-gray-100 text-center text-gray-500 min-h-[300px] flex flex-col items-center justify-center">
        <span className="text-4xl mb-3">🔌</span>
        <h3 className="text-lg font-semibold text-gray-700">No Recommendations Yet</h3>
        <p className="text-sm text-gray-400 max-w-sm mt-1">
          Enter your current location, vehicle state-of-charge, and search radius, then click "Find Recommended Chargers".
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
          <span>🏆</span> Ranked Recommendations
        </h2>
        <span className="text-xs bg-blue-100 text-blue-800 font-semibold px-2.5 py-1 rounded-full">
          {results.length} Stations Found
        </span>
      </div>

      <div className="space-y-3">
        {results.map((charger, index) => {
          const rank = index + 1
          const reliabilityPct = (charger.reliability * 100).toFixed(1)
          const availabilityPct = (charger.probability_available * 100).toFixed(1)
          const isSelected = selectedChargerId === charger.charger_id

          // Badge coloring based on rank
          const isTopRank = rank === 1
          const rankBadgeColor = isTopRank
            ? 'bg-amber-500 text-white shadow-sm'
            : rank === 2
            ? 'bg-slate-400 text-white'
            : rank === 3
            ? 'bg-amber-700 text-white'
            : 'bg-gray-200 text-gray-700'

          return (
            <div
              id={`charger-card-${charger.charger_id}`}
              key={charger.charger_id || index}
              onClick={() => onSelectCharger && onSelectCharger(charger.charger_id)}
              className={`bg-white rounded-xl shadow-sm border p-5 transition-all cursor-pointer hover:shadow-md ${
                isSelected
                  ? 'border-blue-500 ring-2 ring-blue-400 bg-blue-50/30'
                  : isTopRank
                  ? 'border-amber-300 ring-1 ring-amber-200'
                  : 'border-gray-200'
              }`}
            >
              {/* Header: Rank + Title + Power */}
              <div className="flex items-start justify-between gap-3 mb-2">
                <div className="flex items-start gap-3">
                  <span className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm shrink-0 ${rankBadgeColor}`}>
                    #{rank}
                  </span>
                  <div>
                    <h3 className="font-bold text-gray-900 text-base leading-snug">
                      {charger.name}
                    </h3>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {charger.operator ? `${charger.operator} • ` : ''}
                      {charger.address ? `${charger.address}, ` : ''}
                      {charger.city || 'Mysuru'}
                    </p>
                  </div>
                </div>

                <div className="text-right shrink-0">
                  <span className="inline-block bg-blue-50 text-blue-700 text-xs font-bold px-2.5 py-1 rounded-md border border-blue-100">
                    {charger.charging_power_kw} kW
                  </span>
                  <div className="text-[11px] text-gray-400 mt-0.5">
                    Score: <span className="font-semibold text-gray-600">{charger.final_score.toFixed(4)}</span>
                  </div>
                </div>
              </div>

              {/* Compatibility warning if false */}
              {!charger.compatible && (
                <div className="mb-3 bg-amber-50 border border-amber-200 text-amber-800 text-xs px-3 py-1.5 rounded-lg flex items-center gap-1.5">
                  <span>⚠️</span>
                  <span><strong>Fallback Station:</strong> Connector standard may not strictly match requested plug.</span>
                </div>
              )}

              {/* Metric Highlights Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3 pt-3 border-t border-gray-100 text-xs">
                <div className="bg-gray-50 p-2 rounded-lg">
                  <span className="text-gray-400 block text-[10px] uppercase font-semibold">Distance</span>
                  <span className="font-bold text-gray-800 text-sm">{charger.distance_km} km</span>
                </div>

                <div className="bg-emerald-50 p-2 rounded-lg">
                  <span className="text-emerald-600 block text-[10px] uppercase font-semibold">Reliability</span>
                  <span className="font-bold text-emerald-800 text-sm">{reliabilityPct}%</span>
                </div>

                <div className="bg-blue-50 p-2 rounded-lg">
                  <span className="text-blue-600 block text-[10px] uppercase font-semibold">Availability</span>
                  <span className="font-bold text-blue-800 text-sm">{availabilityPct}%</span>
                </div>

                <div className="bg-indigo-50 p-2 rounded-lg">
                  <span className="text-indigo-600 block text-[10px] uppercase font-semibold">Est. Duration</span>
                  <span className="font-bold text-indigo-800 text-sm">{charger.estimated_charging_time_minutes} min</span>
                </div>
              </div>

              {/* Secondary Details Footer */}
              <div className="flex items-center justify-between text-[11px] text-gray-500 mt-2.5 pt-2 border-t border-dashed border-gray-100">
                <div className="flex items-center gap-2">
                  <span className="bg-gray-100 px-2 py-0.5 rounded text-gray-600 font-medium">
                    🔌 {charger.connector_type}
                  </span>
                  <span>{charger.num_ports} {charger.num_ports === 1 ? 'Port' : 'Ports'}</span>
                </div>
                <div className="font-semibold text-gray-700">
                  Est. Cost: <span className="text-gray-900 font-bold">₹{charger.estimated_cost_inr.toFixed(2)}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

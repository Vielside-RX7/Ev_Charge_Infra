import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { useAuth } from '../auth/AuthContext'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const VEHICLE_PRESETS = [
  { name: 'Tata Nexon EV Max', capacity: 30.0, connector: 'CCS2', consumption: 0.15 },
  { name: 'MG ZS EV Long Range', capacity: 50.3, connector: 'CCS2', consumption: 0.15 },
  { name: 'Tata Tiago EV', capacity: 24.0, connector: 'CCS2', consumption: 0.14 },
  { name: 'Hyundai Ioniq 5', capacity: 72.6, connector: 'CCS2', consumption: 0.16 },
  { name: 'BYD Atto 3', capacity: 60.48, connector: 'CCS2', consumption: 0.155 },
  { name: 'Mahindra XUV400', capacity: 39.4, connector: 'CCS2', consumption: 0.15 },
  { name: 'Custom EV Configuration', capacity: 40.0, connector: 'CCS2', consumption: 0.15 },
]

export default function UserAccountModal() {
  const {
    isAccountModalOpen,
    setIsAccountModalOpen,
    user,
    vehicles,
    selectedVehicle,
    selectVehicle,
    addVehicle,
    deleteVehicle,
    logout,
  } = useAuth()

  const [activeTab, setActiveTab] = useState('vehicles') // 'vehicles' | 'history' | 'account'
  const [tripHistory, setTripHistory] = useState([])
  const [isHistoryLoading, setIsHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState(null)

  // Add Vehicle Form State
  const [isAddingVehicle, setIsAddingVehicle] = useState(false)
  const [newVehicleName, setNewVehicleName] = useState('MG ZS EV Long Range')
  const [newBatteryCapacity, setNewBatteryCapacity] = useState(50.3)
  const [newConnectorType, setNewConnectorType] = useState('CCS2')
  const [actionLoading, setActionLoading] = useState(false)

  // Fetch trip history when tab changes to history
  useEffect(() => {
    if (isAccountModalOpen && activeTab === 'history') {
      fetchHistory()
    }
  }, [isAccountModalOpen, activeTab])

  const fetchHistory = async () => {
    setIsHistoryLoading(true)
    setHistoryError(null)
    try {
      const res = await axios.get(`${API_BASE_URL}/trips/history`)
      setTripHistory(res.data || [])
    } catch (err) {
      console.error('Failed to fetch trip history:', err)
      setHistoryError('Unable to load trip history.')
    } finally {
      setIsHistoryLoading(false)
    }
  }

  if (!isAccountModalOpen) return null

  const handleSelectPreset = (e) => {
    const p = VEHICLE_PRESETS.find((item) => item.name === e.target.value)
    if (p) {
      setNewVehicleName(p.name)
      setNewBatteryCapacity(p.capacity)
      setNewConnectorType(p.connector)
    }
  }

  const handleAddVehicleSubmit = async (e) => {
    e.preventDefault()
    setActionLoading(true)
    await addVehicle({
      vehicle_name: newVehicleName.trim(),
      battery_capacity_kwh: Number(newBatteryCapacity),
      connector_type: newConnectorType.trim(),
      energy_consumption_kwh_per_km: 0.15,
      is_selected: true,
    })
    setActionLoading(false)
    setIsAddingVehicle(false)
  }

  const handleSelectVehicle = async (vId) => {
    setActionLoading(true)
    await selectVehicle(vId)
    setActionLoading(false)
  }

  const handleDeleteVehicle = async (vId) => {
    if (confirm('Are you sure you want to remove this vehicle profile?')) {
      setActionLoading(true)
      await deleteVehicle(vId)
      setActionLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-2xl bg-[#0D121D] border border-white/10 rounded-2xl shadow-2xl p-6 text-slate-100 flex flex-col max-h-[85vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 pb-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-cockpit-teal/20 border border-cockpit-teal/40 flex items-center justify-center text-cockpit-teal font-medium text-sm">
              {user?.name ? user.name[0].toUpperCase() : 'U'}
            </div>
            <div>
              <h2 className="text-sm font-medium text-white tracking-tight flex items-center gap-2">
                <span>{user?.name || 'Driver Profile'}</span>
                <span className="px-2 py-0.5 rounded-full bg-cockpit-teal/10 text-cockpit-teal text-[10px] font-mono">
                  Active Driver
                </span>
              </h2>
              <p className="label-quiet text-xs">{user?.email || 'Logged in'}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setIsAccountModalOpen(false)}
            className="text-slate-400 hover:text-white transition p-1.5 rounded-lg hover:bg-white/5"
          >
            ✕
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex rounded-xl bg-white/5 p-1 text-xs mt-4 shrink-0">
          <button
            type="button"
            onClick={() => setActiveTab('vehicles')}
            className={`flex-1 py-1.5 rounded-lg font-medium transition flex items-center justify-center gap-1.5 ${
              activeTab === 'vehicles' ? 'bg-white/10 text-white shadow-sm' : 'text-slate-400 hover:text-white'
            }`}
          >
            <span>🚗</span>
            <span>Vehicle Profiles ({vehicles.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('history')}
            className={`flex-1 py-1.5 rounded-lg font-medium transition flex items-center justify-center gap-1.5 ${
              activeTab === 'history' ? 'bg-white/10 text-white shadow-sm' : 'text-slate-400 hover:text-white'
            }`}
          >
            <span>🗺️</span>
            <span>Trip History</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('account')}
            className={`flex-1 py-1.5 rounded-lg font-medium transition flex items-center justify-center gap-1.5 ${
              activeTab === 'account' ? 'bg-white/10 text-white shadow-sm' : 'text-slate-400 hover:text-white'
            }`}
          >
            <span>⚙️</span>
            <span>Account</span>
          </button>
        </div>

        {/* Tab Content Area (Scrollable) */}
        <div className="py-4 overflow-y-auto space-y-4 flex-1 pr-1 custom-scrollbar">
          {/* TAB 1: VEHICLES */}
          {activeTab === 'vehicles' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <span className="label-subhead text-white">Your Registered EVs</span>
                {!isAddingVehicle && (
                  <button
                    type="button"
                    onClick={() => setIsAddingVehicle(true)}
                    className="btn-secondary px-3 py-1 text-xs text-cockpit-teal border-cockpit-teal/30 hover:bg-cockpit-teal/10 transition flex items-center gap-1"
                  >
                    <span>+</span>
                    <span>Add EV Profile</span>
                  </button>
                )}
              </div>

              {/* Add Vehicle Inline Drawer */}
              {isAddingVehicle && (
                <form
                  onSubmit={handleAddVehicleSubmit}
                  className="p-4 rounded-xl bg-white/[0.03] border border-cockpit-teal/30 space-y-3 animate-fade-in"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-cockpit-teal">New Vehicle Configuration</span>
                    <button
                      type="button"
                      onClick={() => setIsAddingVehicle(false)}
                      className="text-slate-400 hover:text-white text-xs"
                    >
                      Cancel
                    </button>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div>
                      <label className="label-quiet block mb-1">Preset Model</label>
                      <select
                        onChange={handleSelectPreset}
                        className="w-full bg-[#121927] border border-white/10 rounded-xl px-2.5 py-1.5 text-xs text-white"
                      >
                        {VEHICLE_PRESETS.map((p) => (
                          <option key={p.name} value={p.name}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="label-quiet block mb-1">Vehicle Name</label>
                      <input
                        type="text"
                        required
                        value={newVehicleName}
                        onChange={(e) => setNewVehicleName(e.target.value)}
                        className="w-full bg-white/5 border border-white/10 rounded-xl px-2.5 py-1.5 text-xs text-white"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="label-quiet block mb-1">Battery (kWh)</label>
                        <input
                          type="number"
                          step="0.1"
                          min="5"
                          max="200"
                          required
                          value={newBatteryCapacity}
                          onChange={(e) => setNewBatteryCapacity(parseFloat(e.target.value))}
                          className="w-full bg-white/5 border border-white/10 rounded-xl px-2 py-1.5 text-xs text-white font-mono"
                        />
                      </div>
                      <div>
                        <label className="label-quiet block mb-1">Connector</label>
                        <input
                          type="text"
                          required
                          value={newConnectorType}
                          onChange={(e) => setNewConnectorType(e.target.value)}
                          className="w-full bg-white/5 border border-white/10 rounded-xl px-2 py-1.5 text-xs text-white font-mono"
                        />
                      </div>
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={actionLoading}
                    className="btn-primary w-full py-2 text-xs font-medium"
                  >
                    {actionLoading ? 'Saving...' : 'Save & Select Vehicle'}
                  </button>
                </form>
              )}

              {/* Vehicle Cards List */}
              <div className="space-y-2.5">
                {vehicles.map((v) => {
                  const isCurrent = v.id === selectedVehicle?.id || v.is_selected
                  const estRange = Math.round((v.battery_capacity_kwh / 0.15) * 0.95)
                  return (
                    <div
                      key={v.id}
                      className={`p-3.5 rounded-xl border transition flex items-center justify-between gap-4 ${
                        isCurrent
                          ? 'bg-cockpit-teal/[0.07] border-cockpit-teal/40'
                          : 'bg-white/[0.02] border-white/5 hover:border-white/15'
                      }`}
                    >
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm text-white">{v.vehicle_name}</span>
                          {isCurrent && (
                            <span className="px-2 py-0.5 rounded-full bg-cockpit-teal/20 text-cockpit-teal text-[10px] font-medium">
                              Active in Cockpit
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 label-quiet text-xs font-mono">
                          <span>{v.battery_capacity_kwh} kWh Pack</span>
                          <span>·</span>
                          <span>{v.connector_type}</span>
                          <span>·</span>
                          <span className="text-slate-300">~{estRange} km range</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {!isCurrent && (
                          <button
                            type="button"
                            onClick={() => handleSelectVehicle(v.id)}
                            disabled={actionLoading}
                            className="btn-secondary px-3 py-1.5 text-xs hover:text-white"
                          >
                            Select
                          </button>
                        )}
                        {vehicles.length > 1 && (
                          <button
                            type="button"
                            onClick={() => handleDeleteVehicle(v.id)}
                            disabled={actionLoading}
                            className="text-slate-500 hover:text-rose-400 p-1.5 rounded-lg transition"
                            title="Delete vehicle profile"
                          >
                            🗑️
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* TAB 2: TRIP HISTORY */}
          {activeTab === 'history' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="label-subhead text-white">Completed Journeys</span>
                <button
                  type="button"
                  onClick={fetchHistory}
                  disabled={isHistoryLoading}
                  className="label-quiet hover:text-white text-xs flex items-center gap-1"
                >
                  <span>🔄</span>
                  <span>Refresh</span>
                </button>
              </div>

              {isHistoryLoading && (
                <div className="py-8 text-center text-xs text-slate-400">
                  <div className="animate-spin rounded-full h-5 w-5 border-2 border-cockpit-teal border-t-transparent mx-auto mb-2"></div>
                  <p>Loading journey ledger...</p>
                </div>
              )}

              {historyError && (
                <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-amber-300 text-xs">
                  {historyError}
                </div>
              )}

              {!isHistoryLoading && tripHistory.length === 0 && (
                <div className="py-8 text-center border border-dashed border-white/10 rounded-xl space-y-1">
                  <span className="text-2xl">🛣️</span>
                  <p className="text-xs font-medium text-slate-300">No journeys logged yet</p>
                  <p className="label-quiet text-[11px]">
                    Complete a trip simulation or navigation journey to automatically record route telemetry.
                  </p>
                </div>
              )}

              {!isHistoryLoading && tripHistory.length > 0 && (
                <div className="space-y-2.5">
                  {tripHistory.map((trip) => {
                    const formattedDate = new Date(trip.created_at).toLocaleString([], {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                    return (
                      <div
                        key={trip.id}
                        className="p-3.5 rounded-xl bg-white/[0.02] border border-white/5 space-y-2 hover:border-white/15 transition"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="space-y-0.5">
                            <span className="text-xs font-medium text-white flex items-center gap-1.5">
                              <span>{trip.origin_name || 'Origin'}</span>
                              <span className="text-slate-500">→</span>
                              <span className="text-cockpit-teal">{trip.dest_name}</span>
                            </span>
                            <span className="label-quiet text-[10px] block font-mono">
                              {formattedDate} · {trip.vehicle_name || 'EV'}
                            </span>
                          </div>
                          <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 text-[10px] font-mono shrink-0">
                            {trip.status}
                          </span>
                        </div>

                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono text-slate-300 pt-1 border-t border-white/5">
                          <span>
                            <strong>{trip.total_distance_km} km</strong>{' '}
                            <span className="label-quiet">distance</span>
                          </span>
                          <span>·</span>
                          <span>
                            <strong>{trip.total_energy_kwh} kWh</strong>{' '}
                            <span className="label-quiet">energy</span>
                          </span>
                          <span>·</span>
                          <span>
                            <strong>{trip.charging_stop_count}</strong>{' '}
                            <span className="label-quiet">charging stops</span>
                          </span>
                        </div>

                        {trip.charging_stops_summary && (
                          <p className="label-quiet text-[11px] text-slate-400 bg-white/5 p-2 rounded-lg font-sans">
                            {trip.charging_stops_summary}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* TAB 3: ACCOUNT */}
          {activeTab === 'account' && (
            <div className="space-y-4">
              <div className="p-4 rounded-xl bg-white/[0.02] border border-white/5 space-y-3">
                <span className="label-subhead text-white block">Account Information</span>
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between py-1 border-b border-white/5">
                    <span className="label-quiet">Driver Name</span>
                    <span className="text-white font-medium">{user?.name}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-white/5">
                    <span className="label-quiet">Email Address</span>
                    <span className="text-white font-mono">{user?.email}</span>
                  </div>
                  <div className="flex justify-between py-1 border-b border-white/5">
                    <span className="label-quiet">Active Vehicle</span>
                    <span className="text-cockpit-teal">{selectedVehicle?.vehicle_name || 'Virtual EV'}</span>
                  </div>
                  <div className="flex justify-between py-1">
                    <span className="label-quiet">Member Since</span>
                    <span className="label-quiet font-mono">
                      {user?.created_at ? new Date(user.created_at).toLocaleDateString() : 'Today'}
                    </span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={logout}
                className="w-full py-2.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 rounded-xl text-xs font-medium transition flex items-center justify-center gap-2"
              >
                <span>🚪</span>
                <span>Sign Out of VoltGuide</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

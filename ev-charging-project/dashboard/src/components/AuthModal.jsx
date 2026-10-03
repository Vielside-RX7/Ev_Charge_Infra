import React, { useState } from 'react'
import { useAuth } from '../auth/AuthContext'

const VEHICLE_PRESETS = [
  { name: 'Tata Nexon EV Max', capacity: 30.0, connector: 'CCS2' },
  { name: 'MG ZS EV (50.3 kWh)', capacity: 50.3, connector: 'CCS2' },
  { name: 'Tata Tiago EV', capacity: 24.0, connector: 'CCS2' },
  { name: 'Hyundai Ioniq 5 (72.6 kWh)', capacity: 72.6, connector: 'CCS2' },
  { name: 'BYD Atto 3 (60.5 kWh)', capacity: 60.48, connector: 'CCS2' },
  { name: 'Mahindra XUV400', capacity: 39.4, connector: 'CCS2' },
]

export default function AuthModal() {
  const { isAuthModalOpen, setIsAuthModalOpen, login, signup, authError, setAuthError } = useAuth()

  const [tab, setTab] = useState('login') // 'login' | 'signup'
  const [loading, setLoading] = useState(false)

  // Login form state
  const [loginEmail, setLoginEmail] = useState('')
  const [loginPassword, setLoginPassword] = useState('')

  // Signup form state
  const [signupName, setSignupName] = useState('')
  const [signupEmail, setSignupEmail] = useState('')
  const [signupPassword, setSignupPassword] = useState('')
  const [selectedPresetIndex, setSelectedPresetIndex] = useState(0)

  if (!isAuthModalOpen) return null

  const handleClose = () => {
    setIsAuthModalOpen(false)
    setAuthError(null)
  }

  const handleLoginSubmit = async (e) => {
    e.preventDefault()
    if (!loginEmail || !loginPassword) {
      setAuthError('Please fill in all fields.')
      return
    }
    setLoading(true)
    await login(loginEmail, loginPassword)
    setLoading(false)
  }

  const handleSignupSubmit = async (e) => {
    e.preventDefault()
    if (!signupName || !signupEmail || !signupPassword) {
      setAuthError('Please fill in all required fields.')
      return
    }
    if (signupPassword.length < 6) {
      setAuthError('Password must be at least 6 characters.')
      return
    }
    const preset = VEHICLE_PRESETS[selectedPresetIndex]
    setLoading(true)
    await signup({
      name: signupName,
      email: signupEmail,
      password: signupPassword,
      initial_vehicle: {
        vehicle_name: preset.name,
        battery_capacity_kwh: preset.capacity,
        connector_type: preset.connector,
        energy_consumption_kwh_per_km: 0.15,
      },
    })
    setLoading(false)
  }

  const handleDemoLogin = async () => {
    setLoading(true)
    setAuthError(null)
    // Try login with demo account, if not found, create one
    const res = await login('demo@voltguide.com', 'VoltGuideDemo2026!')
    if (!res.success) {
      await signup({
        name: 'VoltGuide Driver',
        email: 'demo@voltguide.com',
        password: 'VoltGuideDemo2026!',
        initial_vehicle: {
          vehicle_name: 'Tata Nexon EV Max',
          battery_capacity_kwh: 30.0,
          connector_type: 'CCS2',
          energy_consumption_kwh_per_km: 0.15,
        },
      })
    }
    setLoading(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-md bg-[#0D121D] border border-white/10 rounded-2xl shadow-2xl p-6 text-slate-100 space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/5 pb-3">
          <div>
            <h2 className="text-base font-medium text-white tracking-tight">
              {tab === 'login' ? 'Sign In to VoltGuide' : 'Create Driver Profile'}
            </h2>
            <p className="label-quiet text-xs mt-0.5">
              Sync vehicle battery parameters, trip logs & community reviews
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="text-slate-400 hover:text-white transition p-1.5 rounded-lg hover:bg-white/5"
          >
            ✕
          </button>
        </div>

        {/* Tab Toggle */}
        <div className="flex rounded-xl bg-white/5 p-1 text-xs">
          <button
            type="button"
            onClick={() => {
              setTab('login')
              setAuthError(null)
            }}
            className={`flex-1 py-1.5 rounded-lg font-medium transition ${
              tab === 'login' ? 'bg-white/10 text-white shadow-sm' : 'text-slate-400 hover:text-white'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => {
              setTab('signup')
              setAuthError(null)
            }}
            className={`flex-1 py-1.5 rounded-lg font-medium transition ${
              tab === 'signup' ? 'bg-white/10 text-white shadow-sm' : 'text-slate-400 hover:text-white'
            }`}
          >
            Create Account
          </button>
        </div>

        {/* Error Alert */}
        {authError && (
          <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs flex items-center gap-2">
            <span>⚠️</span>
            <span>{authError}</span>
          </div>
        )}

        {/* Quick Demo One-Click Access */}
        <button
          type="button"
          onClick={handleDemoLogin}
          disabled={loading}
          className="w-full py-2 bg-cockpit-teal/10 hover:bg-cockpit-teal/20 text-cockpit-teal border border-cockpit-teal/30 rounded-xl text-xs font-medium transition flex items-center justify-center gap-2"
        >
          <span>⚡</span>
          <span>1-Click Demo Account Login</span>
        </button>

        <div className="relative flex items-center justify-center">
          <div className="w-full border-t border-white/5"></div>
          <span className="absolute px-2 bg-[#0D121D] label-quiet text-[10px] uppercase tracking-wider">
            or continue with email
          </span>
        </div>

        {/* Login Form */}
        {tab === 'login' && (
          <form onSubmit={handleLoginSubmit} className="space-y-3.5">
            <div>
              <label className="label-quiet block mb-1">Email Address</label>
              <input
                type="email"
                required
                value={loginEmail}
                onChange={(e) => setLoginEmail(e.target.value)}
                placeholder="driver@example.com"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              />
            </div>

            <div>
              <label className="label-quiet block mb-1">Password</label>
              <input
                type="password"
                required
                value={loginPassword}
                onChange={(e) => setLoginPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-2.5 text-xs font-medium flex items-center justify-center gap-2 mt-2"
            >
              {loading ? 'Authenticating...' : 'Sign In'}
            </button>
          </form>
        )}

        {/* Signup Form */}
        {tab === 'signup' && (
          <form onSubmit={handleSignupSubmit} className="space-y-3">
            <div>
              <label className="label-quiet block mb-1">Driver Name</label>
              <input
                type="text"
                required
                value={signupName}
                onChange={(e) => setSignupName(e.target.value)}
                placeholder="Arnav Sharma"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              />
            </div>

            <div>
              <label className="label-quiet block mb-1">Email Address</label>
              <input
                type="email"
                required
                value={signupEmail}
                onChange={(e) => setSignupEmail(e.target.value)}
                placeholder="arnav@voltguide.com"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              />
            </div>

            <div>
              <label className="label-quiet block mb-1">Create Password</label>
              <input
                type="password"
                required
                minLength={6}
                value={signupPassword}
                onChange={(e) => setSignupPassword(e.target.value)}
                placeholder="Minimum 6 characters"
                className="w-full bg-white/5 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              />
            </div>

            <div>
              <label className="label-quiet block mb-1">Primary EV Model</label>
              <select
                value={selectedPresetIndex}
                onChange={(e) => setSelectedPresetIndex(Number(e.target.value))}
                className="w-full bg-[#121927] border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition"
              >
                {VEHICLE_PRESETS.map((v, idx) => (
                  <option key={v.name} value={idx}>
                    {v.name} · {v.capacity} kWh ({v.connector})
                  </option>
                ))}
              </select>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-2.5 text-xs font-medium flex items-center justify-center gap-2 mt-2"
            >
              {loading ? 'Creating Account...' : 'Create Account & Sync EV'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

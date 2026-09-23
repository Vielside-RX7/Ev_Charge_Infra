import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  loadGooglePlacesApi,
  PlacesSession,
  getDestinationPredictions,
  getSelectedPlaceCoordinates,
} from '../trip/googlePlacesService'

/**
 * DestinationSearch Component
 * 
 * Custom-designed Google Places Autocomplete search input adhering to Change 26 aesthetics:
 * - Restrained dark palette, seamless glass backdrop
 * - Keyboard navigation (ArrowUp, ArrowDown, Enter, Escape)
 * - Session token billing management
 * - Resolves lat/lng and authoritative destination metadata
 * - Non-crashing failure and fallback states
 */
export default function DestinationSearch({
  onDestinationSelect,
  onFallbackToMap,
  isSelectingOnMap = false,
  selectedDestination = null,
}) {
  const [query, setQuery] = useState('')
  const [predictions, setPredictions] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [isResolving, setIsResolving] = useState(false)
  const [isOpen, setIsOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const [apiStatus, setApiStatus] = useState('initializing') // 'initializing' | 'ready' | 'missing_key' | 'error'
  const [statusMessage, setStatusMessage] = useState('')

  const sessionRef = useRef(null)
  const debounceTimerRef = useRef(null)
  const wrapperRef = useRef(null)
  const inputRef = useRef(null)

  // Initialize Places Session
  if (!sessionRef.current) {
    sessionRef.current = new PlacesSession()
  }

  // Check and load Google Places SDK
  useEffect(() => {
    let isMounted = true
    const apiKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || ''

    if (!apiKey || apiKey.trim() === '' || apiKey.includes('your_google_maps_api_key')) {
      if (isMounted) {
        setApiStatus('missing_key')
        setStatusMessage('Google Places API key is not configured.')
      }
      return
    }

    loadGooglePlacesApi(apiKey)
      .then((res) => {
        if (!isMounted) return
        if (res.success) {
          setApiStatus('ready')
          setStatusMessage('')
        } else {
          setApiStatus(res.reason === 'missing_key' ? 'missing_key' : 'error')
          setStatusMessage(res.message || 'Google Places could not be loaded.')
        }
      })
      .catch((err) => {
        if (!isMounted) return
        setApiStatus('error')
        setStatusMessage(err?.message || 'Failed to initialize Google Maps Platform.')
      })

    return () => {
      isMounted = false
    }
  }, [])

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Synchronize input display with selected destination name if external changes happen
  useEffect(() => {
    if (selectedDestination?.name) {
      setQuery(selectedDestination.name)
    } else if (!selectedDestination) {
      setQuery('')
    }
  }, [selectedDestination])

  // Handle query input with 300ms debounce for request & cost control
  const handleInputChange = useCallback((e) => {
    const value = e.target.value
    setQuery(value)
    setHighlightedIndex(-1)

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }

    if (!value || value.trim().length < 2) {
      setPredictions([])
      setIsOpen(false)
      setIsLoading(false)
      return
    }

    setIsLoading(true)
    debounceTimerRef.current = setTimeout(async () => {
      const { predictions: results, error } = await getDestinationPredictions(
        value,
        sessionRef.current
      )
      setIsLoading(false)
      if (error) {
        console.warn('[DestinationSearch] Autocomplete error:', error)
      }
      setPredictions(results || [])
      setIsOpen(true)
    }, 300)
  }, [])

  // Handle prediction selection
  const handleSelectPrediction = useCallback(
    async (prediction) => {
      if (!prediction) return
      setIsOpen(false)
      setIsResolving(true)
      setQuery(prediction.mainText || prediction.description)

      try {
        const { place, error } = await getSelectedPlaceCoordinates(
          prediction,
          sessionRef.current
        )
        setIsResolving(false)

        if (error || !place) {
          setStatusMessage(error || 'Could not resolve destination coordinates.')
          return
        }

        if (onDestinationSelect) {
          onDestinationSelect(place)
        }
      } catch (err) {
        setIsResolving(false)
        setStatusMessage(err?.message || 'Failed to select destination.')
      }
    },
    [onDestinationSelect]
  )

  // Keyboard navigation
  const handleKeyDown = (e) => {
    if (!isOpen || predictions.length === 0) {
      if (e.key === 'ArrowDown' && predictions.length > 0) {
        setIsOpen(true)
      }
      return
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightedIndex((prev) => (prev < predictions.length - 1 ? prev + 1 : 0))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightedIndex((prev) => (prev > 0 ? prev - 1 : predictions.length - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightedIndex >= 0 && highlightedIndex < predictions.length) {
        handleSelectPrediction(predictions[highlightedIndex])
      }
    } else if (e.key === 'Escape') {
      setIsOpen(false)
    }
  }

  const handleClear = () => {
    setQuery('')
    setPredictions([])
    setIsOpen(false)
    setHighlightedIndex(-1)
    if (inputRef.current) {
      inputRef.current.focus()
    }
  }

  return (
    <div ref={wrapperRef} className="relative w-full space-y-3">
      {/* Title & Prompt Header */}
      <div className="space-y-1">
        <label
          htmlFor="destination-search-input"
          className="label-subhead block text-slate-400 font-medium"
        >
          Where do you want to go?
        </label>
        <p className="label-quiet text-slate-500">
          Enter any address, city, or landmark. VoltGuide automatically determines optimal charging stops.
        </p>
      </div>

      {/* Main Search Input Container */}
      <div className="relative group">
        <div className="relative flex items-center">
          {/* Leading Search Icon */}
          <div className="absolute left-4 pointer-events-none text-slate-400 flex items-center justify-center">
            {isLoading || isResolving ? (
              <div className="w-4 h-4 border-2 border-cockpit-teal border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg
                className="w-4 h-4 text-slate-400 group-focus-within:text-cockpit-teal transition-colors duration-200"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            )}
          </div>

          {/* Text Input */}
          <input
            ref={inputRef}
            id="destination-search-input"
            type="text"
            value={query}
            onChange={handleInputChange}
            onFocus={() => {
              if (predictions.length > 0 && query.trim().length >= 2) {
                setIsOpen(true)
              }
            }}
            onKeyDown={handleKeyDown}
            disabled={apiStatus === 'missing_key' || apiStatus === 'error'}
            placeholder={
              apiStatus === 'missing_key'
                ? 'API key required for live search — select on map below'
                : 'Search destination (e.g. Bengaluru, Mysore Palace, Kempegowda Airport...)'
            }
            autoComplete="off"
            className={`w-full pl-11 pr-10 py-3.5 bg-[#080B11] border border-white/10 rounded-2xl text-white placeholder-slate-500 text-sm font-light tracking-wide focus:outline-none focus:border-cockpit-teal/40 focus:ring-1 focus:ring-cockpit-teal/30 transition-all duration-200 shadow-inner ${
              apiStatus === 'missing_key' || apiStatus === 'error'
                ? 'opacity-60 cursor-not-allowed bg-white/[0.02]'
                : ''
            }`}
          />

          {/* Trailing Clear Button */}
          {query && apiStatus === 'ready' && (
            <button
              type="button"
              onClick={handleClear}
              className="absolute right-3.5 p-1 text-slate-500 hover:text-slate-300 transition cursor-pointer"
              title="Clear search"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Prediction Dropdown */}
        {isOpen && (
          <div className="absolute top-full left-0 right-0 mt-2 z-50 seamless-glass rounded-2xl border border-white/10 shadow-2xl overflow-hidden backdrop-blur-2xl">
            {predictions.length > 0 ? (
              <ul className="py-1.5 max-h-64 overflow-y-auto divide-y divide-white/[0.04]">
                {predictions.map((item, idx) => {
                  const isHighlighted = idx === highlightedIndex
                  return (
                    <li
                      key={item.placeId || idx}
                      onMouseDown={(e) => {
                        e.preventDefault()
                        handleSelectPrediction(item)
                      }}
                      onMouseEnter={() => setHighlightedIndex(idx)}
                      className={`px-4 py-3 flex items-start gap-3 cursor-pointer transition-colors duration-150 ${
                        isHighlighted
                          ? 'bg-cockpit-teal/10 text-white'
                          : 'hover:bg-white/[0.04] text-slate-300'
                      }`}
                    >
                      <div className="mt-0.5 text-slate-500 flex-shrink-0">
                        <svg
                          className={`w-4 h-4 ${isHighlighted ? 'text-cockpit-teal' : 'text-slate-500'}`}
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="2"
                            d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"
                          />
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="2"
                            d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"
                          />
                        </svg>
                      </div>

                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-normal text-white truncate">
                          {item.mainText}
                        </div>
                        {item.secondaryText && (
                          <div className="text-[11px] text-slate-400 truncate mt-0.5">
                            {item.secondaryText}
                          </div>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            ) : query.trim().length >= 2 && !isLoading ? (
              <div className="px-5 py-4 text-center text-xs text-slate-400">
                No matching destinations found. Check spelling or try a broader landmark.
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/* API Notice / Fallback Message (Non-Crashing) */}
      {apiStatus === 'missing_key' && (
        <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/20 text-xs text-amber-200 flex items-start gap-2.5">
          <span className="text-amber-400 text-sm mt-0.5">ⓘ</span>
          <div className="space-y-1">
            <p className="font-medium text-amber-100">
              Google Places API key is not configured.
            </p>
            <p className="text-[11px] text-amber-200/80 leading-relaxed">
              Add <code className="font-mono text-amber-300">VITE_GOOGLE_MAPS_API_KEY</code> to your environment file, or select your destination directly on the map.
            </p>
          </div>
        </div>
      )}

      {apiStatus === 'error' && (
        <div className="p-3 rounded-xl bg-rose-950/20 border border-rose-800/30 text-xs text-rose-300 flex items-center justify-between">
          <span>{statusMessage || 'Google Places service unavailable.'}</span>
          <button
            type="button"
            onClick={onFallbackToMap}
            className="text-[11px] underline hover:text-white cursor-pointer"
          >
            Select on map
          </button>
        </div>
      )}

      {/* Secondary Map Selection Fallback (Requirement 8) */}
      <div className="pt-1 flex items-center justify-between text-xs">
        {isSelectingOnMap ? (
          <div className="flex items-center gap-2 text-amber-300 text-xs font-medium animate-pulse">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
            <span>Click anywhere on the map to place destination marker</span>
          </div>
        ) : (
          <button
            type="button"
            onClick={onFallbackToMap}
            className="text-slate-400 hover:text-white transition text-xs flex items-center gap-1.5 cursor-pointer py-1"
          >
            <span className="text-slate-500">⚑</span>
            <span>Or select directly on map</span>
          </button>
        )}
      </div>
    </div>
  )
}

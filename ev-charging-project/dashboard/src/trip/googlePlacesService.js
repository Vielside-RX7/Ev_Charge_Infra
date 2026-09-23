/**
 * Google Places Autocomplete Data API Service
 * 
 * Provides debounced autocomplete predictions and place detail resolution
 * using Google Maps Platform Places Data API (AutocompleteService / AutocompleteSuggestion)
 * with session token cost control.
 * 
 * Complies with Change 29:
 * - Uses Places Data API (custom UI, NOT the prebuilt widget)
 * - Session token lifecycle management (batches queries + detail fetch)
 * - Fetches ONLY required fields: ['displayName', 'formattedAddress', 'location', 'id']
 * - Graceful fallback on missing API key, network failure, or API quota error
 * - Never logs or exposes raw API keys
 */

let apiLoadPromise = null

/**
 * Module-level reference to the Places library object returned by
 * google.maps.importLibrary("places"). All consumers MUST use this
 * instead of window.google.maps.places, which may not be populated
 * by the modern dynamic loader.
 */
let placesLib = null

/**
 * Returns the loaded Places library, or null if not yet loaded.
 */
export function getPlacesLib() {
  return placesLib
}

/**
 * Dynamically loads the Google Maps JavaScript API with Places library.
 * Uses Google's recommended single importLibrary loader pattern.
 * 
 * @param {string} apiKey - Google Cloud browser API key
 * @returns {Promise<{ success: boolean, reason?: string, message?: string, placesLib?: Object }>}
 */
export function loadGooglePlacesApi(apiKey) {
  if (typeof window === 'undefined') {
    return Promise.resolve({ success: false, reason: 'ssr', message: 'Window not available' })
  }

  // Already loaded
  if (placesLib) {
    return Promise.resolve({ success: true, placesLib })
  }

  if (!apiKey || typeof apiKey !== 'string' || apiKey.trim() === '' || apiKey.includes('your_google_maps_api_key')) {
    return Promise.resolve({
      success: false,
      reason: 'missing_key',
      message: 'Google Places API key is not configured. Set VITE_GOOGLE_MAPS_API_KEY in .env',
    })
  }

  if (apiLoadPromise) {
    return apiLoadPromise
  }

  apiLoadPromise = new Promise(async (resolve) => {
    try {
      // 1. If google.maps.importLibrary is already present on window
      if (window.google?.maps?.importLibrary) {
        try {
          placesLib = await window.google.maps.importLibrary('places')
          if (placesLib) {
            resolve({ success: true, placesLib })
            return
          }
        } catch (libErr) {
          console.error('[VoltGuide] importLibrary("places") error:', libErr?.message || libErr)
          apiLoadPromise = null
          resolve({
            success: false,
            reason: 'places_import_error',
            message: libErr?.message || 'Failed to import Places library.',
          })
          return
        }
      }

      // 2. Check if a script tag already exists in the document
      const existingScript = document.querySelector('script[data-voltguide-google-maps="true"]')
      if (existingScript) {
        if (!window.google?.maps?.importLibrary) {
          await new Promise((res, rej) => {
            existingScript.addEventListener('load', res, { once: true })
            existingScript.addEventListener('error', rej, { once: true })
          })
        }
        if (window.google?.maps?.importLibrary) {
          placesLib = await window.google.maps.importLibrary('places')
        }
        if (placesLib) {
          resolve({ success: true, placesLib })
        } else {
          apiLoadPromise = null
          resolve({
            success: false,
            reason: 'places_missing',
            message: 'Google Maps loaded but Places library is missing.',
          })
        }
        return
      }

      // 3. Create and append the single Maps JavaScript API script with libraries=places
      const script = document.createElement('script')
      script.setAttribute('data-voltguide-google-maps', 'true')
      script.async = true
      script.defer = true
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(
        apiKey.trim()
      )}&libraries=places&v=weekly`

      script.onload = async () => {
        try {
          if (window.google?.maps?.importLibrary) {
            placesLib = await window.google.maps.importLibrary('places')
          }
          if (!placesLib && window.google?.maps?.places) {
            placesLib = window.google.maps.places
          }

          if (placesLib) {
            resolve({ success: true, placesLib })
          } else {
            apiLoadPromise = null
            resolve({
              success: false,
              reason: 'places_missing',
              message: 'Google Maps loaded but Places library is missing.',
            })
          }
        } catch (err) {
          if (window.google?.maps?.places) {
            placesLib = window.google.maps.places
            resolve({ success: true, placesLib })
            return
          }
          apiLoadPromise = null
          console.error('[VoltGuide] Places importLibrary error:', err?.message || err)
          resolve({
            success: false,
            reason: 'places_import_error',
            message: err?.message || 'Failed to import Places library.',
          })
        }
      }

      script.onerror = (e) => {
        apiLoadPromise = null
        console.error('[VoltGuide] Google Maps SDK script network error:', e)
        resolve({
          success: false,
          reason: 'network_error',
          message: 'Could not connect to Google Maps Platform. Check network or API key restrictions.',
        })
      }

      document.head.appendChild(script)
    } catch (err) {
      apiLoadPromise = null
      console.error('[VoltGuide] Google Maps initialization error:', err?.message || err)
      resolve({
        success: false,
        reason: 'init_error',
        message: err?.message || 'Failed to initialize Google Maps Platform.',
      })
    }
  })

  return apiLoadPromise
}

/**
 * Creates and manages an Autocomplete Session Token for billing efficiency.
 */
export class PlacesSession {
  constructor() {
    this.token = null
    this.reset()
  }

  reset() {
    const lib = placesLib || window.google?.maps?.places
    if (lib?.AutocompleteSessionToken) {
      this.token = new lib.AutocompleteSessionToken()
    } else {
      this.token = null
    }
    return this.token
  }

  getToken() {
    if (!this.token) {
      this.reset()
    }
    return this.token
  }
}

/**
 * Fetches place autocomplete predictions for a search query.
 * 
 * @param {string} query - Destination query string
 * @param {PlacesSession} session - Active session token tracker
 * @param {Object} [options] - Optional country / bounds constraints
 * @returns {Promise<{ predictions: Array, error: string|null }>}
 */
export async function getDestinationPredictions(query, session, options = {}) {
  const trimmed = (query || '').trim()
  if (!trimmed || trimmed.length < 2) {
    return { predictions: [], error: null }
  }

  let lib = placesLib
  if (!lib && window.google?.maps?.importLibrary) {
    try {
      placesLib = await window.google.maps.importLibrary('places')
      lib = placesLib
    } catch (e) {
      // ignore
    }
  }
  if (!lib) {
    lib = window.google?.maps?.places
  }

  if (!lib) {
    return { predictions: [], error: 'Google Places library is not initialized.' }
  }

  try {
    // 1. Try modern AutocompleteSuggestion (New Places Library Data API)
    if (lib.AutocompleteSuggestion?.fetchAutocompleteSuggestions) {
      const request = {
        input: trimmed,
        sessionToken: session?.getToken ? session.getToken() : session?.token || undefined,
        includedRegionCodes: options.regionCodes || ['in'],
      }
      const response = await lib.AutocompleteSuggestion.fetchAutocompleteSuggestions(request)
      const suggestions = response?.suggestions || []
      const predictions = suggestions
        .filter((s) => s.placePrediction)
        .map((s) => ({
          placeId: s.placePrediction.placeId,
          description: s.placePrediction.text?.toString() || '',
          mainText: s.placePrediction.mainText?.toString() || s.placePrediction.text?.toString() || '',
          secondaryText: s.placePrediction.secondaryText?.toString() || '',
          _suggestion: s,
        }))
      return { predictions, error: null }
    }

    // 2. Standard Maps JS AutocompleteService Data API
    if (lib.AutocompleteService) {
      return new Promise((resolve) => {
        const service = new lib.AutocompleteService()
        const req = {
          input: trimmed,
          sessionToken: session?.token || undefined,
        }
        if (options.componentRestrictions) {
          req.componentRestrictions = options.componentRestrictions
        }

        service.getPlacePredictions(req, (results, status) => {
          const PlacesStatus = lib.PlacesServiceStatus || window.google?.maps?.places?.PlacesServiceStatus

          if ((status === 'OK' || status === PlacesStatus?.OK) && results) {
            const predictions = results.map((p) => ({
              placeId: p.place_id,
              description: p.description,
              mainText: p.structured_formatting?.main_text || p.description,
              secondaryText: p.structured_formatting?.secondary_text || '',
              _raw: p,
            }))
            resolve({ predictions, error: null })
          } else if (status === 'ZERO_RESULTS' || status === PlacesStatus?.ZERO_RESULTS) {
            resolve({ predictions: [], error: null })
          } else {
            resolve({
              predictions: [],
              error: `Google Places search error: ${status}`,
            })
          }
        })
      })
    }

    return { predictions: [], error: 'Google Places autocomplete service is unavailable.' }
  } catch (err) {
    return { predictions: [], error: err?.message || 'Error fetching predictions' }
  }
}

/**
 * Resolves minimal place details (name, formatted_address, lat, lng) for a selected prediction.
 * Automatically refreshes the session token upon completion.
 * 
 * @param {Object} prediction - Selected prediction object
 * @param {PlacesSession} session - Active session token tracker
 * @returns {Promise<{ place: Object|null, error: string|null }>}
 */
export async function getSelectedPlaceCoordinates(prediction, session) {
  if (!prediction || !prediction.placeId) {
    return { place: null, error: 'Invalid place prediction' }
  }

  let lib = placesLib
  if (!lib && window.google?.maps?.importLibrary) {
    try {
      placesLib = await window.google.maps.importLibrary('places')
      lib = placesLib
    } catch (e) {
      // ignore
    }
  }
  if (!lib) {
    lib = window.google?.maps?.places
  }

  try {
    // 1. Try modern Place API (PlacePrediction -> toPlace() -> fetchFields())
    if (prediction._suggestion?.placePrediction?.toPlace) {
      const placeObj = prediction._suggestion.placePrediction.toPlace()
      await placeObj.fetchFields({
        fields: ['displayName', 'formattedAddress', 'location', 'id'],
      })

      const lat = typeof placeObj.location?.lat === 'function' ? placeObj.location.lat() : placeObj.location?.lat
      const lng = typeof placeObj.location?.lng === 'function' ? placeObj.location.lng() : placeObj.location?.lng

      if (typeof lat === 'number' && typeof lng === 'number') {
        session?.reset()
        const rawName = placeObj.displayName
        const resolvedName = typeof rawName === 'string' ? rawName : (rawName?.text || prediction.mainText)
        return {
          place: {
            placeId: placeObj.id || prediction.placeId,
            name: resolvedName,
            address: placeObj.formattedAddress || prediction.description,
            latitude: lat,
            longitude: lng,
          },
          error: null,
        }
      }
    }

    // 1b. If lib.Place exists and we have placeId
    if (lib?.Place) {
      try {
        const placeObj = new lib.Place({
          id: prediction.placeId,
          requestedLanguage: 'en',
        })
        await placeObj.fetchFields({
          fields: ['displayName', 'formattedAddress', 'location', 'id'],
        })
        const lat = typeof placeObj.location?.lat === 'function' ? placeObj.location.lat() : placeObj.location?.lat
        const lng = typeof placeObj.location?.lng === 'function' ? placeObj.location.lng() : placeObj.location?.lng

        if (typeof lat === 'number' && typeof lng === 'number') {
          session?.reset()
          const rawName = placeObj.displayName
          const resolvedName = typeof rawName === 'string' ? rawName : (rawName?.text || prediction.mainText)
          return {
            place: {
              placeId: placeObj.id || prediction.placeId,
              name: resolvedName,
              address: placeObj.formattedAddress || prediction.description,
              latitude: lat,
              longitude: lng,
            },
            error: null,
          }
        }
      } catch (placeErr) {
        console.warn('[VoltGuide] Place.fetchFields fallback to PlacesService:', placeErr)
      }
    }

    // 2. Standard PlacesService.getDetails
    if (lib?.PlacesService) {
      return new Promise((resolve) => {
        const dummyNode = document.createElement('div')
        const placesService = new lib.PlacesService(dummyNode)

        placesService.getDetails(
          {
            placeId: prediction.placeId,
            fields: ['name', 'formatted_address', 'geometry.location', 'place_id'],
            sessionToken: session?.token || undefined,
          },
          (details, status) => {
            const PlacesStatus = lib.PlacesServiceStatus || window.google?.maps?.places?.PlacesServiceStatus

            if ((status === 'OK' || status === PlacesStatus?.OK) && details?.geometry?.location) {
              const lat = details.geometry.location.lat()
              const lng = details.geometry.location.lng()

              // Refresh session token for subsequent autocomplete journeys
              session?.reset()

              resolve({
                place: {
                  placeId: details.place_id || prediction.placeId,
                  name: details.name || prediction.mainText,
                  address: details.formatted_address || prediction.description,
                  latitude: lat,
                  longitude: lng,
                },
                error: null,
              })
            } else {
              resolve({
                place: null,
                error: `Failed to retrieve place coordinates (${status})`,
              })
            }
          }
        )
      })
    }

    return { place: null, error: 'Google Places details service is unavailable.' }
  } catch (err) {
    return { place: null, error: err?.message || 'Error resolving place coordinates' }
  }
}

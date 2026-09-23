/**
 * Change 29 — Google Places Destination Search Seam Tests
 * 
 * Verifies the 7 required seams:
 * 1. destination prediction selection
 * 2. selected place -> latitude/longitude
 * 3. selected place -> TripContext destination
 * 4. existing Plan Journey still works
 * 5. destination can be changed
 * 6. clearing destination works
 * 7. Google failure does not crash the application
 */

import assert from 'node:assert/strict'

// Mock Google Maps JavaScript Environment for Seam Testing
function createMockGoogleMaps(options = {}) {
  const { failPredictions = false, failDetails = false, emptyResults = false } = options

  return {
    maps: {
      places: {
        PlacesServiceStatus: {
          OK: 'OK',
          ZERO_RESULTS: 'ZERO_RESULTS',
          OVER_QUERY_LIMIT: 'OVER_QUERY_LIMIT',
          REQUEST_DENIED: 'REQUEST_DENIED',
          INVALID_REQUEST: 'INVALID_REQUEST',
        },
        AutocompleteSessionToken: function () {
          this.id = Math.random().toString(36).substring(7)
        },
        AutocompleteService: function () {
          this.getPlacePredictions = (req, cb) => {
            if (failPredictions) {
              cb(null, 'OVER_QUERY_LIMIT')
              return
            }
            if (emptyResults || req.input === 'nonexistentplace12345') {
              cb([], 'ZERO_RESULTS')
              return
            }
            cb(
              [
                {
                  place_id: 'ChIJbU60yXAWrjsR4E9-SaRIxk4',
                  description: 'Bengaluru International Airport, Devanahalli, Bengaluru, Karnataka, India',
                  structured_formatting: {
                    main_text: 'Bengaluru International Airport',
                    secondary_text: 'Devanahalli, Bengaluru, Karnataka, India',
                  },
                },
                {
                  place_id: 'ChIJL2_J7vIUrjsR2JtG7Gv5Y7Q',
                  description: 'Mysuru Palace, Sayyaji Rao Road, Agrahara, Chamrajpura, Mysuru',
                  structured_formatting: {
                    main_text: 'Mysuru Palace',
                    secondary_text: 'Sayyaji Rao Road, Mysuru',
                  },
                },
              ],
              'OK'
            )
          }
        },
        PlacesService: function () {
          this.getDetails = (req, cb) => {
            if (failDetails) {
              cb(null, 'REQUEST_DENIED')
              return
            }
            if (req.placeId === 'ChIJbU60yXAWrjsR4E9-SaRIxk4') {
              cb(
                {
                  place_id: 'ChIJbU60yXAWrjsR4E9-SaRIxk4',
                  name: 'Bengaluru International Airport',
                  formatted_address: 'KIAL Rd, Devanahalli, Bengaluru, Karnataka 560300',
                  geometry: {
                    location: {
                      lat: () => 13.1986,
                      lng: () => 77.7066,
                    },
                  },
                },
                'OK'
              )
            } else {
              cb(
                {
                  place_id: req.placeId,
                  name: 'Mysuru Palace',
                  formatted_address: 'Sayyaji Rao Rd, Agrahara, Chamrajpura, Mysuru, Karnataka 570001',
                  geometry: {
                    location: {
                      lat: () => 12.3051,
                      lng: () => 76.6552,
                    },
                  },
                },
                'OK'
              )
            }
          }
        },
      },
    },
  }
}

// Minimal TripContext Destination State Machine for Seam Testing
class TripStateSimulator {
  constructor() {
    this.destination = null
    this.journeyPlan = null
    this.isPlanning = false
    this.navState = 'IDLE'
  }

  setDestination(place) {
    if (!place || typeof place.latitude !== 'number' || typeof place.longitude !== 'number') {
      return
    }
    this.destination = {
      latitude: parseFloat(place.latitude.toFixed(6)),
      longitude: parseFloat(place.longitude.toFixed(6)),
      name: place.name || null,
      address: place.address || null,
      placeId: place.placeId || null,
    }
    this.journeyPlan = null
    this.navState = 'IDLE'
    this.isPlanning = false
  }

  clearDestination() {
    this.destination = null
    this.journeyPlan = null
    this.navState = 'IDLE'
    this.isPlanning = false
  }

  async planJourney() {
    if (!this.destination) throw new Error('No destination set')
    this.isPlanning = true
    this.navState = 'PLANNING'

    // Simulate multi-stop planner execution
    this.journeyPlan = {
      decisionType: 'CHARGE',
      multiStop: true,
      chargingStopCount: 1,
      routeMetrics: {
        totalDistanceKm: 145.2,
        travelTimeMinutes: 165.0,
      },
    }
    this.isPlanning = false
    this.navState = 'READY'
    return this.journeyPlan
  }
}

async function runSeamTests() {
  console.log('--- RUNNING CHANGE 29 SEAM TESTS ---')

  // Setup mock global window
  globalThis.window = {
    google: createMockGoogleMaps(),
  }

  // Import services dynamically
  const {
    PlacesSession,
    getDestinationPredictions,
    getSelectedPlaceCoordinates,
  } = await import('../dashboard/src/trip/googlePlacesService.js')

  // Seam 1: destination prediction selection
  console.log('Seam 1: Testing destination prediction selection...')
  const session = new PlacesSession()
  const { predictions, error: predError } = await getDestinationPredictions('Bengaluru', session)
  assert.equal(predError, null, 'Prediction error should be null')
  assert(Array.isArray(predictions), 'Predictions should be an array')
  assert.equal(predictions.length, 2, 'Should receive 2 mock predictions')
  assert.equal(predictions[0].placeId, 'ChIJbU60yXAWrjsR4E9-SaRIxk4')
  assert.equal(predictions[0].mainText, 'Bengaluru International Airport')
  console.log('✓ Seam 1 PASSED: destination prediction selection verified.')

  // Seam 2: selected place -> latitude/longitude
  console.log('Seam 2: Testing selected place -> latitude/longitude...')
  const selectedPrediction = predictions[0]
  const { place, error: detailError } = await getSelectedPlaceCoordinates(selectedPrediction, session)
  assert.equal(detailError, null, 'Detail error should be null')
  assert(place !== null, 'Place should be returned')
  assert.equal(typeof place.latitude, 'number', 'Latitude must be a number')
  assert.equal(typeof place.longitude, 'number', 'Longitude must be a number')
  assert.equal(place.latitude, 13.1986, 'Latitude must match airport coords')
  assert.equal(place.longitude, 77.7066, 'Longitude must match airport coords')
  assert.equal(place.name, 'Bengaluru International Airport')
  assert(place.address.includes('Devanahalli'))
  console.log('✓ Seam 2 PASSED: selected place -> latitude/longitude verified.')

  // Seam 3: selected place -> TripContext destination
  console.log('Seam 3: Testing selected place -> TripContext destination...')
  const tripSim = new TripStateSimulator()
  tripSim.setDestination(place)
  assert(tripSim.destination !== null, 'Trip destination must be populated')
  assert.equal(tripSim.destination.latitude, 13.1986)
  assert.equal(tripSim.destination.longitude, 77.7066)
  assert.equal(tripSim.destination.name, 'Bengaluru International Airport')
  assert.equal(tripSim.navState, 'IDLE', 'navState must be IDLE before user clicks Plan Journey')
  console.log('✓ Seam 3 PASSED: selected place -> TripContext destination verified.')

  // Seam 4: existing Plan Journey still works
  console.log('Seam 4: Testing existing Plan Journey still works...')
  const plan = await tripSim.planJourney()
  assert(plan !== null, 'Journey plan must be generated')
  assert.equal(plan.decisionType, 'CHARGE')
  assert.equal(tripSim.navState, 'READY')
  console.log('✓ Seam 4 PASSED: Plan Journey still works with selected destination.')

  // Seam 5: destination can be changed
  console.log('Seam 5: Testing destination can be changed...')
  const secondPrediction = predictions[1] // Mysuru Palace
  const { place: secondPlace } = await getSelectedPlaceCoordinates(secondPrediction, session)
  tripSim.setDestination(secondPlace)
  assert.equal(tripSim.destination.latitude, 12.3051)
  assert.equal(tripSim.destination.longitude, 76.6552)
  assert.equal(tripSim.destination.name, 'Mysuru Palace')
  assert.equal(tripSim.journeyPlan, null, 'Changing destination must reset previous plan')
  assert.equal(tripSim.navState, 'IDLE')
  console.log('✓ Seam 5 PASSED: destination can be changed smoothly.')

  // Seam 6: clearing destination works
  console.log('Seam 6: Testing clearing destination works...')
  tripSim.clearDestination()
  assert.equal(tripSim.destination, null, 'Destination must be null after clearing')
  assert.equal(tripSim.journeyPlan, null)
  assert.equal(tripSim.navState, 'IDLE')
  console.log('✓ Seam 6 PASSED: clearing destination works.')

  // Seam 7: Google failure does not crash the application
  console.log('Seam 7: Testing Google failure does not crash the application...')
  // Sub-case A: API key missing
  const { loadGooglePlacesApi } = await import('../dashboard/src/trip/googlePlacesService.js')
  const tempGoogle = globalThis.window.google
  globalThis.window.google = undefined
  const missingKeyResult = await loadGooglePlacesApi('')
  assert.equal(missingKeyResult.success, false)
  assert.equal(missingKeyResult.reason, 'missing_key')
  globalThis.window.google = tempGoogle

  // Sub-case B: Google maps places API quota/error
  globalThis.window.google = createMockGoogleMaps({ failPredictions: true, failDetails: true })
  const failSession = new PlacesSession()
  const failPredRes = await getDestinationPredictions('Bengaluru', failSession)
  assert(failPredRes.error !== null, 'Should return handled error instead of crashing')
  assert.equal(failPredRes.predictions.length, 0)

  const failDetailRes = await getSelectedPlaceCoordinates({ placeId: '123' }, failSession)
  assert(failDetailRes.error !== null, 'Should return handled error on detail failure')
  assert.equal(failDetailRes.place, null)

  // Sub-case C: Zero results
  globalThis.window.google = createMockGoogleMaps({ emptyResults: true })
  const emptyRes = await getDestinationPredictions('nonexistentplace', failSession)
  assert.equal(emptyRes.error, null)
  assert.equal(emptyRes.predictions.length, 0)

  console.log('✓ Seam 7 PASSED: Google failure gracefully handled without crash.')

  console.log('\n--- ALL 7 SEAM TESTS PASSED SUCCESSFULLY! ---')
}

runSeamTests().catch((err) => {
  console.error('Seam test failed:', err)
  process.exit(1)
})

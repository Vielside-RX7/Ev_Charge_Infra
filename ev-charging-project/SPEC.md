# Project Specification — AI-Powered Intelligent EV Charging Infrastructure

## Objectives

- Develop a driver-centric charger reliability score using historical success/failure records, downtime/fault events, maintenance records, user reports, and NLP-derived review information.

- Predict charger availability, occupancy, and expected waiting time using historical charging sessions, time/day, holidays, traffic, weather, and local events.

- Develop an intelligent recommendation engine considering battery state of charge, distance/route, charger compatibility, predicted reliability and occupancy, charging power, cost, and user preference.

- Integrate charging data, mapping/routing data, live context, and human feedback through a data and AI pipeline (cleaning, normalization, feature engineering, model training, validation, prediction).

- Design a safe charging testbed/emulator using ESP32 or Raspberry Pi, with sensing, relay interface, session simulation, network connectivity, and an OCPP-compatible test environment.

- Validate the approach by comparing conventional charger selection with AI-based recommendations using wait time, travel time, charging success, and energy-related performance.

## Tech Stack

- **Frontend:** React (Vite) with Tailwind CSS, React-Leaflet, Recharts
- **Backend:** FastAPI (Python)
- **ML/AI:** scikit-learn, XGBoost, LightGBM, Transformers (Hugging Face)
- **Database:** PostgreSQL (SQLAlchemy + psycopg2)
- **IoT/Testbed:** ESP32 / Raspberry Pi, OCPP, MQTT

## Database Schema & Auto-Seeding

Database: PostgreSQL 16, run via Docker Compose (see `docker-compose.yml`). Connection configured via `DATABASE_URL` in `.env`.

- **chargers** — EV charging station metadata, location, connector type, and power specs (382 records)
- **users** — Registered EV drivers with vehicle info and charging preferences (60 records)
- **charging_sessions** — Individual charging session records (energy, status, SoC, cost) (34,357 records)
- **reviews** — User reviews and NLP-derived sentiment scores for chargers (7,158 records)
- **faults** — Charger fault/downtime event reports and resolution tracking (2,235 records)
- **maintenance_logs** — Scheduled and unscheduled maintenance records per charger

### Database Seeding (`database/seed/init_seed.sql.gz`)
- **Teammate Zero-Configuration Auto-Init:** `docker-compose.yml` mounts `./database/seed:/docker-entrypoint-initdb.d:ro`. When a teammate runs `docker compose up -d` on a fresh machine, PostgreSQL automatically restores all 6 tables and 382 Karnataka charging stations from the pre-packaged gzip seed archive without requiring external API tokens or pipeline execution.
- **Archive Size:** 1.03 MB compressed (`.sql.gz`) / 5.17 MB uncompressed UTF-8 SQL.

## Data Ingestion

Source: [Open Charge Map API](https://openchargemap.org/site/develop/api).
Script: `pipeline/ingest_open_charge_map.py`.
Run after PostgreSQL is up and the database is initialised (`python database/init_db.py`).
Upserts charger POIs into the `chargers` table keyed on `external_id` (idempotent).

### Ingestion Scope & Spatial Filtering
- **Bounding-Box Retrieval:** Ingestion uses an axis-aligned spatial bounding box covering Karnataka state extents (`north=18.45, south=11.5, west=74.0, east=78.6`).
- **Post-Fetch State Normalization & Filtering:** Because Karnataka is geometrically non-rectangular, an axis-aligned bounding box inevitably captures border stations in adjacent states (Kerala, Tamil Nadu, Maharashtra, Telangana, Andhra Pradesh). Out-of-state records are explicitly filtered out post-fetch using the normalized `state` attribute, retaining only validated Karnataka charging stations.
- **Coverage & Data Source Disclosure:** Open Charge Map is a crowdsourced registry providing 382 verified charging station locations across Karnataka (major hubs including Bengaluru, Mysuru, Mangaluru, Belagavi, Hubballi, Udupi, etc.). This represents an open-access subset of the ~5,880 charging stations officially reported in Karnataka state statistics (Ministry of Power / Bureau of Energy Efficiency). This is an expected, documented design characteristic of the open data pipeline.

## Synthetic Data Generation

Script: `pipeline/generate_synthetic_data.py`. Addresses the data scarcity gap for Indian EV charging — real historical session/review/fault data is not publicly available, so realistic synthetic data is generated seeded from the real charger locations.

Each charger is assigned a hidden `true_reliability` value (Beta-distributed in [0.55, 0.98], skewed high). This is **not** stored as a DB column — it is only used internally during generation to bias session success rates, review ratings, and fault counts. This means downstream ML models (e.g. the reliability model) are training on data with a knowable ground-truth signal, enabling meaningful validation.

Run with `--reset` to wipe and regenerate: `python pipeline/generate_synthetic_data.py --reset`.

## Feature Engineering

Script: `pipeline/build_features.py`. Loads data from PostgreSQL, cleans it via `pipeline/clean_data.py`, and outputs two CSV files:

1. **`data/processed/charger_reliability_features.csv`** — One row per charger.
   Columns: `charger_id`, `num_sessions`, `num_success`, `num_failed`, `num_interrupted`, `success_rate`, `failure_rate`, `interrupted_rate`, `avg_energy_delivered_kwh`, `avg_session_duration_minutes`, `num_faults`, `num_resolved_faults`, `resolved_fault_ratio`, `days_since_last_fault`, `avg_rating`, `avg_sentiment_score`, `review_count`, `num_reviews_low` (rating <= 2), `num_reviews_high` (rating >= 4).

2. **`data/processed/charger_occupancy_features.csv`** — One row per (charger, day_of_week, day_time_block) bucket.
   Columns: `charger_id`, `day_of_week` (0=Mon..6=Sun), `day_time_block` (0-4), `session_count`, `avg_duration_minutes`, `is_weekend`.

> **Limitation:** `is_weekend` is used as a rough proxy for holiday-aware occupancy modelling. A real Indian holiday calendar can be integrated in a later phase without changing the output schema.

## Reliability Model

Script: `models/reliability_model.py`. Trains and validates machine learning regression models to predict charger reliability (`success_rate` in `[0.0, 1.0]`) on the expanded 382-charger Karnataka dataset using 10-fold Cross-Validation (with LOOCV maintained for historical comparison).

- **Chosen Model:** `RandomForestRegressor(n_estimators=100, max_depth=3, random_state=42)`
- **Primary Evaluation Metrics (10-Fold CV, n=382):**
  - **R² Score:** `0.5099`
  - **MAE:** `0.0427`
  - **Pearson Correlation (r):** `0.7141` (Predicted vs Actual `success_rate`)
- **LOOCV Comparison (n=382):** R² = `0.5174`, MAE = `0.0420`, r = `0.7195` (1.56s vs 50.12s runtime).
- **Compared Baseline:** `XGBRegressor` (10-Fold R² = `0.5085`, MAE = `0.0426`, r = `0.7145`)

### Features Used (11 features)
- `avg_session_duration_minutes` (successful sessions only)
- `num_faults` (73.8% feature importance)
- `avg_rating` (18.7% feature importance)
- `num_resolved_faults` (2.4% feature importance)
- `num_reviews_low` (rating <= 2, 1.4% importance)
- `days_since_last_fault` (NaN filled with `-1.0`, 1.4% importance)
- `resolved_fault_ratio`
- `review_count`
- `num_reviews_high` (rating >= 4)
- `has_no_faults` (derived boolean flag: `1` if no faults / NaN, else `0`)
- `avg_sentiment_score` (NLP-derived from user reviews, NaN filled with `-1.0`)

### Excluded & Dropped Columns
- `num_sessions`, `num_success`, `num_failed`, `num_interrupted`, `failure_rate`, `interrupted_rate` (directly encode or derive from the target variable `success_rate`).
- `avg_energy_delivered_kwh`: Formally evaluated via ablation testing across all 382 stations. Dropped from the final production model because it provided negligible predictive contribution ($\Delta R^2 \le 0.002$, $\Delta r \le 0.002$) while carrying documented historical leakage risk when calculated across mixed success/failure sessions.

### Saved Artifacts
- Model weights: `models/artifacts/reliability_model.pkl`
- Feature column order: `models/artifacts/reliability_model_features.json`
- Inference function: `predict_reliability(feature_dict: dict) -> float` in `models/reliability_model.py`

## Review Sentiment Scoring

Script: `pipeline/score_review_sentiment.py`. Evaluates unstructured user review texts stored in the `reviews` table using a pretrained transformer pipeline.

- **Model:** `distilbert-base-uncased-finetuned-sst-2-english` (HuggingFace Transformers)
- **Score Representation:** Continuous score in `[0.0, 1.0]` where:
  - `1.0`: Strongly positive
  - `0.0`: Strongly negative
  - `0.5`: Neutral
- **Validation Metric:** Achieves a Pearson correlation of **`r = +0.7760`** ($p = 4.24 \times 10^{-93}$) against user star ratings across all 457 reviews in the database, confirming the NLP model captures authentic text sentiment.
- **Inference Mode:** Batched processing (batch size 32), with `--rescore` flag for idempotent re-evaluations.

## Occupancy Model

Script: `models/occupancy_model.py`. Predicts charger utilization rate and availability probability across a complete 875-row grid (25 chargers $\times$ 7 days $\times$ 5 time blocks).

- **Granularity Switch (Bucket Sparsity Reduction):** To resolve extreme sparsity from spreading 2,036 sessions across 4,200 hourly cells, time is grouped into 5 coarse categorical blocks:
  - `0`: `overnight` (00:00 – 05:59)
  - `1`: `morning_peak` (06:00 – 09:59)
  - `2`: `midday` (10:00 – 15:59)
  - `3`: `evening_peak` (16:00 – 20:59)
  - `4`: `late_evening` (21:00 – 23:59)
- **Target Variable:** `utilization_rate` in `[0.0, 1.0]` ($= \min(1.0, (\text{session\_count} \times \text{avg\_duration\_minutes}) / (\text{block\_width\_minutes} \times \max(\text{num\_ports}, 1)))$).
- **Chosen Model:** `XGBRegressor(n_estimators=100, learning_rate=0.1, max_depth=4, random_state=42)`
- **Evaluation Metrics (80/20 Test Split & 5-Fold CV):**
  - **Test $R^2$ Score:** `0.4404`
  - **Test MAE:** `0.1484`
  - **5-Fold CV $R^2$:** `0.4643`
- **Compared Baseline:** `RandomForestRegressor` (Test $R^2$ = `0.4077`, MAE = `0.1511`, 5-Fold CV $R^2$ = `0.4342`)

### Features Used
- `charger_id` (station identifier)
- `day_of_week` (0=Monday .. 6=Sunday)
- `day_time_block` (0=overnight, 1=morning_peak, 2=midday, 3=evening_peak, 4=late_evening)
- `is_weekend` (binary flag: 1 for Saturday/Sunday, 0 otherwise)
- `num_ports` (hardware port capacity)
- `charging_power_kw` (charger maximum output power)

### Excluded Leakage Columns
`session_count`, `avg_duration_minutes` (directly compute the target `utilization_rate`).

### Saved Artifacts
- Model weights: `models/artifacts/occupancy_model.pkl`
- Feature column order: `models/artifacts/occupancy_model_features.json`
- Inference function: `predict_occupancy(feature_dict: dict) -> dict` returning `{"utilization_rate": float, "probability_available": float}` in `models/occupancy_model.py`

## Recommendation Engine

Script: `models/recommendation_engine.py`. Combines machine learning predictions (reliability and temporal availability) with geospatial distance, charging physics, and session economics into a unified multi-criteria optimization ranking score.

### Scoring Formula
$$\text{Final Score} = w_{\text{rel}} \cdot R + w_{\text{avail}} \cdot P_{\text{avail}} - w_{\text{dist}} \cdot \tilde{D} - w_{\text{cost}} \cdot \tilde{C} - w_{\text{time}} \cdot \tilde{T}$$

Where:
- $R \in [0.0, 1.0]$: Predicted station reliability score (from `reliability_model`).
- $P_{\text{avail}} \in [0.0, 1.0]$: Predicted probability of availability during the arrival time block (from `occupancy_model`).
- $\tilde{D} \in [0.0, 1.0]$: Min-Max normalized driving road distance (via OSRM) within the candidate set.
- $\tilde{C} \in [0.0, 1.0]$: Min-Max normalized estimated charging cost in INR (standard rate: ₹17.0/kWh).
- $\tilde{T} \in [0.0, 1.0]$: Min-Max normalized estimated charging duration in minutes (incorporating 17.5% CC-CV tapering overhead).

### Default Criteria Weights
| Component | Default Weight | Objective |
| :--- | :---: | :--- |
| `reliability` ($w_{\text{rel}}$) | `0.35` | Maximize probability of successful, fault-free session |
| `availability` ($w_{\text{avail}}$) | `0.25` | Maximize likelihood of immediate charger access |
| `distance` ($w_{\text{dist}}$) | `0.20` | Minimize travel deviation / driving road distance |
| `cost` ($w_{\text{cost}}$) | `0.10` | Minimize total charging tariff |
| `charging_time` ($w_{\text{time}}$) | `0.10` | Minimize total time spent at station (favors DC fast charging) |

### Full-Pool Normalization Bounds & Ranking Stability
- **Problem & Root Cause:** Min-max scaling for driving distance ($\tilde{D}$), charging time ($\tilde{T}$), and cost ($\tilde{C}$) was previously calculated using the min/max of the post-trim $K$ candidate set. Trimming distant outliers ($>20\text{ km}$) shrank the normalization denominator ($d_{\max} - d_{\min}$), which artificially steepened the distance penalty for mid-range stations. For example, in Mysuru ($N=22$), *Shell Recharge Mandakalli* (ID 13, $7.09\text{ km}$) ranked #3 in the full evaluation, but dropped out of the top 5 when the two farthest stations ($>22\text{ km}$) were trimmed from normalization.
- **Resolution:** The engine captures min/max bounds across the **FULL candidate set** immediately after radius and connector-compatibility filtering, *before* applying top-$K$ trimming. The $K$ candidate subset is then normalized against these locked-in full-pool statistics.
- **[0.0, 1.0] Factor Clamping:** Because actual driving road distance can exceed straight-line reference $d_{\max}$ near outer search boundaries due to road circuity ($\times 1.2\text{--}1.6$), normalized factors are strictly clamped to $[0.0, 1.0]$ via $\max(0.0, \min(1.0, \tilde{D}))$. This preserves documented weight semantics, ensuring that no single penalty factor (such as distance) can exceed its stated weight budget (e.g. $w_{\text{dist}} = 0.20$).
- **Architectural Principle:** The top-$K$ straight-line trim is strictly an **OSRM-routing-cost control mechanism**, not a candidate-elimination filter with hidden scoring side-effects. Ranking stability is now mathematically invariant to the $K$-trim boundary.

### Road-Based Routing & Fallback (Phase 12 & Optimization)
- **Fast Spatial Pre-Filter:** Great-circle Haversine distance is used as a fast first-pass spatial filter (`dist <= max_search_radius_km`).
- **Top-K Nearest Straight-Line Pre-Filter:** To bound external network requests in dense regions (e.g. Bengaluru with 100+ candidates in radius), active candidates are pre-filtered to the $K$ closest stations by straight-line distance, where:
  $$K = \max(20, \text{top\_n} \times 4)$$
  - *Tradeoff Disclosed:* Stations with slightly larger straight-line distance but exceptional road network connectivity could theoretically be excluded if they fall beyond rank $K$. With $K \ge 20$, this provides a $4\times$ candidate buffer for typical $\text{top\_n}=5$ queries while preventing $60\text{--}80\text{s}$ sequential network stalls.
- **Concurrent Road Distance & Duration:** `get_road_route()` calls OSRM (`http://router.project-osrm.org/route/v1/driving/{lon},{lat};{lon},{lat}`) concurrently using `concurrent.futures.ThreadPoolExecutor(max_workers=8)` to retrieve driving distance (`distance_km`) and travel duration (`travel_time_minutes`).
- **Public Demo Server Latency & Throttling:** Direct latency tracing confirmed that the remaining $\sim 3\text{--}6\text{s}$ query time in dense areas is an accepted, documented external limitation caused by server-side request throttling and queuing on the public demo instance (`router.project-osrm.org`) under concurrent load (individual request latency expands from $200\text{ms}$ up to $2.4\text{s}$ under $8$ parallel connections), rather than any client-side bottleneck.
- **Graceful Fallback:** If OSRM is unreachable, times out ($>3.0\text{s}$), or returns an error, the engine logs a warning and automatically falls back on an individual candidate level to $\text{Haversine} \times 1.3$ road distance approximation and estimated $\sim 30\text{ km/h}$ urban travel time.
- **Production Self-Hosting Note:** A production deployment would deploy a dedicated self-hosted OSRM container (or local C++ bindings), eliminating both public rate limits, internet round-trips, and the need for the straight-line top-K workaround (achieving true $<10\text{ms}$ routing across hundreds of stations).

### Connector Compatibility Fallback
- Strict filtering filters candidate stations matching the user's vehicle connector type (e.g. `CCS2`, `Type 2`).
- If **zero** compatible stations are available within the user's search radius, the engine gracefully falls back to returning the best-ranked stations in radius while setting `"compatible": false` on each result.

### Physical Reachability Hard Filter & Stranded Safeguard
- **Safety Problem:** Recommending high-scoring stations that the vehicle cannot physically reach with its remaining state-of-charge poses a critical real-world stranding risk for EV drivers.
- **Consumption Rate Assumption:** Uses a baseline fleet consumption rate of **`0.15 kWh/km` (150 Wh/km)**, representing the typical efficiency of popular Indian passenger EVs (Tata Nexon EV, Tiago EV, MG ZS EV) in mixed urban/highway driving conditions.
- **Safety Reserve Buffer:** Reserves a **`5.0%` unusable battery capacity buffer** (the standard BMS "turtle mode" threshold) to prevent cell deep-discharge damage and buffer against traffic delays:
  $$\text{Usable SoC} = \max(0.0, \text{SoC}_{\text{curr}} - 5.0\%)$$
  $$\text{Usable Range (km)} = \frac{\text{Usable SoC} \times \text{Battery Capacity (kWh)}}{0.15\text{ kWh/km}}$$
- **Hard Pre-Filter & Post-Route Pruning:** 
  1. Fast straight-line pre-check: excludes stations where $D_{\text{haversine}} > \text{Usable Range}$.
  2. Final confirmation: prunes any station whose actual OSRM driving road distance exceeds $\text{Usable Range}$.
  3. Unlike distance weighting ($20\%$), reachability is a **hard binary feasibility gate**, guaranteeing that unreachable stations are never recommended.
- **Dedicated Stranded Exception (`VehicleStrandedException`):** If the vehicle's remaining charge cannot safely reach *any* charging station within search radius, the API raises `HTTP 400 Bad Request` with error code `VEHICLE_STRANDED`, providing exact usable range, distance to the nearest station, and actionable emergency roadside charging guidance.

### Ranking Function
`score_and_rank_chargers(...) -> list[dict]` in `models/recommendation_engine.py`.

## API Layer

Script: `api/main.py`. High-performance RESTful API service built with FastAPI and Pydantic V2, wrapping the recommendation engine and ML models.

### Endpoints

#### 1. `GET /health`
Performs a readiness and liveness check verifying PostgreSQL connectivity and pre-warmed charger feature cache.
- **Response (`200 OK`):**
  ```json
  {
    "status": "ok",
    "database": "connected",
    "total_chargers_loaded": 25
  }
  ```
- **Error (`503 Service Unavailable`):** If database or feature artifacts cannot be loaded.

#### 2. `POST /recommend`
Generates ranked EV charging recommendations for a given vehicle state, location, and target charge.
- **Request Body (`RecommendationRequest`):**
  ```json
  {
    "user_lat": 12.2958,
    "user_lon": 76.6394,
    "connector_type": "CCS2",
    "battery_capacity_kwh": 30.0,
    "current_soc_percent": 20.0,
    "target_soc_percent": 90.0,
    "arrival_datetime": "2026-08-25T16:00:00",
    "max_search_radius_km": 25.0,
    "top_n": 5
  }
  ```
- **Validation Rules (Pydantic V2):**
  - `user_lat`, `user_lon`: Valid floating point coordinates (required).
  - `connector_type`: Non-empty string (required).
  - `battery_capacity_kwh`: Strict positive float (`> 0.0`).
  - `current_soc_percent`, `target_soc_percent`: Bound in `[0.0, 100.0]`.
  - `target_soc_percent` must be strictly greater than `current_soc_percent`.
  - `max_search_radius_km`: Strict positive float (`> 0.0`, default `25.0`).
  - `top_n`: Integer between `1` and `25` (default `5`).
- **Response (`200 OK` — `List[ChargerRecommendation]`):**
  ```json
  [
    {
      "charger_id": 7,
      "name": "Grand Mercure Mysore",
      "operator": "Zeon Charging",
      "address": "2203 60 New Sayyaji Rao Road, Nelson Mandela Road",
      "city": "Mysuru",
      "latitude": 12.3307,
      "longitude": 76.64479,
      "charging_power_kw": 120.0,
      "num_ports": 2,
      "connector_type": "CCS (Type 2)",
      "distance_km": 5.02,
      "travel_time_minutes": 5.6,
      "reliability": 0.9061,
      "probability_available": 0.9478,
      "estimated_charging_time_minutes": 12.3,
      "estimated_cost_inr": 357.0,
      "compatible": true,
      "normalized_distance": 0.1734,
      "normalized_cost": 0.0,
      "normalized_charging_time": 0.0,
      "final_score": 0.5325
    }
  ]
  ```
- **Error Codes:**
  - `422 Unprocessable Entity`: Validation failures (e.g. invalid SoC, missing required fields).
  - `400 Bad Request`: `VEHICLE_STRANDED` when usable charge cannot reach any candidate.
  - `404 Not Found`: No stations found within the specified search radius.
  - `500 Internal Server Error`: Unhandled model inference or system errors.

#### 3. `POST /route`
Fetches on-demand road driving route geometry (GeoJSON LineString), distance, and duration between origin and destination charger.
- **Request Body (`RouteRequest`):**
  ```json
  {
    "user_lat": 12.2958,
    "user_lon": 76.6394,
    "charger_id": 7
  }
  ```
- **Response (`200 OK` — `RouteResponse`):**
  ```json
  {
    "charger_id": 7,
    "charger_name": "Grand Mercure Mysore",
    "distance_km": 5.02,
    "travel_time_minutes": 5.6,
    "geometry": {
      "type": "LineString",
      "coordinates": [
        [76.639391, 12.295732],
        [76.639548, 12.295713]
      ]
    },
    "is_fallback": false
  }
  ```

### Local Execution
```bash
uvicorn api.main:app --reload --port 8000
```
Interactive OpenAPI documentation is automatically served at `http://localhost:8000/docs`.

## Dashboard (Frontend)

Directory: `dashboard/`. Responsive single-page application built with React (`^18.3.1`), Leaflet (`^1.9.4`), `react-leaflet` (`^4.2.1`), Vite, and Tailwind CSS.

### Architecture & Components

1. **`dashboard/src/components/RecommendationForm.jsx`**:
   - Trip & vehicle configuration form (`user_lat`, `user_lon`, `connector_type`, `battery_capacity_kwh`, `current_soc_percent`, `target_soc_percent`, `max_search_radius_km`, `top_n`).
   - Browser Geolocation API integration ("Use My Location") with graceful fallback.
   - Comprehensive client-side validation mirroring backend rules (`target_soc > current_soc`, SoC bounds `0–100%`, positive radius/capacity).

2. **`dashboard/src/components/MapView.jsx`**:
   - Leaflet interactive geospatial map powered by `react-leaflet` (`^4.2.1`).
   - OpenStreetMap public tile server with required attribution (`&copy; OpenStreetMap contributors`).
   - Custom `L.divIcon` markers for user location and numbered chargers.
   - Interactive popups with station metadata and quick "Navigate" trigger.
   - Auto-fit bounds via `map.fitBounds()` on search results update.

3. **`dashboard/src/components/ResultsList.jsx`**:
   - Renders ranked station recommendation cards.
   - Station metadata: rank badge, power rating (kW), distance (km), reliability percentage, availability percentage, estimated duration (min), estimated tariff (₹), and compatibility flags.
   - "🚗 Start Navigation" button initiating full-screen navigation mode.
   - Loading skeletons and categorized error states (`400 Stranded`, `404 Not Found`, `422 Validation Error`, `Network Unreachable`).

4. **`dashboard/src/components/NavigationView.jsx` (Option 1 Live Navigation View)**:
   - Full-screen map overlay triggered when navigating to a chosen charger.
   - Fetches and renders static road driving route polyline via `POST /route` with dual-layer visual casing.
   - Continuous live GPS position tracking using `navigator.geolocation.watchPosition()`.
   - Distinct animated navigation puck marker with pulsing halo and orientation tracking.
   - Smooth map following (`map.panTo`) keeping the vehicle centered, with auto-follow suspension on manual pan.
   - Built-in movement simulator ("▶ Simulate Drive") for desktop testing and validation.
   - Graceful fallback for permission denial with helpful advisory banner.
   - Floating navigation HUD with destination details, remaining distance, ETA, and clean "Exit Navigation" button.

5. **`dashboard/src/App.jsx`**:
   - Top-level state coordinator managing search inputs, recommendations, selected charger, and active navigation mode.

### Explicit Scope Boundaries & Future Phases
- **Current Scope (Option 1):** Moving live location on a fixed, static route polyline.
- **Explicitly Deferred to Future Phases:**
  - Off-route detection and trajectory divergence triggers.
  - Automatic dynamic re-routing.
  - Turn-by-turn voice and textual instruction queue.
  - User accounts and saved trip histories.




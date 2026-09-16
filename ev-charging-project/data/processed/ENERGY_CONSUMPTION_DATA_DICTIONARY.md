# Energy Consumption Features Data Dictionary

**Dataset File**: `data/processed/energy_consumption_features.csv`  
**Pipeline Script**: `pipeline/generate_energy_dataset.py`  
**Validation Script**: `pipeline/validate_energy_dataset.py`  
**Designation**: Synthetic Virtual EV Trip Dataset (Phase 12 Data Collection)  
**Baseline Model**: 0.150 kWh/km production Virtual EV consumption baseline with physical operating variations  

---

## Overview

This dataset captures simulated trips executed by the Virtual EV. It separates **Input Features** (vehicle specifications, starting state, road distance, speed, elevation, and environmental context) from the **Target Variable** (`energy_consumed_kwh`).

It is structured specifically for training and benchmarking downstream battery energy prediction models (such as Linear Regression, Random Forest, XGBoost, or Neural Networks) without target leakage.

---

## Column Specifications

| Column Name | Data Type | Units / Format | Role | Source | Description & Generation Details |
|---|---|---|---|---|---|
| `trip_id` | String | Identifier (`SIM_TRIP_XXXX`) | Identifier | Generator | Unique identifier for each simulated trip session. |
| `vehicle_id` | String | String (`VIRTUAL_EV_001`, `VIRTUAL_EV_002`) | Input Feature | Vehicle Profile | Active vehicle identifier. Corresponds to the active profile in `vehicleProfile.js`. |
| `battery_capacity_kwh` | Float | kWh | Input Feature | Vehicle Profile | Total nominal battery capacity (e.g. 30.0 kWh for Nexon EV Max baseline). |
| `starting_soc_percent` | Float | % (0.0 – 100.0) | Input Feature | Telemetry | State of Charge at the moment the trip begins. Uniformly sampled in realistic driving range (20.0% – 95.0%). |
| `distance_km` | Float | Kilometers (km) | Input Feature | Route Data | Total road distance traversed along the OSRM route. Sampled from urban (2–15 km) and intercity (15–65 km) driving patterns. |
| `trip_duration_minutes` | Float | Minutes | Input Feature | Route / Simulation | Elapsed trip travel time. Derived from road distance and average speed, plus simulated traffic delays. |
| `average_speed_kmph` | Float | km/h | Input Feature | Telemetry / Route | Average vehicle speed across the trip. Inversely correlated with traffic congestion ($15.0 - 85.0$ km/h). |
| `elevation_gain_m` | Float | Meters (m) | Input Feature | Route Data | Net elevation change along route profile ($-120.0$ to $+180.0$ m). Influences potential energy drain / regeneration. |
| `traffic_density_score` | Float | Score ($0.0 - 1.0$) | Input Feature | Synthetic Context | Simulated road congestion index ($0.0$ = free flow, $1.0$ = severe stop-and-go gridlock). Beta distribution ($\alpha=2, \beta=4$). |
| `ambient_temperature_c` | Float | Celsius (°C) | Input Feature | Synthetic Context | Ambient atmospheric temperature ($15.0 - 42.0$ °C). Influences auxiliary HVAC / cabin cooling load. |
| `weather_condition` | String | Categorical (`Clear`, `Rain`, `Hot`, `Overcast`) | Input Feature | Synthetic Context | Macro weather condition during the trip. |
| `driving_style` | String | Categorical (`Eco`, `Normal`, `Aggressive`) | Input Feature | Driver Context | Throttle aggressiveness and regeneration profile: Eco ($0.92\times$), Normal ($1.00\times$), Aggressive ($1.15\times$). |
| `energy_consumed_kwh` | Float | kWh | **TARGET** | Telemetry / Physics Model | **Target label**: Net battery energy consumed during the trip. Derived from 0.150 kWh/km baseline adjusted for aerodynamics, elevation, traffic, and HVAC. Strictly non-negative. |
| `data_type` | String | Constant (`synthetic_virtual_ev`) | Metadata | Generator | Clearly flags that rows are synthetic simulation data, not raw physical vehicle hardware measurements. |

---

## Target Formulation & Physics Assumptions

The target variable `energy_consumed_kwh` is derived deterministically from the 0.150 kWh/km simulation baseline with physical corrections:

$$\text{Energy}_{\text{total}} = \left(\text{Distance} \times 0.150 \times f_{\text{style}} \times f_{\text{aero}}\right) + E_{\text{elevation}} + E_{\text{traffic}} + E_{\text{HVAC}} + \epsilon$$

1. **Baseline**: $0.150\text{ kWh/km}$ baseline consumption.
2. **Driving Style Factor ($f_{\text{style}}$)**:
   - `Eco`: $0.92$ ($-8\%$ consumption via gentle acceleration and maximum regen).
   - `Normal`: $1.00$ (baseline).
   - `Aggressive`: $1.15$ ($+15\%$ consumption via hard acceleration and friction brake losses).
3. **Aerodynamic Drag Factor ($f_{\text{aero}}$)**:
   - Calibrated around $45\text{ km/h}$. For speeds $>50\text{ km/h}$, adds quadratic drag penalty $+0.0035 \times (\text{speed} - 50)$.
4. **Elevation Potential Energy ($E_{\text{elevation}}$)**:
   - Ascent ($\Delta h > 0$): $+ \frac{m \cdot g \cdot \Delta h}{3.6 \times 10^6 \times 0.85}\text{ kWh}$ ($\approx +0.0049\text{ kWh/m}$).
   - Descent ($\Delta h < 0$): $- \frac{m \cdot g \cdot |\Delta h| \times 0.65}{3.6 \times 10^6}\text{ kWh}$ ($\approx -0.0028\text{ kWh/m}$).
5. **Traffic Friction Loss ($E_{\text{traffic}}$)**:
   - $+0.015 \times \text{traffic\_density} \times \text{distance\_km}$.
6. **Auxiliary HVAC Load ($E_{\text{HVAC}}$)**:
   - For temperatures $>25^\circ\text{C}$, air conditioning power draw is $0.06 \times (T_{\text{ambient}} - 25)\text{ kW}$ multiplied by trip duration.
7. **Sensor Noise ($\epsilon$)**:
   - Small Gaussian noise $\sim \mathcal{N}(0, 0.015)$ reflecting real-world OBD current sensor tolerance.

---

## Strict Constraints & Leakage Safeguards

- **No Future Information in Features**: All input features represent pre-trip or macroscopic route conditions (distance, speed estimate, weather, elevation, driving style).
- **Target Separation**: `energy_consumed_kwh` is the only target column.
- **Physical Boundaries**: Energy is constrained to $\ge 0.05\text{ kWh}$. Negative energy and negative distances are strictly prevented.

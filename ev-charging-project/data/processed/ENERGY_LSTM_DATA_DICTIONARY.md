# Virtual EV LSTM Sequential Energy Dataset — Data Dictionary

**Artifact Files**:
- Compressed Arrays: `data/processed/energy_lstm_sequences.npz`
- Preprocessing Scaler: `models/artifacts/energy_lstm_scaler.pkl`
- Dataset Metadata: `models/artifacts/energy_lstm_dataset_metadata.json`
- Generator Script: `pipeline/generate_lstm_energy_dataset.py`
- Validation Script: `pipeline/validate_lstm_energy_dataset.py`

**Designation**: Synthetic Virtual EV Sequential Driving Telemetry (Phase 15 Dataset Preparation)  
**Physics Baseline**: 0.150 kWh/km production Virtual EV consumption baseline with aerodynamic, topographic, traffic, and HVAC corrections.

---

## 1. Research Note: Tabular vs. Sequential Energy Modeling

### Fundamental Architectural Distinction
| Model Family | Dataset File | Unit of Observation | Sample Representation |
|---|---|---|---|
| **Tabular Models** (Linear Regression, XGBoost) | `data/processed/energy_consumption_features.csv` | **One row = One entire trip** | Aggregated trip summary features (total distance, average speed, net elevation, macro weather, overall trip energy). |
| **Recurrent Model** (LSTM) | `data/processed/energy_lstm_sequences.npz` | **One sample = Sequence of ordered telemetry timesteps** | 3D tensor `(samples, timesteps, features)` capturing temporal dynamics, micro-accelerations, speed fluctuations, and incremental battery draw over time. |

### Why Separate Datasets?
Tabular regressors operate on static trip-level summaries ($X \in \mathbb{R}^D$), whereas recurrent neural networks (LSTMs) model temporal dependencies across continuous driving sequences ($X \in \mathbb{R}^{L \times D}$). Tabular models cannot model step-by-step driving dynamics, while LSTMs require sliding observation windows with strict chronological ordering.

### Synthetic Data Transparency Disclosure
> [!IMPORTANT]
> **SIMULATED DATA DISCLOSURE**:  
> All telemetry traces and energy values in this dataset are generated synthetically using the project's Virtual EV simulation architecture (`tripRecorder.js` and `virtualEvModel.js`). While the physics equations model real-world vehicle dynamics (aerodynamic drag, gravity potential energy, rolling resistance, traffic friction, and HVAC draw), these records **do not constitute physical hardware measurements from an actual road EV**.

---

## 2. Target Formulation & Anti-Leakage Protocol

### Target Definition: `target_future_energy_kwh`
The target variable $y$ represents the **net battery energy (kWh) consumed over the subsequent prediction horizon $H$** immediately following the observed sequence window:

$$y_i = \sum_{j=1}^{H} \Delta e_{t_N + j} = E_{\text{consumed}}[t_N + H] - E_{\text{consumed}}[t_N]$$

Where:
- Observed Sequence: $t_1, t_2, \dots, t_N$ ($L = 20$ timesteps $= 100\text{ seconds}$).
- Prediction Horizon: $t_{N+1}, \dots, t_{N+H}$ ($H = 6$ timesteps $= 30\text{ seconds}$).
- $\Delta e_t$: Incremental energy consumed during timestep $t$.

### Target Leakage Prevention
1. **No Future Values in Inputs**: The input sequence $X$ strictly terminates at timestep $t_N$. No telemetry from $t_{N+1} \dots t_{N+H}$ appears in $X$.
2. **Cumulative Energy Isolation**: Rather than feeding unbounded cumulative trip energy (which would drift and create future leakage), each timestep provides instantaneous step energy ($\Delta e_t$) and step distance ($\Delta d_t$).
3. **Strict Target Separation**: The target variable name and future values do not exist anywhere inside $X$.

---

## 3. Sequence Windowing & Sampling Parameters

| Parameter | Symbol | Value | Real-Time Equivalence | Description |
|---|---|---|---|---|
| **Sampling Interval** | $\Delta t$ | $5.0\text{ s}$ | $0.2\text{ Hz}$ | Time duration between successive telemetry observations. |
| **Sequence Length** | $L$ | $20\text{ steps}$ | $100.0\text{ s}$ | Number of historical timesteps in the LSTM input sequence. |
| **Prediction Horizon** | $H$ | $6\text{ steps}$ | $30.0\text{ s}$ | Duration into the future over which energy consumption is predicted. |
| **Stride** | $S$ | $2\text{ steps}$ | $10.0\text{ s}$ | Step increment between consecutive sliding window extractions. |
| **Trip Crossing** | — | **Prohibited** | — | Windows are generated strictly within individual trips; zero crossing of trip boundaries. |

---

## 4. Timestep Feature Schema (12 Features)

Every timestep $t \in [1, L]$ within an input sequence contains 12 normalized variables:

| Feature Index | Feature Name | Data Type | Units / Range | Category | Physical Description |
|---|---|---|---|---|---|
| `0` | `speed_kmph` | Float | $\text{km/h}$ ($0 - 110$) | Dynamic Telemetry | Instantaneous vehicle ground speed at this timestep. |
| `1` | `latitude` | Float | Decimal degrees | Dynamic Telemetry | GPS vehicle latitude along simulated route. |
| `2` | `longitude` | Float | Decimal degrees | Dynamic Telemetry | GPS vehicle longitude along simulated route. |
| `3` | `soc_percent` | Float | % ($0.0 - 100.0$) | Dynamic Telemetry | Active battery State of Charge at this timestep. |
| `4` | `step_distance_km` | Float | $\text{km}$ | Dynamic Telemetry | Distance traversed during the $5.0\text{s}$ timestep interval. |
| `5` | `step_energy_kwh` | Float | $\text{kWh}$ | Dynamic Telemetry | Energy consumed by vehicle during the $5.0\text{s}$ timestep interval. |
| `6` | `elevation_m` | Float | Meters ASL | Dynamic Context | Absolute terrain elevation along road profile. |
| `7` | `elevation_delta_m` | Float | Meters ($\Delta h$) | Dynamic Context | Elevation change in this step (positive = uphill, negative = downhill). |
| `8` | `traffic_density_score` | Float | Score ($0.0 - 1.0$) | Dynamic Context | Instantaneous traffic congestion index at vehicle location. |
| `9` | `ambient_temperature_c` | Float | $^\circ\text{C}$ ($15.0 - 42.0$) | Static/Context | Ambient atmospheric temperature influencing HVAC power. |
| `10` | `battery_capacity_kwh` | Float | $\text{kWh}$ ($30.0, 40.5$) | Static Vehicle | Total nominal battery capacity of active vehicle profile. |
| `11` | `driving_style_encoded` | Float | Ordinal ($0, 1, 2$) | Static Driver | Throttle profile: Eco ($0.0$), Normal ($1.0$), Aggressive ($2.0$). |

---

## 5. Train / Validation / Test Splitting Strategy

### Trip-Level Disjoint Partition
To eliminate **data leakage**, telemetry rows are **never** partitioned randomly:
1. All $1,200$ simulated trips are partitioned at the **trip level**:
   - **Training Set**: $70\%$ ($840$ trips)
   - **Validation Set**: $15\%$ ($180$ trips)
   - **Test Set**: $15\%$ ($180$ trips)
2. Trip ID sets are strictly mutually exclusive ($\text{Train} \cap \text{Val} = \emptyset$, $\text{Train} \cap \text{Test} = \emptyset$, $\text{Val} \cap \text{Test} = \emptyset$).
3. Sequence windows are extracted **independently inside each partition**. Telemetry from the same trip never appears in more than one split.

---

## 6. Preprocessing & Normalization Protocol

- **Scaler**: `sklearn.preprocessing.StandardScaler` (zero mean, unit variance).
- **Strict Isolation**: Scaler is fitted **strictly on the flattened training sequences** ($X_{\text{train}} \in \mathbb{R}^{N_{\text{train}} \times L \times D} \to \mathbb{R}^{(N_{\text{train}} \cdot L) \times D}$).
- **Transformation**: Validation and test sets ($X_{\text{val}}$, $X_{\text{test}}$) are transformed using the training-fitted scaler parameters.
- **Persistence**: Fitted scaler is serialized to `models/artifacts/energy_lstm_scaler.pkl` and full parameters are logged in `models/artifacts/energy_lstm_dataset_metadata.json`.

---

## 7. How to Load and Inspect the Dataset

```python
import numpy as np
import joblib
import json

# 1. Load compressed arrays
data = np.load("data/processed/energy_lstm_sequences.npz")
X_train = data["X_train"]  # (samples, 20, 12)
y_train = data["y_train"]  # (samples,)
X_val   = data["X_val"]    # (samples, 20, 12)
y_val   = data["y_val"]    # (samples,)
X_test  = data["X_test"]   # (samples, 20, 12)
y_test  = data["y_test"]   # (samples,)

print("Train shape:", X_train.shape, y_train.shape)
print("Val shape:  ", X_val.shape, y_val.shape)
print("Test shape: ", X_test.shape, y_test.shape)

# 2. Load scaler bundle
scaler_bundle = joblib.load("models/artifacts/energy_lstm_scaler.pkl")
scaler = scaler_bundle["scaler"]
features = scaler_bundle["feature_names"]

# 3. Load metadata
with open("models/artifacts/energy_lstm_dataset_metadata.json") as f:
    meta = json.load(f)
print("Target description:", meta["target_definition"]["description"])
```

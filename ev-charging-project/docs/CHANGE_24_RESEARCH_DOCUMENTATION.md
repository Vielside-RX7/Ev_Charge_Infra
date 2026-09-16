# Research Documentation: Change 24 — AI-Informed Journey Planning

## 1. Executive Summary & Objective

Prior to Change 24, machine learning energy models (XGBoost whole-trip predictor and LSTM near-term predictor) operated purely as an advisory telemetry layer, displayed separately in the user interface but decoupled from candidate route selection and charging stop optimization.

**Change 24 achieves AI-informed route planning**: Machine-learning whole-trip energy prediction directly participates in candidate route and charging-stop evaluation and cost ranking, while the deterministic physical battery model ($0.150\text{ kWh/km}$) remains strictly authoritative for battery feasibility checks and Virtual EV state updates.

---

## 2. Why XGBoost Is Used at Candidate-Route Level (Not Per-Edge)

### Whole-Trip Semantic Validity
The trained XGBoost energy prediction model (`energy_xgboost_model.pkl`) was trained on macroscopic trip features:
- Total trip distance ($D$)
- Estimated trip duration ($T_{\text{drive}}$)
- Average vehicle speed ($\bar{v} = D / T$)
- Elevation gain ($h_{\text{gain}}$)
- Battery capacity ($C_{\text{batt}}$)
- Ambient temperature ($T_{\text{amb}}$)
- Traffic density score ($S_{\text{traffic}}$)

These features inherently describe an **entire end-to-end journey or journey leg**. They do not describe a 20-meter micro-segment of an urban road graph.

### Mathematical Fallacies Avoided
1. **No Per-Edge Energy Projection**: Feeding whole-trip macroscopic features into individual graph edges in Modified A* would violate edge-independence assumptions and produce invalid per-edge cost estimates.
2. **Preservation of A* Admissibility**: Modified A* graph search uses an admissible Euclidean straight-line distance heuristic ($h(n) = \frac{w_d}{D_{\text{max}}} \cdot d_{\text{straight}}(n, g)$) to find optimal Pareto paths on the physical road network. Injecting non-admissible ML predictions into graph edges would corrupt heuristic admissibility and optimality guarantees.
3. **Correct Architectural Placement**: XGBoost is evaluated at the candidate route / journey plan layer:
   - For a direct candidate: evaluated over the complete origin-to-destination path.
   - For a charging candidate: evaluated over the combined multi-leg journey ($O \to C \to D$).

---

## 3. Mathematical Formulation of AI-Informed Route Cost

Candidate journey alternatives are evaluated using the Change 18A Multi-Objective Cost Function:

$$C = w_d \cdot D_{\text{norm}} + w_e \cdot E_{\text{norm}} + w_t \cdot T_{\text{norm}} + w_w \cdot W_{\text{norm}} + w_c \cdot C_{\text{norm}}$$

Subject to weights $\sum w_i = 1.0$ and min-max normalization bounds:
- $D_{\text{norm}} = \text{clamp}\left(\frac{D - D_{\text{min}}}{D_{\text{max}} - D_{\text{min}}}, 0, 1\right)$
- $E_{\text{norm}} = \text{clamp}\left(\frac{E - E_{\text{min}}}{E_{\text{max}} - E_{\text{min}}}, 0, 1\right)$
- $T_{\text{norm}} = \text{clamp}\left(\frac{T - T_{\text{min}}}{T_{\text{max}} - T_{\text{min}}}, 0, 1\right)$
- $W_{\text{norm}} = \text{clamp}\left(\frac{W - W_{\text{min}}}{W_{\text{max}} - W_{\text{min}}}, 0, 1\right)$
- $C_{\text{norm}} = \text{clamp}\left(\frac{C - C_{\text{min}}}{C_{\text{max}} - C_{\text{min}}}, 0, 1\right)$

### AI Energy Substitution in Candidate Evaluation
When whole-trip ML energy prediction is active:
$$E = E_{\text{AI}} = \text{XGBoost}(D, T_{\text{drive}}, \bar{v}, \text{SoC}, C_{\text{batt}})$$

If the AI energy predictor is unavailable or encounters an error, the system seamlessly falls back to the deterministic physical baseline:
$$E = E_{\text{baseline}} = D \times 0.150\text{ kWh/km}$$

Because $E_{\text{AI}}$ enters the normalized energy term $E_{\text{norm}}$ directly, candidates that encounter favorable elevation, lower traffic friction, or optimal speed corridors receive a lower normalized energy cost ($w_e \cdot E_{\text{norm}}$), directly influencing candidate ranking between otherwise feasible alternatives.

---

## 4. Authoritative Physics & The Hard Feasibility Constraint

### Core Architectural Principle
**ML predictions must NEVER directly mutate vehicle battery telemetry or override physical feasibility.**

### The Hard Safety Rule
Let:
- $E_{\text{usable}} = \text{BatteryCapacity} \times \frac{\text{CurrentSoC} - \text{ReserveSoC}}{100}$
- $E_{\text{baseline}} = D \times 0.150\text{ kWh/km}$
- $E_{\text{AI}} = \text{XGBoost prediction}$

**Hard Feasibility Check**:
$$\text{Feasible}_{\text{physical}} = (E_{\text{baseline}} \le E_{\text{usable}})$$

Even if an optimistic ML prediction estimates $E_{\text{AI}} < E_{\text{usable}}$, if $E_{\text{baseline}} > E_{\text{usable}}$, the route is **strictly rejected as physically infeasible**:

$$\text{Decision} = \begin{cases} 
\text{INFEASIBLE} & \text{if } E_{\text{baseline}} > E_{\text{usable}} \\
\text{Evaluate candidate cost } C(E_{\text{AI}}) & \text{if } E_{\text{baseline}} \le E_{\text{usable}}
\end{cases}$$

### Telemetry Simulation State
During active driving simulation (Change 23 Navigation Mode), the Virtual EV battery state strictly integrates the deterministic physical model:
$$\Delta \text{Energy}_{\text{consumed}} = \Delta D \times 0.150\text{ kWh/km}$$
$$\text{SoC}(t + \Delta t) = \text{SoC}(t) - \frac{\Delta \text{Energy}_{\text{consumed}}}{\text{BatteryCapacity}} \times 100$$

The ML model provides non-mutating advisory and planning intelligence. It never mutates battery telemetry.

---

## 5. Architectural Boundary: XGBoost vs. LSTM

| Attribute | XGBoost Whole-Trip Predictor | LSTM Near-Term Predictor |
|---|---|---|
| **Role** | Macroscopic Journey Planning & Candidate Ranking | Real-Time Driving Dynamics & Advisory Horizon |
| **Time Horizon** | Full trip ($10\text{ min} - 5\text{ hours}$, complete route) | Next 30 seconds ($k = 6$ sequential steps) |
| **Input Data** | Static trip summary (Distance, Duration, Avg Speed) | Sliding window of 20 sequential telemetry samples (100 Hz resampled to 5s steps: speed, acceleration, grade, aux power) |
| **Invocation** | At trip planning time (`POST /trip/plan`) | Continuously during driving simulation |
| **Output** | Whole-trip energy estimate ($E_{\text{AI}}$ in kWh) | Near-term energy delta ($\Delta E_{\text{30s}}$ in kWh) |
| **Planning Impact** | Modulates candidate ranking cost | Advisory HUD display only (no route re-routing) |

---

## 6. Controlled Scenario: AI Changes Route Preference

Consider an EV navigating from Origin to Destination (direct route infeasible):
- Usable battery: $4.5\text{ kWh}$
- Candidate Charger A: Total distance $70\text{ km}$ ($20\text{ km} + 50\text{ km}$), baseline energy $10.5\text{ kWh}$.
- Candidate Charger B: Total distance $72\text{ km}$ ($22\text{ km} + 50\text{ km}$), baseline energy $10.8\text{ kWh}$.
- Identical charger hardware: $50\text{ kW}$ DC, $95\%$ reliability, $90\%$ availability.

### Outcome under Baseline Planning (Physics-Only)
- Distance: Candidate A ($70\text{ km}$) < Candidate B ($72\text{ km}$)
- Baseline Energy: Candidate A ($10.5\text{ kWh}$) < Candidate B ($10.8\text{ kWh}$)
- **Selected**: **Charger A**

### Outcome under AI-Informed Planning
- Route A traverses steep uphill highway terrain: XGBoost predicts $15.0\text{ kWh}$ ($+42.9\%$ vs baseline).
- Route B traverses a gentle gradient eco corridor: XGBoost predicts $7.5\text{ kWh}$ ($-30.6\%$ vs baseline).
- Because energy weight $w_e = 0.30$, the significant reduction in normalized energy consumption ($7.5\text{ kWh}$ vs $15.0\text{ kWh}$) outweighs the minor 2 km distance difference.
- **Selected**: **Charger B**

---

## 7. Synthetic Data Limitations & Future Real-Vehicle Telematics

### Synthetic Training Data Limitation (Honest Disclosure)
The current XGBoost model was trained on synthetically generated vehicle driving cycles seeded from Karnataka road geometries and simulated vehicle dynamics. While thermodynamically and statistically sound:
- It reflects synthetic driving cycle distributions, not real CAN bus logging.
- Prediction accuracy metrics ($R^2$, MAE) apply to the synthetic test split, not real-world EV fleets.
- It must be understood as an architectural prototype demonstrating AI-informed optimization rather than a calibrated OEM-grade production system.

### Future Upgrade Path with Real Vehicle Telematics
In an OEM or production deployment, the current synthetic pipeline would be directly replaced with:
1. **OBD-II / CAN Bus High-Frequency Telemetry**: Real battery current, voltage, cell temperature, auxiliary HVAC load, and regenerative braking power.
2. **High-Resolution Digital Elevation Models (DEM)**: True road grade percentages from LiDAR or satellite elevation maps.
3. **Live Connected Vehicle Fleets**: Continuous online training on real driving profiles across diverse traffic conditions.
4. **Hardware-in-the-Loop Validation**: Real battery test benches validating degradation and temperature-dependent internal resistance.

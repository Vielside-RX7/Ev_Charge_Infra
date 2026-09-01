# AI-Powered Intelligent EV Charging Infrastructure

An AI-powered EV charging recommendation platform designed for India's growing electric vehicle ecosystem. The system combines predictive charger reliability scoring, real-time occupancy and wait-time forecasting, NLP-based review analysis, and intelligent route-aware recommendation to help EV drivers find the best available charger — minimizing wait times, maximizing charging success rates, and optimizing energy costs. The platform integrates historical charging data, live context (traffic, weather, events), and user feedback through a unified data and AI pipeline, with a hardware testbed for protocol-level validation using OCPP.

## Folder Structure

```
ev-charging-project/
├── data/                  # Raw and processed datasets
├── pipeline/              # Data cleaning and feature engineering scripts
├── models/                # ML training scripts and saved model files
│   ├── reliability_model.py    # Charger reliability scoring model
│   ├── occupancy_model.py      # Occupancy and wait-time prediction model
│   └── nlp_review_model.py     # NLP-based user review analysis model
├── api/                   # FastAPI backend application
│   └── main.py                 # API entry point
├── testbed/               # ESP32/Raspberry Pi firmware and OCPP client
├── dashboard/             # React (Vite) visualization dashboard
│   └── src/App.jsx             # Dashboard entry point
├── database/              # Database schema and migration scripts
├── SPEC.md                # Full project objectives and specification
├── requirements.txt       # Python dependencies
├── .gitignore             # Git ignore rules
└── README.md              # This file
```

## Setup Instructions

### 1. Clone the repository

```bash
git clone <repo-url>
cd ev-charging-project
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

- **Windows**: `venv\Scripts\activate`
- **Linux/macOS**: `source venv/bin/activate`

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Database setup

Start the PostgreSQL container, copy the environment file, and initialise the schema:

```bash
docker compose up -d
cp .env.example .env
python database/init_db.py
```

### 5. Run the FastAPI server

```bash
uvicorn api.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`. Interactive docs at `http://127.0.0.1:8000/docs`.

### 6. Run the React dashboard

```bash
cd dashboard && npm install && npm run dev
```

The dashboard will be available at `http://localhost:5173`.

> **Note:** The FastAPI backend must be running separately (step 4) for the dashboard to communicate with the API.

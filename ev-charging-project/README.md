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

### 4. Database Setup & Automatic Seeding

Start the PostgreSQL container and copy the environment configuration:

```bash
cp .env.example .env
docker compose up -d
```

> [!TIP]
> **Automatic Database Seeding:** `docker compose up -d` automatically seeds the database with all **382 Karnataka charging stations**, 34,000+ session logs, 7,000+ reviews, and fault records from [`database/seed/init_seed.sql.gz`](database/seed/init_seed.sql.gz).
> Teammates **do NOT need an `OCM_API_KEY`** or need to run the data ingestion pipeline manually.

> [!IMPORTANT]
> **Fresh Volume Requirement:** PostgreSQL only executes init scripts in `/docker-entrypoint-initdb.d/` on a **completely empty data volume**. If you previously created an unseeded or broken container volume, reset it first before starting:
> ```bash
> docker compose down -v
> docker compose up -d
> ```

### 5. Pretrained Model Artifacts

Pretrained model weights in `models/artifacts/` (`reliability_model.pkl`, `occupancy_model.pkl`, ~320 KB total) are committed directly to Git, enabling immediate API execution after cloning.

If you ever wish to re-train the models from scratch on the database:
```bash
python models/reliability_model.py
python models/occupancy_model.py
```

### 6. Run the FastAPI Backend Server

```bash
uvicorn api.main:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000`. Interactive Swagger UI documentation is available at `http://127.0.0.1:8000/docs`.

### 7. Run the React Dashboard

```bash
cd dashboard
npm install
npm run dev
```

The dashboard will be available at `http://localhost:5173`.

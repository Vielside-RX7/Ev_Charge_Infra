-- ============================================================================
-- EV Charging Intelligence — Database Schema (Reference)
-- ============================================================================
-- This file mirrors the SQLAlchemy ORM models in models.py.
-- It is provided for documentation/reference purposes.
-- The authoritative schema is defined in models.py.
-- ============================================================================

-- ── chargers ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chargers (
    id              INTEGER     PRIMARY KEY AUTOINCREMENT,
    external_id     VARCHAR(255),                       -- e.g. Open Charge Map ID
    name            VARCHAR(255) NOT NULL,
    operator        VARCHAR(255),
    address         TEXT,
    city            VARCHAR(100),
    state           VARCHAR(100),
    latitude        FLOAT       NOT NULL,
    longitude       FLOAT       NOT NULL,
    connector_type  VARCHAR(100),
    charging_power_kw FLOAT,
    num_ports       INTEGER,
    source          VARCHAR(100) NOT NULL DEFAULT 'open_charge_map',
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── users ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                      INTEGER     PRIMARY KEY AUTOINCREMENT,
    name                    VARCHAR(255) NOT NULL,
    email                   VARCHAR(255) NOT NULL UNIQUE,
    vehicle_model           VARCHAR(255),
    battery_capacity_kwh    FLOAT,
    preferred_connector_type VARCHAR(100),
    created_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── charging_sessions ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS charging_sessions (
    id                  INTEGER     PRIMARY KEY AUTOINCREMENT,
    charger_id          INTEGER     NOT NULL REFERENCES chargers(id),
    user_id             INTEGER              REFERENCES users(id),
    start_time          DATETIME    NOT NULL,
    end_time            DATETIME,
    energy_delivered_kwh FLOAT,
    status              VARCHAR(11) NOT NULL CHECK (status IN ('success', 'failed', 'interrupted')),
    soc_start           FLOAT,                          -- state of charge at start (%)
    soc_end             FLOAT,                          -- state of charge at end (%)
    cost                FLOAT,
    source              VARCHAR(9)  NOT NULL DEFAULT 'simulated' CHECK (source IN ('real', 'simulated')),
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── reviews ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER     PRIMARY KEY AUTOINCREMENT,
    charger_id      INTEGER     NOT NULL REFERENCES chargers(id),
    user_id         INTEGER              REFERENCES users(id),
    rating          INTEGER     NOT NULL CHECK (rating >= 1 AND rating <= 5),
    review_text     TEXT,
    sentiment_score FLOAT,                              -- filled later by NLP model
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── faults ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS faults (
    id              INTEGER     PRIMARY KEY AUTOINCREMENT,
    charger_id      INTEGER     NOT NULL REFERENCES chargers(id),
    reported_at     DATETIME    NOT NULL,
    fault_type      VARCHAR(255) NOT NULL,
    description     TEXT,
    resolved        BOOLEAN     NOT NULL DEFAULT 0,
    resolved_at     DATETIME,
    source          VARCHAR(11) NOT NULL DEFAULT 'user_report' CHECK (source IN ('user_report', 'system')),
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── maintenance_logs ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS maintenance_logs (
    id                  INTEGER     PRIMARY KEY AUTOINCREMENT,
    charger_id          INTEGER     NOT NULL REFERENCES chargers(id),
    maintenance_date    DATETIME    NOT NULL,
    description         TEXT,
    technician          VARCHAR(255),
    created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

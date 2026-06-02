-- Store Intelligence Database Schema
-- Brigade Road Bangalore (ST1008) — Purplle Tech Challenge 2026

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Events table ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    event_id    UUID PRIMARY KEY,
    store_id    VARCHAR(50)  NOT NULL,
    camera_id   VARCHAR(50)  NOT NULL,
    visitor_id  VARCHAR(50)  NOT NULL,
    event_type  VARCHAR(50)  NOT NULL,
    timestamp   TIMESTAMPTZ  NOT NULL,
    zone_id     VARCHAR(50),
    dwell_ms    INTEGER      DEFAULT 0,
    is_staff    BOOLEAN      DEFAULT FALSE,
    confidence  FLOAT        NOT NULL,
    metadata    JSONB        DEFAULT '{}',
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_store_ts    ON events(store_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_visitor     ON events(visitor_id);
CREATE INDEX IF NOT EXISTS idx_events_type        ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_store_date  ON events(store_id, DATE(timestamp));
CREATE INDEX IF NOT EXISTS idx_events_metadata    ON events USING GIN (metadata);

-- ── Sessions table ────────────────────────────────────────────────────────────
-- zones_visited stored as JSONB array (portable; ingestion.py writes JSON strings)
CREATE TABLE IF NOT EXISTS sessions (
    session_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id        VARCHAR(50)  NOT NULL,
    visitor_id      VARCHAR(50)  NOT NULL UNIQUE,
    entry_time      TIMESTAMPTZ  NOT NULL,
    exit_time       TIMESTAMPTZ,
    zones_visited   TEXT         DEFAULT '[]',   -- JSON array string, updated by app
    total_dwell_ms  INTEGER      DEFAULT 0,
    converted       BOOLEAN      DEFAULT FALSE,
    is_staff        BOOLEAN      DEFAULT FALSE,
    reentry_count   INTEGER      DEFAULT 0,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_store_date  ON sessions(store_id, DATE(entry_time));
CREATE INDEX IF NOT EXISTS idx_sessions_visitor     ON sessions(visitor_id);
CREATE INDEX IF NOT EXISTS idx_sessions_converted   ON sessions(store_id, converted) WHERE NOT is_staff;

-- ── POS transactions ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pos_transactions (
    transaction_id    VARCHAR(50)    PRIMARY KEY,
    store_id          VARCHAR(50)    NOT NULL,
    timestamp         TIMESTAMPTZ    NOT NULL,
    basket_value_inr  DECIMAL(10,2)  NOT NULL,
    session_id        UUID           REFERENCES sessions(session_id),
    created_at        TIMESTAMPTZ    DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pos_store_ts  ON pos_transactions(store_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_pos_session   ON pos_transactions(session_id);

-- ── Anomalies table ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id        VARCHAR(50)  NOT NULL,
    anomaly_type    VARCHAR(50)  NOT NULL,
    severity        VARCHAR(20)  NOT NULL CHECK (severity IN ('INFO','WARN','CRITICAL')),
    detected_at     TIMESTAMPTZ  NOT NULL,
    current_value   FLOAT,
    baseline_value  FLOAT,
    suggested_action TEXT,
    resolved        BOOLEAN      DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_anomalies_store ON anomalies(store_id, detected_at DESC) WHERE NOT resolved;

-- ── Stores metadata ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS stores (
    store_id    VARCHAR(50)  PRIMARY KEY,
    store_name  VARCHAR(100) NOT NULL,
    city        VARCHAR(50)  NOT NULL,
    open_hours  JSONB        NOT NULL,
    zones       JSONB        NOT NULL,
    cameras     JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- ── Seed real store data ──────────────────────────────────────────────────────

-- Brigade Road Bangalore (real store from challenge dataset)
INSERT INTO stores (store_id, store_name, city, open_hours, zones, cameras)
VALUES (
    'ST1008',
    'Brigade_Bangalore',
    'Bangalore',
    '{"open": "10:00", "close": "22:00"}'::JSONB,
    '["MAYBELLINE","LAKME","FACES_CANADA","MARS_NYBAE","ALPS_GOODNESS","LOREAL",
      "BEAUTY_ESSENTIALS","ACCESSORIES","JUICY_CHEMISTRY","AQUALOGICA","TFS",
      "GOOD_VIBES","DERMDOC","FOXTALE","MINIMALIST","MENS_CARE",
      "SWISS_BEAUTY","RENEE","PILGRIM","SALM_EB","COSRX_KOREAN","BILLING"]'::JSONB,
    '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_FLOOR_02","CAM_BILLING_01","CAM_FLOOR_03"]'::JSONB
)
ON CONFLICT (store_id) DO NOTHING;

-- Legacy test store (keeps existing tests passing)
INSERT INTO stores (store_id, store_name, city, open_hours, zones, cameras)
VALUES (
    'STORE_BLR_002',
    'Purplle Bangalore - Koramangala',
    'Bangalore',
    '{"open": "10:00", "close": "22:00"}'::JSONB,
    '["SKINCARE","MAKEUP","FRAGRANCE","BILLING"]'::JSONB,
    '["CAM_ENTRY_01","CAM_FLOOR_01","CAM_BILLING_01"]'::JSONB
)
ON CONFLICT (store_id) DO NOTHING;

-- ── Permissions ───────────────────────────────────────────────────────────────
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO storeuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO storeuser;

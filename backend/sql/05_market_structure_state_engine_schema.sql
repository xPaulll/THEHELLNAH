-- ============================================================================
-- ALPED PUNYA V3 — FEATURE ENGINE V1: MARKET STRUCTURE STATE ENGINE (STEP 3)
-- ============================================================================

-- 1. Persistent Current State (1 row per source_id + symbol + timeframe)
CREATE TABLE IF NOT EXISTS market_structure_state_current (
    id BIGSERIAL,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    state TEXT NOT NULL CHECK (state IN ('UNKNOWN', 'NEUTRAL', 'BULLISH', 'BEARISH', 'TRANSITION')),
    previous_state TEXT NOT NULL CHECK (previous_state IN ('UNKNOWN', 'NEUTRAL', 'BULLISH', 'BEARISH', 'TRANSITION')),
    structure TEXT NOT NULL CHECK (structure IN ('HH_HL', 'LH_LL', 'MIXED', 'NONE')),

    last_event TEXT NOT NULL,
    last_event_time BIGINT NOT NULL,
    last_event_price NUMERIC(16, 6) NOT NULL,

    state_changed BOOLEAN NOT NULL DEFAULT FALSE,
    state_reason TEXT NOT NULL,
    structure_strength TEXT NOT NULL CHECK (structure_strength IN ('INSUFFICIENT', 'DEVELOPING', 'CONFIRMED')),

    source_event_id BIGINT,
    bar_time BIGINT NOT NULL,
    bar_index BIGINT NOT NULL CHECK (bar_index > 0),

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (source_id, symbol, timeframe)
);

-- 2. Audit Trail State History (Inserts ONLY when state_changed == True)
CREATE TABLE IF NOT EXISTS market_structure_state_history (
    id BIGSERIAL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    previous_state TEXT NOT NULL CHECK (previous_state IN ('UNKNOWN', 'NEUTRAL', 'BULLISH', 'BEARISH', 'TRANSITION')),
    new_state TEXT NOT NULL CHECK (new_state IN ('UNKNOWN', 'NEUTRAL', 'BULLISH', 'BEARISH', 'TRANSITION')),
    structure TEXT NOT NULL CHECK (structure IN ('HH_HL', 'LH_LL', 'MIXED', 'NONE')),

    last_event TEXT NOT NULL,
    event_time BIGINT NOT NULL,
    event_price NUMERIC(16, 6) NOT NULL,

    state_reason TEXT NOT NULL,
    structure_strength TEXT NOT NULL CHECK (structure_strength IN ('INSUFFICIENT', 'DEVELOPING', 'CONFIRMED')),

    source_event_id BIGINT,
    bar_time BIGINT NOT NULL,
    bar_index BIGINT NOT NULL CHECK (bar_index > 0),

    created_at TIMESTAMPTZ DEFAULT now(),

    -- Durable uniqueness preventing duplicate state transitions at the same bar
    CONSTRAINT uq_state_history_transition
        UNIQUE (source_id, symbol, timeframe, bar_time, new_state)
);

CREATE INDEX IF NOT EXISTS idx_state_history_lookup
ON market_structure_state_history (source_id, symbol, timeframe, bar_time DESC);

-- 3. Durable Processed-Event Ledger (Idempotency)
CREATE TABLE IF NOT EXISTS market_structure_state_events_processed (
    id BIGSERIAL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    event_key TEXT NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_state_events_processed
        UNIQUE (source_id, symbol, timeframe, event_key)
);

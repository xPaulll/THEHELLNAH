-- ============================================================================
-- ALPED PUNYA V3 — FEATURE ENGINE V1: SWING DETECTION (STEP 1)
-- ============================================================================

CREATE TABLE IF NOT EXISTS market_swings (
    id BIGSERIAL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    swing_type TEXT NOT NULL CHECK (swing_type IN ('HIGH', 'LOW')),
    swing_candle_time_epoch BIGINT NOT NULL,
    swing_price NUMERIC(16, 6) NOT NULL,
    confirmed_at_candle_time_epoch BIGINT NOT NULL,
    classification TEXT CHECK (
        classification IN ('HH', 'HL', 'LH', 'LL', 'EQH', 'EQL')
        OR classification IS NULL
    ),
    fractal_n INT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_market_swings
        UNIQUE(
            source_id,
            symbol,
            timeframe,
            swing_candle_time_epoch,
            swing_type
        )
);

CREATE INDEX IF NOT EXISTS idx_market_swings_lookup
ON market_swings(
    source_id,
    symbol,
    timeframe,
    swing_candle_time_epoch DESC
);

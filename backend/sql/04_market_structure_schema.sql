-- ============================================================================
-- ALPED PUNYA V3 — FEATURE ENGINE V1: MARKET STRUCTURE DETECTION (STEP 2)
-- ============================================================================

CREATE TABLE IF NOT EXISTS market_structure_events (
    id BIGSERIAL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'BOS_BULLISH',
            'BOS_BEARISH',
            'CHOCH_BULLISH',
            'CHOCH_BEARISH',
            'MSS_BULLISH',
            'MSS_BEARISH',
            'DOUBLE_BREAK'
        )
    ),

    event_candle_time_epoch BIGINT NOT NULL,

    -- Primary broken swing reference
    broken_swing_candle_time_epoch BIGINT,
    broken_swing_type TEXT CHECK (broken_swing_type IS NULL OR broken_swing_type IN ('HIGH', 'LOW')),
    broken_swing_price NUMERIC(16, 6),

    -- Explicit references for DOUBLE_BREAK (prevents ambiguity)
    broken_high_swing_candle_time_epoch BIGINT,
    broken_high_swing_price NUMERIC(16, 6),
    broken_low_swing_candle_time_epoch BIGINT,
    broken_low_swing_price NUMERIC(16, 6),

    candle_close NUMERIC(16, 6) NOT NULL,
    break_threshold NUMERIC(16, 6) NOT NULL DEFAULT 0.0,

    previous_bias TEXT NOT NULL CHECK (previous_bias IN ('NEUTRAL', 'BULLISH', 'BEARISH')),
    resulting_bias TEXT NOT NULL CHECK (resulting_bias IN ('NEUTRAL', 'BULLISH', 'BEARISH')),
    transition_state TEXT NOT NULL CHECK (
        transition_state IN ('NORMAL', 'CHOCH_BEARISH_PENDING', 'CHOCH_BULLISH_PENDING')
    ),

    fractal_n INT NOT NULL,
    decision_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),

    -- Safe Idempotency: NO nullable fields in UNIQUE constraint
    CONSTRAINT uq_market_structure_event
        UNIQUE (source_id, symbol, timeframe, event_candle_time_epoch, event_type)
);

CREATE INDEX IF NOT EXISTS idx_market_structure_events_lookup
ON market_structure_events (
    source_id,
    symbol,
    timeframe,
    event_candle_time_epoch DESC
);

-- Persistent Structure State (Eliminates in-memory dependency)
CREATE TABLE IF NOT EXISTS market_structure_state (
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    bias TEXT NOT NULL CHECK (bias IN ('NEUTRAL', 'BULLISH', 'BEARISH')),
    transition_state TEXT NOT NULL CHECK (
        transition_state IN ('NORMAL', 'CHOCH_BEARISH_PENDING', 'CHOCH_BULLISH_PENDING')
    ),

    last_processed_candle_time_epoch BIGINT NOT NULL,
    last_processed_confirmation_time BIGINT NOT NULL DEFAULT 0,

    -- Protected Levels (Defense boundaries)
    protected_high_candle_time_epoch BIGINT,
    protected_high_price NUMERIC(16, 6),
    protected_low_candle_time_epoch BIGINT,
    protected_low_price NUMERIC(16, 6),

    -- Continuation Break Targets
    bullish_break_level_candle_time_epoch BIGINT,
    bullish_break_level_price NUMERIC(16, 6),
    bearish_break_level_candle_time_epoch BIGINT,
    bearish_break_level_price NUMERIC(16, 6),

    -- Tracks last broken swings to prevent duplicate BOS on consecutive candles
    last_broken_high_candle_time_epoch BIGINT,
    last_broken_low_candle_time_epoch BIGINT,

    -- Active CHoCH tracking for MSS confirmation
    pending_choch_event_id BIGINT,
    pending_choch_epoch BIGINT,

    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (source_id, symbol, timeframe)
);

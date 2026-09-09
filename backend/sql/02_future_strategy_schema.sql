-- ============================================================================
-- ALPED PUNYA V3 — FORWARD-COMPATIBLE STRATEGY & AI CONTRACTS (V2/V3)
-- ============================================================================

-- 1. LIQUIDITY POOLS (HTF & Session Liquidity Tracking)
CREATE TABLE IF NOT EXISTS liquidity_pools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,                  -- 'H4', 'H1', 'M15', 'D1'
    pool_type TEXT NOT NULL,                  -- 'ASIA_HIGH', 'ASIA_LOW', 'LONDON_HIGH', 'LONDON_LOW', 'PDH', 'PDL', 'EQH', 'EQL'
    price_level NUMERIC(16, 6) NOT NULL,
    start_time_utc TIMESTAMPTZ NOT NULL,
    swept_time_utc TIMESTAMPTZ,
    sweep_candle_high NUMERIC(16, 6),
    sweep_candle_low NUMERIC(16, 6),
    status TEXT NOT NULL DEFAULT 'UNTOUCHED', -- 'UNTOUCHED', 'SWEPT', 'PARTIALLY_SWEPT', 'INVALIDATED'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_liquidity_lookup ON liquidity_pools (source_id, symbol, status);

-- 2. FVG RECORDS (Fair Value Gaps Imbalance Tracking)
CREATE TABLE IF NOT EXISTS fvg_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,                  -- 'H1', 'M15', 'M5', 'M1'
    fvg_type TEXT NOT NULL,                   -- 'BULLISH', 'BEARISH'
    high_price NUMERIC(16, 6) NOT NULL,
    low_price NUMERIC(16, 6) NOT NULL,
    size_pips NUMERIC(10, 2) NOT NULL,
    formed_time_utc TIMESTAMPTZ NOT NULL,
    filled_time_utc TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'ACTIVE',    -- 'ACTIVE', 'PARTIALLY_FILLED', 'FILLED', 'INVALID'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fvg_lookup ON fvg_records (source_id, symbol, timeframe, status);

-- 3. SETUPS (Correlates Market Data to Strategies with Setup ID)
CREATE TABLE IF NOT EXISTS setups (
    setup_id TEXT PRIMARY KEY,                -- e.g. 'SETUP-20260909-EURUSD-0001'
    source_id VARCHAR(64) NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,                   -- 'INTRADAY' | 'SILVER_BULLET'
    direction TEXT NOT NULL,                  -- 'BUY' | 'SELL'
    setup_time_utc TIMESTAMPTZ NOT NULL,
    market_state TEXT,                        -- 'TRENDING', 'EXPANDING', 'RANGING', etc.
    evidence JSONB NOT NULL,                  -- Deep quantitative metrics
    status TEXT NOT NULL DEFAULT 'DETECTED',  -- 'DETECTED', 'QUALIFIED', 'INVALIDATED'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_setups_lookup ON setups (source_id, symbol, strategy, setup_time_utc DESC);

-- 4. SIGNALS (AI Generated Signals Waiting for Manual Approval)
CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,               -- e.g. 'SIG-20260909-000001'
    setup_id TEXT REFERENCES setups(setup_id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    context JSONB NOT NULL,                   -- HTF bias, structure, session
    evidence JSONB NOT NULL,                  -- Displacement, FVG, sweep details
    entry_low NUMERIC(16, 6) NOT NULL,
    entry_high NUMERIC(16, 6) NOT NULL,
    stop_loss NUMERIC(16, 6) NOT NULL,
    take_profit NUMERIC(16, 6) NOT NULL,
    risk_reward NUMERIC(6, 2) NOT NULL,
    invalidation_rule TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'WAITING',   -- 'WAITING', 'APPROVED', 'REJECTED', 'EXPIRED'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signals_lookup ON signals (symbol, strategy, status, created_at DESC);

# Alped Punya V3 — V1 Data Infrastructure & MT5 Bridge

**Alped Punya V3** is a high-integrity, production-grade market data bridge connecting MetaTrader 5 (MT5) to a FastAPI ingestion backend and Supabase PostgreSQL.

> [!IMPORTANT]
> **V1 Core Mandate**:
> - **MQL5 EA (`Alped_Bridge.mq5`) = Raw Data Collector ONLY.** Zero indicator calculations, zero strategy logic, zero auto-trading (`AllowExecution = false`).
> - **100% Timer-Driven Network I/O**: `OnInit()`, `OnTick()`, and `OnTradeTransaction()` are strictly non-blocking (< 0.1ms) and queue events in memory. All HTTP `WebRequest()` calls occur solely inside `OnTimer()`.
> - **Authoritative Epoch & Canonical UTC**: MT5 sends `candle_time_epoch` as the authoritative integer timestamp. Backend generates timezone-aware `candle_time_utc` with 3-way consistency verification.
> - **Zero `pytz`**: Exclusively uses Python 3.12 standard `zoneinfo.ZoneInfo` for robust Daylight Saving Time (DST) tracking across London, New York, Tokyo, and Jakarta.
> - **Source Isolation**: Every table and entity is scoped by an authoritative 64-character SHA-256 `source_id`.

---

## 1. Directory Structure

```text
├── Alped_Bridge.mq5                      # MT5 Expert Advisor (Decoupled Queue Bridge)
├── backend/
│   ├── app/
│   │   ├── main.py                       # FastAPI entrypoint & middleware
│   │   ├── api/v1/endpoints/             # Handshake, Symbols, Candles, Ticks, Account
│   │   ├── core/                         # Config, Constants, Security (source_id generator)
│   │   ├── models/                       # Pydantic schemas & future strategy contracts
│   │   ├── services/                     # Timezone, Session, MarketSchedule, DataQuality, Sync
│   │   ├── repositories/                 # Supabase query layer with in-memory fallback
│   │   ├── features/                     # [V1 Interface Placeholders] Structure, FVG, Liquidity
│   │   ├── strategies/                   # [V1 Interface Placeholders] Intraday & Silver Bullet
│   │   └── analytics/                    # [V1 Interface Placeholders] Journal & Metrics
│   ├── sql/
│   │   ├── 01_v1_core_schema.sql         # Production V1 DDL tables & indexes
│   │   └── 02_future_strategy_schema.sql # Forward-compatible V2/V3 tables
│   ├── tests/                            # Automated test suite (17 comprehensive tests)
│   ├── requirements.txt                  # Dependencies
│   └── .env.example
└── README.md
```

---

## 2. Setup & Installation

### Step A: Backend Environment Setup
1. Ensure Python 3.12 is installed:
   ```bash
   python --version
   ```
2. Install requirements:
   ```bash
   pip install -r backend/requirements.txt
   ```
3. Copy environment configuration:
   ```bash
   copy backend\.env.example backend\.env
   ```
4. Start the FastAPI development server:
   ```bash
   python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
5. Verify health:
   Visit `http://127.0.0.1:8000/api/v1/health` in your browser.

---

### Step B: Database Setup (Supabase)
1. Open your Supabase project dashboard -> **SQL Editor**.
2. Run `backend/sql/01_v1_core_schema.sql` to deploy all V1 core tables (`sources`, `symbols`, `market_candles`, `candle_gaps`, `market_ticks`, `account_snapshots`, `positions`, `position_events`).
3. Run `backend/sql/02_future_strategy_schema.sql` to deploy the forward-compatible strategy contracts (`liquidity_pools`, `fvg_records`, `setups`, `signals`).
4. Set your `SUPABASE_URL` and `SUPABASE_KEY` inside `backend/.env`.

---

### Step C: MetaTrader 5 Configuration
1. In MT5 Terminal, click **Tools** -> **Options** (or `Ctrl+O`) -> **Expert Advisors**.
2. Check **"Allow WebRequest for listed URL"** and add:
   ```text
   http://127.0.0.1:8000
   ```
3. Open MetaEditor, compile `Alped_Bridge.mq5`.
4. Attach `Alped_Bridge` to any chart (e.g. `EURUSD`, `M5`).
5. In Inputs tab:
   - `InpFastApiUrl`: `http://127.0.0.1:8000`
   - `InpApiKey`: `alped_secret_key_v3_secure`
   - `InpEaIdentifier`: `ALPED_BRIDGE_01`
   - `InpEnvironment`: `DEMO`
   - `AllowExecution`: `false` (hard-locked)
6. Check the MT5 **Experts** tab:
   - Handshake registers and receives 64-char `source_id`.
   - Symbol specifications sync.
   - Historical bars sync for M1–D1.
   - Transitions to `STATE_LIVE_RUNNING`.

---

## 3. Running Automated Tests

Run the full automated test suite (86/86 PASSED):
```bash
python -m pytest backend/tests/ -v
```
Verifies:
- 3-way timestamp consistency validation
- Authoritative 64-character hex `source_id` normalization
- Instrument-specific market schedule gap classification (Weekend, Maintenance, Holiday vs Missing Data)
- Mathematical bar price sanity & precision rules
- Account-scoped ticket uniqueness `UNIQUE(source_id, ticket)`
- MQL5 architecture invariants (Zero WebRequest in `OnInit`, `OnTick`, `OnTradeTransaction`)
- **Step 1 Swing Detection**: Non-repainting N-bar fractals, same-side swing classification (HH/HL/LH/LL/EQH/EQL), deterministic confirmation epochs, point-tolerance calculation, restart reconstruction.
- **Step 2 Market Structure Detection Hardening**: BOS, CHoCH, strict MSS sequence checking (`check_bearish_mss_sequence`, `check_bullish_mss_sequence`), continuation target preservation, pending CHoCH closed-candle invalidation, PostgreSQL transactional persistence (`conn.transaction()`), snapshot rollback, failure injection, and independent oracle reference model verification.

---

## 4. Feature Engine Status

| Step | Component | Status | Test Coverage |
| :--- | :--- | :--- | :--- |
| **Step 1** | **Swing Detection Engine** | **COMPLETE & HARDENED** | 25/25 Tests PASSED |
| **Step 2** | **Market Structure Detection** | **COMPLETE & HARDENED** | 29/29 Tests PASSED |
| **Step 3+** | **FVG, Liquidity & Setups** | **LOCKED (NEXT PHASE)** | Pending Execution |


# Implementation Plan — Market Structure State Engine (Step 3)

## 1. Objective & Scope

Build the **Market Structure State Engine (Step 3)** on top of validated outputs from **Step 1 (Market Swings)** and **Step 2 (Market Structure Detection)**.

Step 3 transforms discrete structural events into a **persistent, deterministic, stateful, chronological, and auditable market regime** answering:
> *"Based on validated structure up to a given closed candle, what state is the market in right now?"*

### Strict Non-Goals & Boundaries
Step 3 is strictly an evaluation and regime state layer. It does **NOT**:
- Detect swings or evaluate raw candle wicks/fractals (Step 1 responsibility).
- Detect break thresholds or evaluate candle closes against swings (Step 2 responsibility).
- Generate entry/exit trading signals, SL/TP levels, or execute trades.
- Detect liquidity pools, liquidity sweeps, order blocks, or fair value gaps (FVG).
- Use machine learning, probability scoring, or speculative prediction.
- Mutate persistent state from unconfirmed / forming bars (Bar 0).

---

## 2. Step 2 Semantic Audit: Opposite BOS Verification

We conducted a source-code audit of `market_structure_detector.py` (lines 465–665):
1. **How Step 2 handles breaks against trend**:
   - In `BULLISH` state, breaking below the protected low is captured by Priority 4 as `CHOCH_BEARISH` (setting `transition_state = CHOCH_BEARISH_PENDING`, preserving `bias = BULLISH`). Step 2 **never** emits `BOS_BEARISH` when `bias == BULLISH` in normal state.
   - In `BEARISH` state, breaking above the protected high is captured by Priority 4 as `CHOCH_BULLISH` (setting `transition_state = CHOCH_BULLISH_PENDING`, preserving `bias = BEARISH`). Step 2 **never** emits `BOS_BULLISH` when `bias == BEARISH` in normal state.
2. **Opposite BOS in Step 3**:
   - If an external, synthetic, or out-of-band stream delivers an opposite BOS (e.g. `BOS_BULLISH` while in `BEARISH`):
     - **Rule 1 (Invariant)**: It **never** causes an immediate flip to the opposite regime (`BEARISH + BOS_BULLISH != BULLISH`).
     - **Rule 2 (Transition)**: Because an opposite BOS represents a contrary structural break that disrupts the active regime, Step 3 transitions to `TRANSITION` with `structure_strength = DEVELOPING`, `structure = MIXED`, and `state_reason = "Opposite BOS_BULLISH detected against BEARISH regime; moved to TRANSITION for confirmation"`.
     - Only subsequent confirmed directional sequence (e.g. `HH -> HL` or `MSS_BULLISH`) can confirm a new trend.

---

## 3. State Machine Model & Transition Rules

```
                 ┌───────────────┐
                 │    UNKNOWN    │
                 └───────┬───────┘
                         │ (structural data arrives)
                         ▼
                 ┌───────────────┐
         ┌───────┤    NEUTRAL    ├───────┐
         │       └───────┬───────┘       │
         │ (HH->HL)      │               │ (LH->LL)
         ▼               │               ▼
  ┌─────────────┐        │        ┌─────────────┐
  │   BULLISH   │◄───────┼───────►│   BEARISH   │
  └──────┬──────┘        │        └──────┬──────┘
         │               │               │
         │ CHOCH_BEARISH │ CHOCH_BULLISH │
         │  MSS_BEARISH  │  MSS_BULLISH  │
         │  BOS_BEARISH  │  BOS_BULLISH  │
         ▼               │               ▼
  ┌──────────────────────┴──────────────────────┐
  │                 TRANSITION                  │
  └──────────────────────┬──────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   (LH->LL/MSS)    (mixed/unresolved)(HH->HL/MSS)
    BEARISH             NEUTRAL          BULLISH
```

### Transition Matrix
- **`UNKNOWN -> NEUTRAL`**: Structural swings exist but lack minimum directional confirmation. Strength: `DEVELOPING`.
- **`UNKNOWN -> BULLISH`**: Validated ordered sequence `HH -> HL`. Strength: `CONFIRMED`. Config: `HH_HL`.
- **`UNKNOWN -> BEARISH`**: Validated ordered sequence `LH -> LL`. Strength: `CONFIRMED`. Config: `LH_LL`.
- **`NEUTRAL -> BULLISH`**: Validated ordered sequence `HH -> HL`. Strength: `CONFIRMED`.
- **`NEUTRAL -> BEARISH`**: Validated ordered sequence `LH -> LL`. Strength: `CONFIRMED`.
- **`BULLISH -> BULLISH`**: Receives `HH`, `HL`, or `BOS_BULLISH`. Reaffirms continuation.
- **`BEARISH -> BEARISH`**: Receives `LH`, `LL`, or `BOS_BEARISH`. Reaffirms continuation.
- **`BULLISH -> TRANSITION`**: Validated `CHOCH_BEARISH`, `MSS_BEARISH`, or contrary `BOS_BEARISH`.
- **`BEARISH -> TRANSITION`**: Validated `CHOCH_BULLISH`, `MSS_BULLISH`, or contrary `BOS_BULLISH`.
- **`TRANSITION -> BEARISH`**: Validated bearish confirmation sequence (`LH -> LL` or `MSS_BEARISH`). Strength: `CONFIRMED`. Config: `LH_LL`.
- **`TRANSITION -> BULLISH`**: Validated bullish confirmation sequence (`HH -> HL` or `MSS_BULLISH`). Strength: `CONFIRMED`. Config: `HH_HL`.
- **`TRANSITION -> NEUTRAL`**: Sequence becomes mixed (e.g. `HH -> LL -> HL`) without confirming new direction. Strength: `DEVELOPING`. Config: `MIXED`.
- **`DOUBLE_BREAK`**: Preserves active state and configuration without mutation (`state_changed = False`).

---

## 4. File Layout & Component Design

1. **`backend/app/core/constants.py`** [MODIFY]:
   - Enums:
     - `MarketState`: `UNKNOWN`, `NEUTRAL`, `BULLISH`, `BEARISH`, `TRANSITION`
     - `StructureStrength`: `INSUFFICIENT`, `DEVELOPING`, `CONFIRMED`
     - `StructureConfiguration`: `HH_HL`, `LH_LL`, `MIXED`, `NONE`
     - `CanonicalStructureEventType`: `HH`, `HL`, `LH`, `LL`, `BOS_BULLISH`, `BOS_BEARISH`, `CHOCH_BULLISH`, `CHOCH_BEARISH`, `MSS_BULLISH`, `MSS_BEARISH`, `DOUBLE_BREAK`

2. **`backend/sql/05_market_structure_state_engine_schema.sql`** [NEW]:
   - `market_structure_state_current`: Stores latest state per `(source_id, symbol, timeframe)`.
   - `market_structure_state_history`: Audit trail table with UNIQUE constraint `(source_id, symbol, timeframe, bar_time, new_state)`. Inserts ONLY when `state_changed == True`.
   - `market_structure_state_events_processed`: Durable processed ledger with UNIQUE constraint `(source_id, symbol, timeframe, event_key)` for idempotency.

3. **`backend/app/models/market_structure_state_models.py`** [NEW]:
   - Pydantic models:
     - `CanonicalStructureEvent`: payload schema carrying event attributes.
     - `MarketStructureCurrentStateResponse`: matching Dashboard contract.
     - `MarketStructureHistoryItemResponse` and list queries.

4. **`backend/app/features/market_structure_state_engine.py`** [NEW]:
   - Pure, deterministic, stateless functional engine:
     - `create_initial_market_state(...)`
     - `evaluate_structure_event(...)`
     - Chronological check, Bar 0 guard, duplicate ledger check, ordered sequence validation (`check_ordered_bullish_sequence`, `check_ordered_bearish_sequence`).

5. **`backend/app/repositories/market_structure_state_repo.py`** [NEW]:
   - Dual-persistence layer (PostgreSQL transaction + in-memory fallback for test isolation):
     - `get_current_state(source_id, symbol, timeframe)`
     - `save_state_and_history_if_changed(...)` (atomic transaction across current, history, and processed ledger)
     - `get_state_history(...)`
     - `is_event_processed(...)`
     - `clear_state_for_timeframe(...)`

6. **`backend/app/services/market_data_service.py`** [MODIFY]:
   - Wire Step 3 into full rebuild and live processing pipelines.
   - Build canonical event stream with deterministic sort key `(bar_time, stream_priority, sub_epoch, source_event_id)`.

7. **`backend/app/api/v1/endpoints/market_structure_state_router.py`** [NEW] & **`router.py`** [MODIFY]:
   - Expose:
     - `GET /api/v1/market-structure-state/{symbol}/{timeframe}`
     - `GET /api/v1/market-structure-state/{symbol}/{timeframe}/history`

8. **`backend/tests/test_market_structure_state_engine.py`** [NEW]:
   - 20 comprehensive scenarios + regression validation.

---

## 5. Verification Plan
- Run `python -m pytest backend/tests -v` to ensure all 86 existing tests + new Step 3 tests pass 100%.
- Verify git status and diff.

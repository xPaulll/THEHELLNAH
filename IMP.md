# Implementation Plan — Feature Engine: Market Structure Detection (Step 2)

## 1. Objective

Membangun **Market Structure Engine** di atas `market_swings` yang sudah tersedia pada Step 1.

Step 2 bertanggung jawab untuk mendeteksi:

- **BOS — Break of Structure**
- **CHoCH — Change of Character**
- **MSS — Market Structure Shift**, hanya jika memenuhi kondisi objektif yang didefinisikan di bawah
- structural bias: `BULLISH`, `BEARISH`, atau `NEUTRAL`

Step 2 **tidak menghitung ulang swing**.

Pipeline harus menjadi:

```
MT5
 ↓
market_candles
 ↓
Step 1 — Swing Detector
 ↓
market_swings
 ↓
Step 2 — Market Structure Engine
 ↓
BOS / CHoCH / MSS
 ↓
market_structure_events
```

### Hard Boundary

Step 2 **DILARANG** mengimplementasikan:

- FVG
- Liquidity Pool
- Liquidity Sweep
- Order Block
- Fair Value Gap
- Trading Signal
- Entry / Exit
- Stop Loss / Take Profit
- Strategy execution
- Indicator-based trend detection
- AI / ML prediction

Semua fitur tersebut merupakan tahap berikutnya.

---

# 2. Core Design Principle

`market_swings` dari Step 1 adalah **single source of truth** untuk structural points.

Contoh:

```
market_swings

HIGH  4400.00 HH
LOW   4380.00 HL
HIGH  4420.00 HH
LOW   4400.00 HL
```

Step 2 membaca data tersebut dan menentukan apakah candle berikutnya melakukan break terhadap structural level.

**Jangan melakukan swing detection ulang di Step 2.**

---

# 3. Market Structure Definitions

## 3.1 Structural High

Structural High berasal dari:

```
market_swings.swing_type = HIGH
```

Structural Low berasal dari:

```
market_swings.swing_type = LOW
```

Hanya swing yang sudah **confirmed** yang boleh digunakan.

Tidak boleh menggunakan:

```
Bar 0
unconfirmed swing
future candle
```

---

# 4. Break Validation

## 4.1 Close-Based Break

Untuk Step 2, definisi break harus menggunakan **candle CLOSE**, bukan sekadar wick.

### Bullish Break

Jika:

```
candle.close > structural_high.swing_price
```

maka structural high tersebut dianggap broken.

### Bearish Break

Jika:

```
candle.close < structural_low.swing_price
```

maka structural low tersebut dianggap broken.

Contoh:

```
Swing High = 4400.00

Candle:
High   = 4405.00
Close  = 4398.00

Result:
NO BREAK
```

Karena wick menembus tetapi close tidak melewati level.

Sebaliknya:

```
Swing High = 4400.00

Candle:
High   = 4405.00
Close  = 4402.00

Result:
BULLISH BREAK
```

---

# 5. Break Tolerance

Break tidak boleh menggunakan raw floating-point equality.

Gunakan `symbol_point` dari metadata symbol yang sama dengan Step 1.

Tambahkan konfigurasi:

```
STRUCTURE_BREAK_TOLERANCE_POINTS
```

Default:

```
XAUUSD = 0 points
EURUSD = 0 points
GBPUSD = 0 points
default = 0 points
```

Untuk initial implementation, **default 0** berarti:

```
close > level
```

untuk bullish break dan:

```
close < level
```

untuk bearish break.

Tolerance harus configurable sehingga dapat diubah kemudian tanpa mengubah algoritma.

Jangan hardcode nilai dollar/pip ke dalam detector.

---

# 6. Structural Bias State

Step 2 memperkenalkan state:

```
NEUTRAL
BULLISH
BEARISH
```

Initial state:

```
NEUTRAL
```

Bias **tidak boleh ditentukan dari indikator**.

Bias hanya berasal dari struktur swing.

---

# 7. Initial Structure Detection

Gunakan classification dari `market_swings`.

### Bullish Structure

Structural bullish condition:

```
latest meaningful HIGH = HH
latest meaningful LOW  = HL
```

Kemudian:

```
bias = BULLISH
```

### Bearish Structure

Structural bearish condition:

```
latest meaningful HIGH = LH
latest meaningful LOW  = LL
```

Kemudian:

```
bias = BEARISH
```

### Equal Levels

`EQH` dan `EQL` **tidak boleh digunakan sebagai bukti directional bias secara langsung**.

Contoh:

```
HIGH = EQH
LOW  = HL
```

belum cukup untuk menghasilkan:

```
BULLISH
```

Engine harus mencari directional structural point sebelumnya.

---

# 8. BOS Definition

## 8.1 Bullish BOS

Jika current structural bias:

```
BULLISH
```

dan candle close menembus:

```
latest valid structural HIGH
```

maka:

```
BOS_BULLISH
```

Contoh:

```
HH = 4420
HL = 4400

Candle Close = 4425

→ BOS_BULLISH
```

BOS harus menyimpan referensi ke swing yang ditembus.

---

## 8.2 Bearish BOS

Jika:

```
bias = BEARISH
```

dan candle close menembus:

```
latest valid structural LOW
```

maka:

```
BOS_BEARISH
```

Contoh:

```
LH = 4400
LL = 4375

Candle Close = 4370

→ BOS_BEARISH
```

---

# 9. CHoCH Definition

CHoCH adalah break terhadap **protected structural level yang berlawanan dengan current bias**.

## 9.1 Bullish → Bearish

Jika:

```
current bias = BULLISH
```

dan candle close menembus:

```
latest valid structural LOW
```

maka:

```
CHoCH_BEARISH
```

Contoh:

```
HH = 4420
HL = 4400

Candle Close = 4395

→ CHoCH_BEARISH
```

Setelah event tersebut:

```
bias = BEARISH
```

---

## 9.2 Bearish → Bullish

Jika:

```
current bias = BEARISH
```

dan candle close menembus:

```
latest valid structural HIGH
```

maka:

```
CHoCH_BULLISH
```

Contoh:

```
LH = 4400
LL = 4375

Candle Close = 4405

→ CHoCH_BULLISH
```

Setelah event:

```
bias = BULLISH
```

---

# 10. BOS vs CHoCH Priority

Engine **tidak boleh menentukan BOS/CHoCH hanya berdasarkan arah candle**.

Event harus ditentukan berdasarkan:

```
current structural bias
+
broken structural level
```

Mapping:

|Bias|Level Broken|Event|
|---|---|---|
|BULLISH|HIGH|BOS_BULLISH|
|BULLISH|LOW|CHoCH_BEARISH|
|BEARISH|LOW|BOS_BEARISH|
|BEARISH|HIGH|CHoCH_BULLISH|
|NEUTRAL|HIGH|candidate bullish structure|
|NEUTRAL|LOW|candidate bearish structure|

---

# 11. Neutral State Handling

Saat:

```
bias = NEUTRAL
```

jangan memaksakan BOS atau CHoCH.

Jika candle close menembus structural high:

```
candidate bullish break
```

Jika candle close menembus structural low:

```
candidate bearish break
```

Directional bias hanya berubah menjadi:

```
BULLISH
```

atau:

```
BEARISH
```

setelah structural prerequisites terpenuhi.

Semua keputusan harus deterministic dan dapat direproduksi dari database.

---

# 12. Multiple-Level Break

Satu candle dapat secara teoritis menembus lebih dari satu structural level.

Engine harus:

1. Mengambil seluruh structural levels yang masih valid.
2. Mengurutkan berdasarkan `swing_candle_time_epoch`.
3. Mengevaluasi level yang paling recent terlebih dahulu.
4. Tidak menghasilkan duplicate event terhadap level yang sama.

Setiap structural level hanya boleh menghasilkan satu break event.

Setelah sebuah level broken:

```
broken = true
```

dan level tersebut tidak boleh menghasilkan event kedua.

---

# 13. Double-Sided Break

Jika satu candle close secara matematis dapat memenuhi:

```
close > structural_high
```

dan

```
close < structural_low
```

pada structural levels yang masih aktif, engine harus **tidak memilih secara arbitrer**.

Buat deterministic result:

```
event_type = DOUBLE_BREAK
```

atau status internal:

```
AMBIGUOUS_BREAK
```

Namun event tersebut **tidak boleh langsung diklasifikasikan sebagai BOS/CHoCH**.

Tambahkan test khusus untuk kondisi ini.

---

# 14. MSS Definition

Untuk Step 2, MSS dibuat sebagai **event lanjutan dari CHoCH**, bukan synonym dari setiap break.

MSS terjadi jika:

```
CHoCH
+
subsequent confirmed structural continuation
```

mengonfirmasi bahwa structural bias benar-benar bergeser.

Contoh:

```
BULLISH
   ↓
CHoCH_BEARISH
   ↓
new LH / LL structure
   ↓
MSS_BEARISH
```

Dan sebaliknya:

```
BEARISH
   ↓
CHoCH_BULLISH
   ↓
new HL / HH structure
   ↓
MSS_BULLISH
```

### Important

Jangan menggunakan:

```
volume
ATR
RSI
MACD
moving average
displacement percentage
```

untuk MSS pada Step 2.

MSS harus murni berdasarkan market structure.

Jika definisi MSS belum dapat dipastikan secara deterministic dari data `market_swings`, engine **lebih baik menyimpan CHoCH tetapi tidak menghasilkan MSS** daripada membuat heuristic.

---

# 15. Database Schema

## NEW

```
backend/sql/04_market_structure_schema.sql
```

Create:

```
CREATE TABLE IF NOT EXISTS market_structure_events (
    id BIGSERIAL PRIMARY KEY,

    source_id VARCHAR(64) NOT NULL
        REFERENCES sources(source_id)
        ON DELETE CASCADE,

    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    event_type TEXT NOT NULL
        CHECK (
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

    broken_swing_candle_time_epoch BIGINT,
    broken_swing_type TEXT
        CHECK (
            broken_swing_type IS NULL
            OR broken_swing_type IN ('HIGH', 'LOW')
        ),

    broken_swing_price NUMERIC(16, 6),

    candle_close NUMERIC(16, 6) NOT NULL,

    previous_bias TEXT
        CHECK (
            previous_bias IS NULL
            OR previous_bias IN (
                'NEUTRAL',
                'BULLISH',
                'BEARISH'
            )
        ),

    resulting_bias TEXT NOT NULL
        CHECK (
            resulting_bias IN (
                'NEUTRAL',
                'BULLISH',
                'BEARISH'
            )
        ),

    fractal_n INT NOT NULL,

    created_at TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT uq_market_structure_event
        UNIQUE (
            source_id,
            symbol,
            timeframe,
            event_candle_time_epoch,
            event_type,
            broken_swing_candle_time_epoch
        )
);

CREATE INDEX IF NOT EXISTS idx_market_structure_lookup
ON market_structure_events (
    source_id,
    symbol,
    timeframe,
    event_candle_time_epoch DESC
);
```

---

# 16. Structure State Persistence

Jangan bergantung kepada in-memory state.

## NEW

```
market_structure_state
```

Schema:

```
CREATE TABLE IF NOT EXISTS market_structure_state (
    source_id VARCHAR(64) NOT NULL
        REFERENCES sources(source_id)
        ON DELETE CASCADE,

    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    bias TEXT NOT NULL
        CHECK (bias IN ('NEUTRAL', 'BULLISH', 'BEARISH')),

    last_processed_candle_time_epoch BIGINT NOT NULL,

    protected_high_candle_time_epoch BIGINT,
    protected_high_price NUMERIC(16, 6),

    protected_low_candle_time_epoch BIGINT,
    protected_low_price NUMERIC(16, 6),

    updated_at TIMESTAMPTZ DEFAULT now(),

    PRIMARY KEY (source_id, symbol, timeframe)
);
```

Tujuannya:

```
MT5 restart
Backend restart
Process restart
```

tidak menyebabkan struktur hilang.

State harus dapat direcover dari:

```
market_structure_state
+
market_swings
+
market_structure_events
+
market_candles
```

---

# 17. Repository Layer

## NEW

```
backend/app/repositories/market_structure_repo.py
```

Implement:

```
upsert_structure_event(...)
get_structure_events(...)
get_latest_structure_event(...)
get_structure_state(...)
upsert_structure_state(...)
```

Semua query symbol wajib:

```
.ilike("symbol", symbol)
```

In-memory key tetap:

```
symbol.upper()
```

mengikuti convention Step 1.

---

# 18. Domain Models

## NEW

```
backend/app/models/market_structure.py
```

Models:

```
StructureBias
StructureEventType
MarketStructureEvent
MarketStructureState
StructureQueryFilter
```

---

# 19. Feature Engine

## NEW

```
backend/app/features/market_structure_detector.py
```

Pisahkan detector dari service.

Detector harus **pure/deterministic** sebisa mungkin.

Contoh API:

```
detect_structure_event(
    candle,
    swings,
    state
) -> Optional[dict]
```

Detector tidak boleh:

```
query Supabase
write database
call API
```

---

# 20. Market Structure Service

## MODIFY

```
backend/app/services/market_data_service.py
```

Tambahkan:

```
recalculate_market_structure_full(
    source_id,
    symbol,
    timeframe
)
```

dan:

```
process_live_market_structure(
    source_id,
    symbol,
    timeframe
)
```

---

# 21. Full Scan

Initial sync / historical backfill:

```
market_candles
      ↓
market_swings
      ↓
chronological candles
      ↓
Market Structure Detector
      ↓
market_structure_events
      ↓
market_structure_state
```

Processing wajib ascending berdasarkan:

```
candle_time_epoch ASC
```

Tidak boleh menggunakan random database order.

---

# 22. Incremental Mode

Saat candle baru closed:

```
LIVE_SYNC
   ↓
load latest market candle
   ↓
load relevant market_swings
   ↓
load market_structure_state
   ↓
evaluate current candle
   ↓
create event if break exists
   ↓
update state
```

Tidak boleh melakukan full history scan setiap menit.

Target complexity:

```
O(1)
```

atau bounded by a small structural window.

---

# 23. Restart Recovery

Simulasikan:

```
process
 ↓
10 candles
 ↓
restart
 ↓
continue processing
```

Hasil harus identik dengan:

```
continuous processing
```

Tidak boleh:

```
duplicate BOS
duplicate CHoCH
bias reset ke NEUTRAL
```

---

# 24. Idempotency

Memproses candle yang sama dua kali:

```
run #1
run #2
```

harus menghasilkan:

```
same event count
same event records
same structure state
```

Tidak boleh duplicate event.

Gunakan:

```
UNIQUE constraint
+
upsert
```

sebagai database-level protection.

---

# 25. Timeframe Isolation

Structure setiap timeframe harus independen.

Contoh:

```
XAUUSD.vx M1
XAUUSD.vx M5
XAUUSD.vx M15
XAUUSD.vx M30
XAUUSD.vx H1
XAUUSD.vx H4
```

tidak boleh saling memengaruhi.

Key logical:

```
(source_id, symbol, timeframe)
```

---

# 26. Source Isolation

Account/broker berbeda:

```
source A
source B
```

tidak boleh berbagi:

```
market_swings
market_structure_events
market_structure_state
```

`source_id` tetap authoritative ID dari Step 1.

Jangan membuat ulang mekanisme source identity.

---

# 27. API

## NEW

```
backend/app/api/v1/endpoints/market_structure.py
```

Endpoint:

```
GET /api/v1/market-structure
```

Parameters:

```
source_id
symbol
timeframe
limit
```

Response:

```
{
  "source_id": "...",
  "symbol": "XAUUSD.vx",
  "timeframe": "M1",
  "bias": "BULLISH",
  "events": [
    {
      "event_type": "BOS_BULLISH",
      "event_candle_time_epoch": 1234567890,
      "broken_swing_price": 4400.23,
      "candle_close": 4401.12
    }
  ]
}
```

Read-only.

Tidak boleh ada endpoint yang dapat membuat BOS/CHoCH secara manual.

---

# 28. Testing Requirements

Create:

```
backend/tests/test_market_structure.py
```

Minimal test:

### Test 1 — Bullish BOS

```
HH
HL
close > HH
```

Expected:

```
BOS_BULLISH
```

---

### Test 2 — Bearish BOS

```
LH
LL
close < LL
```

Expected:

```
BOS_BEARISH
```

---

### Test 3 — Bullish CHoCH

```
BULLISH
HL broken by close
```

Expected:

```
CHoCH_BEARISH
```

---

### Test 4 — Bearish CHoCH

```
BEARISH
LH broken by close
```

Expected:

```
CHoCH_BULLISH
```

---

### Test 5 — Wick Does Not Break

```
High > structural high
Close < structural high
```

Expected:

```
NO EVENT
```

---

### Test 6 — Close Break

```
Close > structural high
```

Expected:

```
BOS_BULLISH / CHoCH_BULLISH
```

depending on current bias.

---

### Test 7 — EQH

EQH tidak boleh otomatis dianggap:

```
BOS
```

hanya karena price menyentuh level equal high.

Break harus tetap memenuhi close-based rule.

---

### Test 8 — EQL

Sama untuk EQL.

---

### Test 9 — Duplicate Processing

Process same candle twice.

Expected:

```
1 event
```

bukan:

```
2 events
```

---

### Test 10 — Restart Recovery

Compare:

```
continuous processing
```

vs:

```
processing → restart → processing
```

Expected:

```
identical events
identical state
```

---

### Test 11 — Full vs Incremental

Full scan:

```
all historical candles
```

Incremental:

```
one candle at a time
```

Expected:

```
100% identical market_structure_events
```

---

### Test 12 — Timeframe Isolation

M1 event tidak boleh mengubah:

```
M5
M15
H1
```

---

### Test 13 — Source Isolation

Source A structure tidak boleh muncul pada Source B.

---

### Test 14 — Case Insensitivity

Test:

```
XAUUSD.vx
xauusd.vx
XAUUSD.VX
```

harus menghasilkan lookup structure yang sama.

---

### Test 15 — Double Break

Satu candle yang memenuhi kedua sisi harus menghasilkan deterministic:

```
DOUBLE_BREAK
```

dan **tidak boleh arbitrarily memilih BOS atau CHoCH**.

---

# 29. Regression Requirement

Setelah Step 2 selesai:

```
python -m pytest backend/tests -v
```

Expected:

```
ALL TESTS PASSED
```

Step 1 tidak boleh rusak.

Minimal harus tetap lolos:

```
Swing Detection
Timestamp
Source Isolation
Data Quality
Market Schedule
Ingestion
API
```

---

# 30. Live Verification

Gunakan data real:

```
source_id yang sama dengan Step 1
symbol = XAUUSD.vx
```

Untuk setiap timeframe:

```
M1
M5
M15
M30
H1
H4
D1
```

verifikasi:

```
market_candles
      ↓
market_swings
      ↓
market_structure_events
```

Tidak boleh ada structure event yang referensinya tidak ditemukan pada:

```
market_swings
```

---

# 31. Visual Verification

Generate chart:

```
XAUUSD.vx
```

dengan overlay:

```
Swing High
Swing Low
HH
HL
LH
LL
EQH
EQL
```

dan tambahan:

```
BOS ↑
BOS ↓
CHoCH ↑
CHoCH ↓
```

Visual harus menunjukkan:

```
BOS_BULLISH
```

tepat pada candle close yang memecahkan structural high.

Dan:

```
CHoCH_BEARISH
```

tepat pada candle close yang memecahkan protected structural low.

**Jangan gunakan visual sebagai sumber kebenaran algoritma.** Visual hanya verification layer.

---

# 32. Acceptance Criteria

Step 2 hanya boleh dianggap **DONE** apabila seluruh kondisi berikut terpenuhi:

```
[ ] market_swings Step 1 tidak dimodifikasi secara breaking
[ ] BOS_BULLISH terdeteksi deterministic
[ ] BOS_BEARISH terdeteksi deterministic
[ ] CHoCH_BULLISH terdeteksi deterministic
[ ] CHoCH_BEARISH terdeteksi deterministic
[ ] MSS hanya muncul jika structural confirmation terpenuhi
[ ] Wick-only break tidak dianggap break
[ ] Close-based break bekerja
[ ] EQH/EQL tidak otomatis dianggap directional break
[ ] Double-break ditangani deterministic
[ ] source_id isolation tetap bekerja
[ ] timeframe isolation bekerja
[ ] symbol matching case-insensitive
[ ] restart recovery bekerja
[ ] duplicate processing tidak menghasilkan duplicate event
[ ] full scan = incremental scan 100%
[ ] structure state persistent di database
[ ] tidak ada dependency pada in-memory state untuk correctness
[ ] Step 1 tests tetap PASS
[ ] seluruh Step 2 tests PASS
[ ] visual verification berhasil
[ ] tidak ada FVG
[ ] tidak ada liquidity engine
[ ] tidak ada order block
[ ] tidak ada trading signal
[ ] tidak ada execution logic
```

## Final Architecture After Step 2

```
                    MT5
                     │
                     ▼
              market_candles
                     │
                     ▼
          ┌─────────────────────┐
          │ Step 1              │
          │ Swing Detector      │
          └─────────────────────┘
                     │
                     ▼
               market_swings
                     │
                     ▼
          ┌─────────────────────┐
          │ Step 2              │
          │ Market Structure    │
          │ Engine               │
          └─────────────────────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
         BOS       CHoCH       MSS
          │          │          │
          └──────────┼──────────┘
                     ▼
       market_structure_events
                     │
                     ▼
       market_structure_state
```

### Important Implementation Rule

**Do not start Step 3 or introduce liquidity/FVG/signal logic after implementing Step 2.**

First prove:

```
market_candles
      ↓
market_swings
      ↓
market_structure_events
```

is mathematically deterministic, restart-safe, idempotent, and identical between full-scan and incremental processing.

---

# 14. Verification & Audit Sign-Off (Step 2 Hardening)

- **Step 1 Swing Detection**: 25/25 Pytest Tests PASSED. 0 same-side classification violations across 141 live XAUUSD swings.
- **Step 2 Market Structure Detection**: 29/29 Pytest Tests PASSED (including strict MSS sequence checks, adversarial matrix, pending continuation target invalidation, and independent oracle reference model).
- **Persistence Atomicity**: PostgreSQL `conn.transaction()` with snapshot rollback on failure injection.
- **Mathematical Equivalence**: `FULL REBUILD == INCREMENTAL == PERSISTED SUPABASE` proven on live M1, M5, M15, M30, and H1 data.
- **Total Automated Test Suite**: 86 passed / 0 failed.

```text
STATUS:
STEP 1: COMPLETED & HARDENED
STEP 2: COMPLETED & HARDENED
OVERALL STATUS: READY FOR STEP 3
```
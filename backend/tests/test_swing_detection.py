import pytest
import copy
from backend.app.features.swing_detector import (
    SwingDetector,
    swing_detector,
    get_symbol_point,
    get_symbol_tolerance_points
)
from backend.app.repositories.candle_repo import candle_repo, _memory_candles
from backend.app.repositories.swing_repo import swing_repo, _memory_swings
from backend.app.repositories.symbol_repo import symbol_repo, _memory_symbols
from backend.app.services.market_data_service import MarketDataService, market_data_service
from backend.app.core.constants import SwingType, SwingClassification, SyncType
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}

def build_candle(epoch: int, high: float, low: float, timeframe: str = "M1", symbol: str = "XAUUSD.vx", source_id: str = "test_src") -> dict:
    from datetime import datetime, timezone
    utc_str = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    return {
        "source_id": source_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_time_epoch": epoch,
        "candle_time_utc": utc_str,
        "broker_time": utc_str,
        "broker_gmt_offset": 0,
        "open": low + (high - low) * 0.5,
        "high": high,
        "low": low,
        "close": low + (high - low) * 0.5,
        "tick_volume": 100,
        "spread": 10,
        "session": "LONDON",
        "is_gap_recovered": False,
        "payload_version": "1.0.0",
        "schema_version": "1.0.0"
    }

def make_candles_series(
    highs: list[float],
    lows: list[float],
    step_seconds: int = 60,
    symbol: str = "XAUUSD.vx",
    source_id: str = "test_src",
    timeframe: str = "M1",
    base_epoch: int = 1788900000
) -> list[dict]:
    candles = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        candles.append(build_candle(
            epoch=base_epoch + i * step_seconds,
            high=float(h),
            low=float(l),
            timeframe=timeframe,
            symbol=symbol,
            source_id=source_id
        ))
    return candles


# ==============================================================================
# MANDATORY 21-ITEM REGRESSION TEST MATRIX
# ==============================================================================

def test_01_basic_n_bar_fractal():
    """
    Item 1: Basic N-bar fractal.
    Verifies that a candidate candle at index i is detected as a swing high/low
    if and only if it strictly exceeds all N bars to the left and N bars to the right.
    """
    highs = [10, 12, 15, 13, 11, 14, 18, 16, 12]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows)

    detector = SwingDetector(fractal_n=2)
    swings = detector.detect_swings_from_candles(candles, fractal_n=2, symbol="XAUUSD.vx")

    high_swings = [s for s in swings if s["swing_type"] == SwingType.HIGH.value]
    assert len(high_swings) == 2

    # High 1 at index 2 (15.0)
    assert high_swings[0]["swing_candle_time_epoch"] == candles[2]["candle_time_epoch"]
    assert high_swings[0]["swing_price"] == 15.0
    assert high_swings[0]["confirmed_at_candle_time_epoch"] == candles[2 + 2]["candle_time_epoch"]

    # High 2 at index 6 (18.0)
    assert high_swings[1]["swing_candle_time_epoch"] == candles[6]["candle_time_epoch"]
    assert high_swings[1]["swing_price"] == 18.0
    assert high_swings[1]["confirmed_at_candle_time_epoch"] == candles[6 + 2]["candle_time_epoch"]


def test_02_n_variation_n1_n2_n3():
    """
    Item 2: N variation (N=1, N=2, N=3).
    A candle peak valid for N=1 is not necessarily a peak for N=2 or N=3.
    """
    # At index 2: 15. Left=12, Right=13 (valid for N=1).
    # But right neighbor 2 is 16 (greater than 15, so invalid for N=2).
    highs = [10, 12, 15, 13, 16, 12, 10]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows)

    swings_n1 = swing_detector.detect_swings_from_candles(candles, fractal_n=1, symbol="XAUUSD.vx")
    assert any(s["swing_price"] == 15.0 for s in swings_n1 if s["swing_type"] == SwingType.HIGH.value)

    swings_n2 = swing_detector.detect_swings_from_candles(candles, fractal_n=2, symbol="XAUUSD.vx")
    assert not any(s["swing_price"] == 15.0 for s in swings_n2 if s["swing_type"] == SwingType.HIGH.value)

    swings_n3 = swing_detector.detect_swings_from_candles(candles, fractal_n=3, symbol="XAUUSD.vx")
    assert not any(s["swing_price"] == 15.0 for s in swings_n3 if s["swing_type"] == SwingType.HIGH.value)


def test_03_boundary_protection():
    """
    Item 3: Boundary protection.
    No swing may ever be confirmed without N bars on both left and right sides.
    Bar 0 must never be considered confirmed.
    """
    # 4 candles with N=2 (< 2N + 1 = 5 candles required)
    highs = [10.0, 50.0, 20.0, 10.0]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows)

    swings = swing_detector.detect_swings_from_candles(candles, fractal_n=2, symbol="XAUUSD.vx")
    assert len(swings) == 0

    # 5 candles: Edge candle cannot be a swing because it lacks right/left neighbors
    highs5 = [100.0, 20.0, 30.0, 40.0, 100.0]
    lows5 = [90.0, 15.0, 25.0, 35.0, 90.0]
    candles5 = make_candles_series(highs5, lows5)
    swings5 = swing_detector.detect_swings_from_candles(candles5, fractal_n=2, symbol="XAUUSD.vx")
    assert len(swings5) == 0


def test_04_higher_high_hh():
    """
    Item 4: Higher High (HH) classification.
    current high > previous high + tolerance -> HH.
    """
    threshold = 0.10  # 10 points on XAUUSD
    assert swing_detector.classify_swing_high(4410.00, 4400.00, threshold) == SwingClassification.HH.value
    assert swing_detector.classify_swing_high(4400.25, 4400.00, threshold) == SwingClassification.HH.value


def test_05_higher_low_hl():
    """
    Item 5: Higher Low (HL) classification.
    current low > previous low + tolerance -> HL.
    """
    threshold = 0.10
    assert swing_detector.classify_swing_low(4395.00, 4390.00, threshold) == SwingClassification.HL.value
    assert swing_detector.classify_swing_low(4390.25, 4390.00, threshold) == SwingClassification.HL.value


def test_06_lower_high_lh():
    """
    Item 6: Lower High (LH) classification.
    current high < previous high - tolerance -> LH.
    """
    threshold = 0.10
    assert swing_detector.classify_swing_high(4390.00, 4400.00, threshold) == SwingClassification.LH.value
    assert swing_detector.classify_swing_high(4399.63, 4400.23, threshold) == SwingClassification.LH.value


def test_07_lower_low_ll():
    """
    Item 7: Lower Low (LL) classification.
    current low < previous low - tolerance -> LL.
    """
    threshold = 0.10
    assert swing_detector.classify_swing_low(4385.00, 4390.00, threshold) == SwingClassification.LL.value
    assert swing_detector.classify_swing_low(4389.75, 4390.00, threshold) == SwingClassification.LL.value


def test_08_equal_high_eqh():
    """
    Item 8: Equal High (EQH) classification within tolerance.
    |current high - previous high| <= tolerance -> EQH.
    """
    threshold = 0.10
    # Difference of 0.05 <= 0.10
    assert swing_detector.classify_swing_high(4400.05, 4400.00, threshold) == SwingClassification.EQH.value
    assert swing_detector.classify_swing_high(4399.95, 4400.00, threshold) == SwingClassification.EQH.value


def test_09_equal_low_eql():
    """
    Item 9: Equal Low (EQL) classification within tolerance.
    |current low - previous low| <= tolerance -> EQL.
    """
    threshold = 0.10
    # Difference of 0.05 <= 0.10
    assert swing_detector.classify_swing_low(4390.05, 4390.00, threshold) == SwingClassification.EQL.value
    assert swing_detector.classify_swing_low(4389.95, 4390.00, threshold) == SwingClassification.EQL.value


def test_10_exact_tolerance_boundary():
    """
    Item 10: Exact tolerance boundary.
    delta == threshold_price MUST classify as EQH / EQL without floating point jitter.
    """
    threshold = 0.10
    # Exact upper boundary
    assert swing_detector.classify_swing_high(4400.33, 4400.23, threshold) == SwingClassification.EQH.value
    # Exact lower boundary
    assert swing_detector.classify_swing_high(4400.13, 4400.23, threshold) == SwingClassification.EQH.value
    # Exact upper boundary for low
    assert swing_detector.classify_swing_low(4390.94, 4390.84, threshold) == SwingClassification.EQL.value
    # Exact lower boundary for low
    assert swing_detector.classify_swing_low(4390.74, 4390.84, threshold) == SwingClassification.EQL.value


def test_11_outside_tolerance():
    """
    Item 11: Outside tolerance boundary.
    delta > threshold_price MUST classify as directional (HH / LH / HL / LL), never EQH / EQL.
    """
    threshold = 0.10
    # Just outside upper boundary (+0.1001)
    assert swing_detector.classify_swing_high(4400.3301, 4400.23, threshold) == SwingClassification.HH.value
    # Just outside lower boundary (-0.1001)
    assert swing_detector.classify_swing_high(4400.1299, 4400.23, threshold) == SwingClassification.LH.value
    # Just outside lower boundary for low (-0.1001)
    assert swing_detector.classify_swing_low(4390.7399, 4390.84, threshold) == SwingClassification.LL.value
    # Just outside upper boundary for low (+0.1001)
    assert swing_detector.classify_swing_low(4390.9401, 4390.84, threshold) == SwingClassification.HL.value


def test_12_first_same_side_swing_null_initial():
    """
    Item 12: First same-side swing = NULL / INITIAL.
    When previous same-side swing is None, classification must be None (not forced to HH/HL/LH/LL).
    """
    threshold = 0.10
    assert swing_detector.classify_swing_high(4400.23, None, threshold) is None
    assert swing_detector.classify_swing_low(4390.84, None, threshold) is None


def test_13_high_compared_only_with_high():
    """
    Item 13: HIGH compared ONLY with previous confirmed HIGH (same-side isolation).
    HIGH is never compared with LOW.
    """
    threshold = 0.10
    # Scenario:
    # High 1: 4400.00
    # Low 1:  4380.00
    # High 2: 4395.00
    # If High 2 were compared against Low 1 (4380.00), it would be wrongly labeled HH!
    # But when compared ONLY against High 1 (4400.00), it is correctly labeled LH!
    prev_high = 4400.00
    curr_high = 4395.00
    assert swing_detector.classify_swing_high(curr_high, prev_high, threshold) == SwingClassification.LH.value


def test_14_low_compared_only_with_low():
    """
    Item 14: LOW compared ONLY with previous confirmed LOW (same-side isolation).
    LOW is never compared with HIGH.
    """
    threshold = 0.10
    # Scenario:
    # Low 1:  4380.00
    # High 1: 4400.00
    # Low 2:  4385.00
    # If Low 2 were compared against High 1 (4400.00), it would be wrongly labeled LL!
    # But when compared ONLY against Low 1 (4380.00), it is correctly labeled HL!
    prev_low = 4380.00
    curr_low = 4385.00
    assert swing_detector.classify_swing_low(curr_low, prev_low, threshold) == SwingClassification.HL.value


def test_15_deterministic_confirmation_timestamp():
    """
    Item 15: Deterministic confirmation timestamp.
    confirmed_at_candle_time_epoch = candle_time_epoch of candle i + N.
    - Strictly derived from closed candles; never system clock, request time, or DB timestamp.
    - Two runs on the same data yield 100% identical timestamps.
    """
    highs = [10, 12, 16, 13, 11, 14, 20, 15, 12, 14, 22, 17, 13]
    lows = [h - 5 for h in highs]
    candles = make_candles_series(highs, lows, step_seconds=60)

    for n in [1, 2, 3]:
        swings1 = swing_detector.detect_swings_from_candles(candles, fractal_n=n, symbol="XAUUSD.vx")
        swings2 = swing_detector.detect_swings_from_candles(candles, fractal_n=n, symbol="XAUUSD.vx")

        assert len(swings1) > 0
        for s1, s2 in zip(swings1, swings2):
            cand_epoch = s1["swing_candle_time_epoch"]
            conf_epoch = s1["confirmed_at_candle_time_epoch"]

            # Must be strictly greater than swing candle time
            assert conf_epoch > cand_epoch

            # Must match index + N in closed candle series
            cand_idx = next(i for i, c in enumerate(candles) if c["candle_time_epoch"] == cand_epoch)
            assert conf_epoch == candles[cand_idx + n]["candle_time_epoch"]

            # Idempotent recomputation
            assert s1["confirmed_at_candle_time_epoch"] == s2["confirmed_at_candle_time_epoch"]
            assert s1["classification"] == s2["classification"]


def test_16_idempotent_reprocessing():
    """
    Item 16: Idempotent reprocessing.
    Reprocessing the exact same dataset produces zero duplicates and identical records.
    """
    source_id = "test_src_idempotent"
    symbol = "XAUUSD.vx"
    tf = "M5"

    highs = [10, 12, 15, 13, 11, 14, 18, 16, 12]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows, symbol=symbol, source_id=source_id, timeframe=tf)

    candle_repo.upsert_candles(candles)

    # First pass
    c1 = market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)
    swings1 = swing_repo.get_swings(source_id, symbol, tf)

    # Second pass
    c2 = market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)
    swings2 = swing_repo.get_swings(source_id, symbol, tf)

    assert c1 == c2 == 3
    assert len(swings1) == len(swings2) == 3


def test_17_full_rebuild_vs_incremental_equivalence():
    """
    Item 17: Full rebuild vs Incremental equivalence.
    Mode A (Full Rebuild) and Mode B (Incremental Sliding Window) must yield 100%
    mathematically identical records for the same dataset:
    - swing_candle_time_epoch
    - swing_type
    - swing_price
    - confirmed_at_candle_time_epoch
    - classification
    - fractal_n
    """
    source_full = "src_full_equiv"
    source_inc = "src_inc_equiv"
    symbol = "XAUUSD.vx"
    tf = "M1"

    highs = [4380, 4385, 4395, 4388, 4375, 4386, 4402, 4390, 4378, 4392, 4410, 4398, 4385, 4390, 4380]
    lows  = [4370, 4372, 4375, 4368, 4360, 4370, 4380, 4372, 4365, 4375, 4388, 4380, 4370, 4372, 4368]
    candles_full = make_candles_series(highs, lows, symbol=symbol, source_id=source_full, timeframe=tf)
    candles_inc = make_candles_series(highs, lows, symbol=symbol, source_id=source_inc, timeframe=tf)

    # A. Execute FULL SCAN
    candle_repo.upsert_candles(candles_full)
    market_data_service.recalculate_swings_full(source_full, symbol, tf, fractal_n=2)
    swings_full = swing_repo.get_swings(source_full, symbol, tf, limit=100)
    swings_full.sort(key=lambda x: (x["swing_candle_time_epoch"], x["swing_type"]))

    # B. Execute INCREMENTAL candle-by-candle
    for c in candles_inc:
        candle_repo.upsert_candles([c])
        market_data_service.process_live_candle_swing(source_inc, symbol, tf, fractal_n=2)

    swings_inc = swing_repo.get_swings(source_inc, symbol, tf, limit=100)
    swings_inc.sort(key=lambda x: (x["swing_candle_time_epoch"], x["swing_type"]))

    assert len(swings_full) == len(swings_inc)
    assert len(swings_full) > 0

    for sf, si in zip(swings_full, swings_inc):
        assert sf["swing_candle_time_epoch"] == si["swing_candle_time_epoch"]
        assert sf["swing_type"] == si["swing_type"]
        assert sf["swing_price"] == si["swing_price"]
        assert sf["confirmed_at_candle_time_epoch"] == si["confirmed_at_candle_time_epoch"]
        assert sf["classification"] == si["classification"]
        assert sf["fractal_n"] == si["fractal_n"]


def test_18_restart_recovery_from_db():
    """
    Item 18: Restart recovery from DB.
    Incremental processing reconstructs previous swing state from database/repository,
    independent of Python memory caches.
    """
    source_id = "src_restart_recovery_db"
    symbol = "XAUUSD.vx"
    tf = "M1"

    # Pre-existing confirmed swing high
    prior_swing = {
        "source_id": source_id,
        "symbol": symbol,
        "timeframe": tf,
        "swing_type": SwingType.HIGH.value,
        "swing_candle_time_epoch": 1788900000,
        "swing_price": 4350.0,
        "confirmed_at_candle_time_epoch": 1788900120,
        "classification": None,
        "fractal_n": 2
    }
    swing_repo.upsert_swings([prior_swing])

    # Now simulate a fresh process restart with new candle series forming a peak at 4360.0
    fresh_service = MarketDataService()
    highs = [4340.0, 4355.0, 4360.0, 4352.0, 4345.0]
    lows = [h - 10 for h in highs]
    candles = make_candles_series(highs, lows, symbol=symbol, source_id=source_id, timeframe=tf)
    candle_repo.upsert_candles(candles)

    # Incremental process with fresh service instance
    fresh_service.process_live_candle_swing(source_id, symbol, tf, fractal_n=2)

    all_swings = swing_repo.get_swings(source_id, symbol, tf)
    newest = [s for s in all_swings if s["swing_price"] == 4360.0]
    assert len(newest) == 1
    # Successfully classified against the recovered prior swing from DB (4360 > 4350 -> HH)
    assert newest[0]["classification"] == SwingClassification.HH.value


def test_19_case_insensitive_symbol():
    """
    Item 19: Case-insensitive symbol matching.
    XAUUSD.vx, xauusd.vx, and XAUUSD.VX must resolve to identical symbol configurations
    and return identical results.
    """
    source_id = "src_case_insensitivity_test"
    highs = [10, 12, 15, 13, 11, 14, 18, 16, 12]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows, symbol="XAUUSD.vx", source_id=source_id, timeframe="M1")
    candle_repo.upsert_candles(candles)

    market_data_service.recalculate_swings_full(source_id, "XAUUSD.vx", "M1", fractal_n=2)

    res_exact = swing_repo.get_swings(source_id, "XAUUSD.vx", "M1")
    res_lower = swing_repo.get_swings(source_id, "xauusd.vx", "M1")
    res_upper = swing_repo.get_swings(source_id, "XAUUSD.VX", "M1")

    assert len(res_exact) == len(res_lower) == len(res_upper) == 3


def test_20_cross_timeframe_isolation():
    """
    Item 20: Cross-timeframe isolation.
    M15 and H1 timeframes remain isolated and do not contaminate each other.
    """
    source_id = "src_tf_isolation_test"
    symbol = "XAUUSD.vx"

    highs_m15 = [10, 12, 15, 13, 11, 14, 18, 16, 12]
    lows_m15 = [h - 2 for h in highs_m15]
    candles_m15 = make_candles_series(highs_m15, lows_m15, step_seconds=900, symbol=symbol, source_id=source_id, timeframe="M15")

    highs_h1 = [100, 120, 150, 130, 110, 140, 180, 160, 120]
    lows_h1 = [h - 20 for h in highs_h1]
    candles_h1 = make_candles_series(highs_h1, lows_h1, step_seconds=3600, symbol=symbol, source_id=source_id, timeframe="H1")

    candle_repo.upsert_candles(candles_m15)
    candle_repo.upsert_candles(candles_h1)

    market_data_service.recalculate_swings_full(source_id, symbol, "M15", fractal_n=2)
    market_data_service.recalculate_swings_full(source_id, symbol, "H1", fractal_n=2)

    swings_m15 = swing_repo.get_swings(source_id, symbol, "M15")
    swings_h1 = swing_repo.get_swings(source_id, symbol, "H1")

    assert len(swings_m15) > 0
    assert len(swings_h1) > 0
    assert all(s["timeframe"] == "M15" for s in swings_m15)
    assert all(s["timeframe"] == "H1" for s in swings_h1)
    assert all(s["swing_price"] < 50 for s in swings_m15)
    assert all(s["swing_price"] > 50 for s in swings_h1)


def test_21_existing_regression_and_user_4400_23_to_4399_63_lh():
    """
    Item 21: Existing regression tests & specific user case:
    Previous HIGH: 4400.23
    Current HIGH:  4399.63
    Tolerance:     0.10
    Expected:      LH (Lower High), NEVER HH.
    """
    threshold = 0.10

    # User specified exact case
    prev_high = 4400.23
    curr_high = 4399.63
    classification = swing_detector.classify_swing_high(curr_high, prev_high, threshold)
    assert classification == SwingClassification.LH.value
    assert classification != SwingClassification.HH.value

    # Full user sequence verification
    highs = [4404.62, 4400.23, 4399.63, 4413.57, 4434.00]
    expected_high_classes = [None, SwingClassification.LH.value, SwingClassification.LH.value, SwingClassification.HH.value, SwingClassification.HH.value]

    prev_h = None
    for h, exp in zip(highs, expected_high_classes):
        actual = swing_detector.classify_swing_high(h, prev_h, threshold)
        assert actual == exp, f"Expected {exp} for high {h} against prev {prev_h}, got {actual}"
        prev_h = h

    lows = [4400.85, 4394.91, 4391.91, 4390.84]
    expected_low_classes = [None, SwingClassification.LL.value, SwingClassification.LL.value, SwingClassification.LL.value]

    prev_l = None
    for l, exp in zip(lows, expected_low_classes):
        actual = swing_detector.classify_swing_low(l, prev_l, threshold)
        assert actual == exp, f"Expected {exp} for low {l} against prev {prev_l}, got {actual}"
        prev_l = l


# ==============================================================================
# SUPPLEMENTARY ARCHITECTURAL & API TESTS
# ==============================================================================

def test_22_api_endpoint_read_only():
    """Test 22: GET /api/v1/swings read-only endpoint returns market swings."""
    source_id = "663767a265a3d4c29c3cff1dca10fee9966fb053ea8dfd613a3f70a73a9db757"
    symbol = "XAUUSD.vx"
    tf = "M15"

    highs = [10, 12, 15, 13, 11, 14, 18, 16, 12]
    lows = [h - 2 for h in highs]
    candles = make_candles_series(highs, lows, symbol=symbol, source_id=source_id, timeframe=tf)
    candle_repo.upsert_candles(candles)
    market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)

    res = client.get(
        f"/api/v1/swings?source_id={source_id}&symbol={symbol}&timeframe={tf}",
        headers=AUTH_HEADERS
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    assert data["symbol"] == symbol
    assert data["timeframe"] == tf
    assert data["total_count"] == 3
    assert len(data["swings"]) == 3


def test_23_instrument_point_metadata_resolution():
    """
    Test 23: Instrument point metadata resolution.
    Point sizes for instruments are resolved via symbol_repo (point, digits)
    with case-insensitivity.
    """
    source_id = "test_point_res_src"
    symbol_repo.upsert_symbol({"source_id": source_id, "symbol": "XAUUSD.VX", "point": 0.01, "digits": 2})
    symbol_repo.upsert_symbol({"source_id": source_id, "symbol": "BTCUSD.RAW", "point": 1.0, "digits": 1})
    symbol_repo.upsert_symbol({"source_id": source_id, "symbol": "EURUSD.STD", "point": 0.00001, "digits": 5})

    assert get_symbol_point("XAUUSD.VX", source_id) == 0.01
    assert get_symbol_point("xauusd.vx", source_id) == 0.01
    assert get_symbol_point("BTCUSD.raw", source_id) == 1.0
    assert get_symbol_point("eurusd.std", source_id) == 0.00001


def test_24_production_restart_recovery_full_workflow():
    """
    Test 24: Production restart recovery full workflow.
    Compares clean full scan against state produced by partial full scan + restart + incremental.
    Result MUST be 100% identical.
    """
    source_id = "restart_recovery_workflow_src"
    symbol = "XAUUSD.vx"
    tf = "M1"

    highs = [4390, 4395, 4400, 4395, 4390, 4405, 4410, 4402, 4395, 4412, 4420, 4415, 4410]
    lows  = [h - 10 for h in highs]
    candles = make_candles_series(highs, lows, symbol=symbol, source_id=source_id, timeframe=tf)

    # Initial history up to index 9
    candle_repo.upsert_candles(candles[:10])
    market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)

    # Fresh instance simulating restart
    fresh_service = MarketDataService()
    for c in candles[10:]:
        candle_repo.upsert_candles([c])
        fresh_service.process_live_candle_swing(source_id, symbol, tf, fractal_n=2)

    restart_swings = swing_repo.get_swings(source_id, symbol, tf, limit=100)
    restart_swings.sort(key=lambda x: (x["swing_candle_time_epoch"], x["swing_type"]))

    # Ground truth: clean full scan
    gt_source = "gt_clean_full_scan_src"
    gt_candles = make_candles_series(highs, lows, symbol=symbol, source_id=gt_source, timeframe=tf)
    candle_repo.upsert_candles(gt_candles)
    fresh_service.recalculate_swings_full(gt_source, symbol, tf, fractal_n=2)
    gt_swings = swing_repo.get_swings(gt_source, symbol, tf, limit=100)
    gt_swings.sort(key=lambda x: (x["swing_candle_time_epoch"], x["swing_type"]))

    assert len(restart_swings) == len(gt_swings)
    for r_s, gt_s in zip(restart_swings, gt_swings):
        assert r_s["swing_candle_time_epoch"] == gt_s["swing_candle_time_epoch"]
        assert r_s["swing_type"] == gt_s["swing_type"]
        assert r_s["swing_price"] == gt_s["swing_price"]
        assert r_s["confirmed_at_candle_time_epoch"] == gt_s["confirmed_at_candle_time_epoch"]
        assert r_s["classification"] == gt_s["classification"]
        assert r_s["fractal_n"] == gt_s["fractal_n"]


def test_25_historical_rebuild_classification_recomputation():
    """
    Test 25: Classification recomputation on historical backfill.
    When intermediate historical candles are inserted, full recalculation
    recomputes classifications correctly relative to new chronological sequence.
    """
    source_id = "rebuild_recompute_src_25"
    symbol = "XAUUSD.vx"
    tf = "M1"

    # Step 1: Initial sequence High 1 = 4400.00, High 2 = 4405.00
    highs_1 = [4390, 4395, 4400.00, 4395, 4390, 4402, 4405.00, 4401, 4395]
    lows_1  = [h - 10 for h in highs_1]
    c_1 = make_candles_series(highs_1, lows_1, symbol=symbol, source_id=source_id, timeframe=tf, base_epoch=10000)
    candle_repo.upsert_candles(c_1)
    market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)

    # Step 2: Backfill inserts intermediate High 4400.03 (EQH to 4400.00)
    highs_rebuild = [4390, 4395, 4400.00, 4395, 4390, 4398, 4400.03, 4397, 4392, 4402, 4405.00, 4401, 4395]
    lows_rebuild  = [h - 10 for h in highs_rebuild]
    c_rebuild = make_candles_series(highs_rebuild, lows_rebuild, symbol=symbol, source_id=source_id, timeframe=tf, base_epoch=10000)
    candle_repo.upsert_candles(c_rebuild)

    market_data_service.recalculate_swings_full(source_id, symbol, tf, fractal_n=2)

    swings = swing_repo.get_swings(source_id, symbol, tf)
    high_4400_03 = next(s for s in swings if s["swing_price"] == 4400.03)
    high_4405 = next(s for s in swings if s["swing_price"] == 4405.00)

    assert high_4400_03["classification"] == SwingClassification.EQH.value
    assert high_4405["classification"] == SwingClassification.HH.value

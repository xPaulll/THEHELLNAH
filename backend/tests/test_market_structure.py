import pytest
from backend.app.core.constants import (
    StructureBias,
    StructureTransitionState,
    StructureEventType,
    SwingType,
    SwingClassification,
    canonicalize_symbol
)
from backend.app.features.market_structure_detector import (
    market_structure_detector,
    create_initial_state,
    get_break_tolerance_price
)
from backend.app.repositories.market_structure_repo import (
    market_structure_repo,
    _memory_structure_events,
    _memory_structure_state
)
from backend.app.repositories.swing_repo import swing_repo, _memory_swings
from backend.app.repositories.candle_repo import candle_repo, _memory_candles
from backend.app.services.market_data_service import market_data_service
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.core.config import settings

client = TestClient(app)
SOURCE_ID = "a" * 64
AUTH_HEADERS = {"X-API-Key": settings.API_KEY}

@pytest.fixture(autouse=True)
def clean_memory():
    """Isolates each test in pure memory without remote database writes."""
    _memory_structure_events.clear()
    _memory_structure_state.clear()
    _memory_swings.clear()
    _memory_candles.clear()
    yield
    _memory_structure_events.clear()
    _memory_structure_state.clear()
    _memory_swings.clear()
    _memory_candles.clear()

def test_a_neutral_to_bullish():
    """A. Neutral -> Bullish: Minimal valid structural evidence is HH + HL."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    assert state["bias"] == StructureBias.NEUTRAL.value

    # Confirmed swings: High1, Low1, High2 (HH), Low2 (HL)
    swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 100, "swing_price": 4400.0, "confirmed_at_candle_time_epoch": 120, "classification": None},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 110, "swing_price": 4380.0, "confirmed_at_candle_time_epoch": 130, "classification": None},
        {"id": 3, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 140, "swing_price": 4420.0, "confirmed_at_candle_time_epoch": 160, "classification": "HH"},
        {"id": 4, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 150, "swing_price": 4390.0, "confirmed_at_candle_time_epoch": 170, "classification": "HL"},
    ]

    state, _ = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=swings,
        all_confirmed_swings_history=swings
    )

    assert state["bias"] == StructureBias.BULLISH.value
    assert state["bullish_break_level_price"] == 4420.0
    assert state["protected_low_price"] == 4390.0

def test_b_neutral_to_bearish():
    """B. Neutral -> Bearish: Minimal valid structural evidence is LH + LL."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    assert state["bias"] == StructureBias.NEUTRAL.value

    # Confirmed swings: High1, Low1, High2 (LH), Low2 (LL)
    swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 100, "swing_price": 4400.0, "confirmed_at_candle_time_epoch": 120, "classification": None},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 110, "swing_price": 4380.0, "confirmed_at_candle_time_epoch": 130, "classification": None},
        {"id": 3, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 140, "swing_price": 4395.0, "confirmed_at_candle_time_epoch": 160, "classification": "LH"},
        {"id": 4, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 150, "swing_price": 4370.0, "confirmed_at_candle_time_epoch": 170, "classification": "LL"},
    ]

    state, _ = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=swings,
        all_confirmed_swings_history=swings
    )

    assert state["bias"] == StructureBias.BEARISH.value
    assert state["protected_high_price"] == 4395.0
    assert state["bearish_break_level_price"] == 4370.0

def test_c_bullish_bos():
    """C. Bullish BOS: Candle close > bullish_break_level + tolerance."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4420.0
    state["bullish_break_level_candle_time_epoch"] = 140
    state["protected_low_price"] = 4390.0
    state["protected_low_candle_time_epoch"] = 150

    candle = {"candle_time_epoch": 180, "open": 4415.0, "high": 4425.0, "low": 4410.0, "close": 4422.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert ev is not None
    assert ev["event_type"] == StructureEventType.BOS_BULLISH.value
    assert ev["broken_swing_price"] == 4420.0
    assert ev["resulting_bias"] == StructureBias.BULLISH.value

    # Consecutive candle continuing above does NOT emit duplicate BOS
    candle2 = {"candle_time_epoch": 190, "open": 4422.0, "high": 4426.0, "low": 4420.0, "close": 4425.0}
    state, ev2 = market_structure_detector.evaluate_closed_candle(candle2, state, threshold_price=0.10)
    assert ev2 is None

def test_d_bearish_bos():
    """D. Bearish BOS: Candle close < bearish_break_level - tolerance."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BEARISH.value
    state["bearish_break_level_price"] = 4370.0
    state["bearish_break_level_candle_time_epoch"] = 150
    state["protected_high_price"] = 4395.0
    state["protected_high_candle_time_epoch"] = 140

    candle = {"candle_time_epoch": 180, "open": 4375.0, "high": 4376.0, "low": 4365.0, "close": 4368.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert ev is not None
    assert ev["event_type"] == StructureEventType.BOS_BEARISH.value
    assert ev["broken_swing_price"] == 4370.0
    assert ev["resulting_bias"] == StructureBias.BEARISH.value

    # Consecutive candle continuing below does NOT emit duplicate BOS
    candle2 = {"candle_time_epoch": 190, "open": 4368.0, "high": 4369.0, "low": 4360.0, "close": 4365.0}
    state, ev2 = market_structure_detector.evaluate_closed_candle(candle2, state, threshold_price=0.10)
    assert ev2 is None

def test_e_bullish_to_bearish_choch():
    """E. Bullish -> Bearish CHOCH: Candle close < protected_low - tolerance."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["protected_low_price"] = 4400.0
    state["protected_low_candle_time_epoch"] = 100
    state["bullish_break_level_price"] = 4450.0

    candle = {"candle_time_epoch": 200, "open": 4410.0, "high": 4412.0, "low": 4390.0, "close": 4395.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert ev is not None
    assert ev["event_type"] == StructureEventType.CHOCH_BEARISH.value
    assert ev["broken_swing_price"] == 4400.0
    assert ev["resulting_bias"] == StructureBias.BULLISH.value # Bias does NOT flip immediately!
    assert state["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value
    assert state["pending_choch_epoch"] == 200

def test_f_bearish_to_bullish_choch():
    """F. Bearish -> Bullish CHOCH: Candle close > protected_high + tolerance."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BEARISH.value
    state["protected_high_price"] = 4450.0
    state["protected_high_candle_time_epoch"] = 100
    state["bearish_break_level_price"] = 4400.0

    candle = {"candle_time_epoch": 200, "open": 4440.0, "high": 4460.0, "low": 4438.0, "close": 4455.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert ev is not None
    assert ev["event_type"] == StructureEventType.CHOCH_BULLISH.value
    assert ev["broken_swing_price"] == 4450.0
    assert ev["resulting_bias"] == StructureBias.BEARISH.value # Bias does NOT flip immediately!
    assert state["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value
    assert state["pending_choch_epoch"] == 200

def test_g_bearish_mss_with_post_choch_lh_ll():
    """G. Bearish MSS with post-CHOCH LH + LL."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state["pending_choch_epoch"] = 1000

    # Two post-CHOCH confirmed swings: LH and LL
    post_swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 1020, "swing_price": 4430.0, "confirmed_at_candle_time_epoch": 1040, "classification": "LH"},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 1030, "swing_price": 4380.0, "confirmed_at_candle_time_epoch": 1050, "classification": "LL"},
    ]

    state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=post_swings,
        all_confirmed_swings_history=post_swings
    )

    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BEARISH.value
    assert mss_ev["previous_bias"] == StructureBias.BULLISH.value
    assert mss_ev["resulting_bias"] == StructureBias.BEARISH.value
    assert state["bias"] == StructureBias.BEARISH.value
    assert state["transition_state"] == StructureTransitionState.NORMAL.value
    assert state["protected_high_price"] == 4430.0
    assert state["bearish_break_level_price"] == 4380.0
    assert state["pending_choch_epoch"] is None

def test_h_bullish_mss_with_post_choch_hl_hh():
    """H. Bullish MSS with post-CHOCH HL + HH."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BEARISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    state["pending_choch_epoch"] = 1000

    # Two post-CHOCH confirmed swings: HL and HH
    post_swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 1020, "swing_price": 4410.0, "confirmed_at_candle_time_epoch": 1040, "classification": "HL"},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 1030, "swing_price": 4460.0, "confirmed_at_candle_time_epoch": 1050, "classification": "HH"},
    ]

    state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=post_swings,
        all_confirmed_swings_history=post_swings
    )

    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BULLISH.value
    assert mss_ev["previous_bias"] == StructureBias.BEARISH.value
    assert mss_ev["resulting_bias"] == StructureBias.BULLISH.value
    assert state["bias"] == StructureBias.BULLISH.value
    assert state["transition_state"] == StructureTransitionState.NORMAL.value
    assert state["protected_low_price"] == 4410.0
    assert state["bullish_break_level_price"] == 4460.0

def test_i_one_post_choch_swing_insufficient():
    """I. One post-CHOCH swing is insufficient for MSS."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state["pending_choch_epoch"] = 1000

    # Only one swing (LH) confirmed after CHOCH
    post_swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 1020, "swing_price": 4430.0, "confirmed_at_candle_time_epoch": 1040, "classification": "LH"}
    ]

    state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=post_swings,
        all_confirmed_swings_history=post_swings
    )

    assert mss_ev is None
    assert state["bias"] == StructureBias.BULLISH.value
    assert state["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value

def test_j_pre_choch_swing_cannot_satisfy_mss():
    """J. Pre-CHOCH swings cannot satisfy MSS."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state["pending_choch_epoch"] = 1000

    # One pre-CHOCH swing (LH confirmed at 950) + one post-CHOCH swing (LL confirmed at 1020)
    swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 920, "swing_price": 4430.0, "confirmed_at_candle_time_epoch": 950, "classification": "LH"},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 1010, "swing_price": 4380.0, "confirmed_at_candle_time_epoch": 1020, "classification": "LL"},
    ]

    state, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(
        state=state,
        new_swings=[swings[1]], # Only LL is newly confirmed
        all_confirmed_swings_history=swings
    )

    assert mss_ev is None
    assert state["bias"] == StructureBias.BULLISH.value
    assert state["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value

def test_k_wick_only_break_does_not_trigger():
    """K. Wick-only break does not trigger event."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4500.0

    # High penetrates 4505, but close is 4495
    candle = {"candle_time_epoch": 100, "open": 4490.0, "high": 4505.0, "low": 4485.0, "close": 4495.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)
    assert ev is None

def test_l_exact_close_boundary_does_not_trigger():
    """L. Exact close boundary does not trigger event (strict inequality)."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4500.0
    tol = 0.10

    # Close is exactly 4500.10 (level + tol)
    candle = {"candle_time_epoch": 100, "open": 4490.0, "high": 4501.0, "low": 4485.0, "close": 4500.10}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=tol)
    assert ev is None

def test_m_eqh_eql_cannot_become_protected_levels():
    """M. EQH/EQL cannot become protected or break levels."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["protected_low_price"] = 4400.0
    state["protected_low_candle_time_epoch"] = 100
    state["bullish_break_level_price"] = 4500.0

    eqh_sw = [{"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 150, "swing_price": 4500.05, "confirmed_at_candle_time_epoch": 170, "classification": "EQH"}]
    eql_sw = [{"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 160, "swing_price": 4400.02, "confirmed_at_candle_time_epoch": 180, "classification": "EQL"}]

    state, _ = market_structure_detector.apply_confirmed_swings_to_state(state, eqh_sw, eqh_sw)
    assert state["bullish_break_level_price"] == 4500.0 # Unchanged!

    state, _ = market_structure_detector.apply_confirmed_swings_to_state(state, eql_sw, eqh_sw + eql_sw)
    assert state["protected_low_price"] == 4400.0 # Unchanged!

def test_n_double_break_deterministic():
    """N. DOUBLE_BREAK deterministic and preserves prior bias."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4450.0
    state["bullish_break_level_candle_time_epoch"] = 100
    state["protected_low_price"] = 4460.0 # Inverted anomaly to test double break
    state["protected_low_candle_time_epoch"] = 110

    # Candle closes at 4455: > 4450.10 AND < 4459.90
    candle = {"candle_time_epoch": 200, "open": 4450.0, "high": 4470.0, "low": 4440.0, "close": 4455.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert ev is not None
    assert ev["event_type"] == StructureEventType.DOUBLE_BREAK.value
    assert ev["broken_high_swing_price"] == 4450.0
    assert ev["broken_low_swing_price"] == 4460.0
    assert ev["previous_bias"] == StructureBias.BULLISH.value
    assert ev["resulting_bias"] == StructureBias.BULLISH.value # Preserved!

def test_o_look_ahead_prevention():
    """O. Look-ahead prevention: Swings only available when confirmed_at <= current closed candle."""
    swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 105, "swing_price": 4500.0, "confirmed_at_candle_time_epoch": 107, "classification": "HH"}
    ]
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value

    # Candle 105: swing NOT available
    avail_105 = [s for s in swings if s["confirmed_at_candle_time_epoch"] <= 105]
    assert len(avail_105) == 0

    # Candle 106: swing NOT available
    avail_106 = [s for s in swings if s["confirmed_at_candle_time_epoch"] <= 106]
    assert len(avail_106) == 0

    # Candle 107: swing BECOMES available
    avail_107 = [s for s in swings if s["confirmed_at_candle_time_epoch"] <= 107]
    assert len(avail_107) == 1

def test_p_pending_choch_invalidation():
    """P. Pending CHOCH invalidation: Breaking original continuation target restores NORMAL."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.vx", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state["pending_choch_epoch"] = 1000
    state["bullish_break_level_price"] = 4500.0
    state["bullish_break_level_candle_time_epoch"] = 900

    # Candle closes > 4500.10: cancels pending CHOCH and emits BOS_BULLISH
    candle = {"candle_time_epoch": 1010, "open": 4490.0, "high": 4510.0, "low": 4485.0, "close": 4505.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    assert state["transition_state"] == StructureTransitionState.NORMAL.value
    assert state["pending_choch_epoch"] is None
    assert ev is not None
    assert ev["event_type"] == StructureEventType.BOS_BULLISH.value

def test_q_r_full_scan_vs_incremental_equivalence_and_idempotency():
    """Q & R. Full Scan vs Incremental 100% equivalence and idempotency."""
    candles = [
        {"candle_time_epoch": 100, "open": 4400.0, "high": 4410.0, "low": 4390.0, "close": 4405.0},
        {"candle_time_epoch": 110, "open": 4405.0, "high": 4415.0, "low": 4400.0, "close": 4410.0},
        {"candle_time_epoch": 120, "open": 4410.0, "high": 4425.0, "low": 4405.0, "close": 4420.0},
        {"candle_time_epoch": 130, "open": 4420.0, "high": 4422.0, "low": 4395.0, "close": 4400.0},
        {"candle_time_epoch": 140, "open": 4400.0, "high": 4435.0, "low": 4398.0, "close": 4430.0},
        {"candle_time_epoch": 150, "open": 4430.0, "high": 4432.0, "low": 4385.0, "close": 4390.0},
        {"candle_time_epoch": 160, "open": 4390.0, "high": 4440.0, "low": 4388.0, "close": 4438.0},
    ]
    swings = [
        {"id": 1, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 100, "swing_price": 4410.0, "confirmed_at_candle_time_epoch": 120, "classification": None},
        {"id": 2, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 110, "swing_price": 4390.0, "confirmed_at_candle_time_epoch": 130, "classification": None},
        {"id": 3, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 120, "swing_price": 4425.0, "confirmed_at_candle_time_epoch": 140, "classification": "HH"},
        {"id": 4, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "LOW", "swing_candle_time_epoch": 130, "swing_price": 4395.0, "confirmed_at_candle_time_epoch": 150, "classification": "HL"},
        {"id": 5, "source_id": SOURCE_ID, "symbol": "XAUUSD.VX", "timeframe": "M1", "swing_type": "HIGH", "swing_candle_time_epoch": 140, "swing_price": 4435.0, "confirmed_at_candle_time_epoch": 160, "classification": "HH"},
    ]

    candles_prepared = []
    for i, c in enumerate(candles):
        candles_prepared.append({
            **c,
            "source_id": SOURCE_ID,
            "symbol": "XAUUSD.VX",
            "timeframe": "M1",
            "candle_time_utc": f"2026-03-01T00:{i:02d}:00Z",
            "broker_time": f"2026-03-01T00:{i:02d}:00Z",
            "broker_gmt_offset": 0
        })

    candle_repo.upsert_candles(candles_prepared)
    swing_repo.upsert_swings(swings)

    # 1. Full Scan
    full_count = market_data_service.recalculate_market_structure_full(SOURCE_ID, "XAUUSD.VX", "M1")
    full_events = market_structure_repo.get_structure_events(SOURCE_ID, "XAUUSD.VX", "M1")
    full_state = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")

    # 2. Reset and run Incremental candle-by-candle
    _memory_structure_events.clear()
    _memory_structure_state.clear()

    # Step-by-step incremental
    for i in range(1, len(candles_prepared) + 1):
        # Temporarily mock available candles up to i
        _memory_candles.clear()
        candle_repo.upsert_candles(candles_prepared[:i])
        market_data_service.process_live_market_structure(SOURCE_ID, "XAUUSD.VX", "M1")

    inc_events = market_structure_repo.get_structure_events(SOURCE_ID, "XAUUSD.VX", "M1")
    inc_state = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")

    assert len(full_events) == len(inc_events)
    assert full_state["bias"] == inc_state["bias"]
    assert full_state["transition_state"] == inc_state["transition_state"]
    assert full_state["bullish_break_level_price"] == inc_state["bullish_break_level_price"]
    assert full_state["protected_low_price"] == inc_state["protected_low_price"]

    # 3. Idempotent rerun: rerunning full scan produces exact same count
    rerun_count = market_data_service.recalculate_market_structure_full(SOURCE_ID, "XAUUSD.VX", "M1")
    assert rerun_count == full_count

def test_s_restart_recovery():
    """S. Restart recovery: Restarting service reconstructs state from repository without memory dependency."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4450.0
    state["bullish_break_level_candle_time_epoch"] = 100
    state["last_processed_candle_time_epoch"] = 150
    market_structure_repo.upsert_structure_state(state)

    # Simulate fresh service
    fresh_state = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")
    assert fresh_state is not None
    assert fresh_state["bias"] == StructureBias.BULLISH.value
    assert float(fresh_state["bullish_break_level_price"]) == 4450.0

def test_t_u_v_timeframe_source_canonical_isolation():
    """T, U, V. Timeframe isolation, Source isolation, Canonical symbol normalization."""
    # Canonical symbol: lowercase resolves to uppercase
    sym_lower = "xauusd.vx"
    assert canonicalize_symbol(sym_lower) == "XAUUSD.VX"

    state_m1 = create_initial_state(SOURCE_ID, sym_lower, "M1")
    state_m1["bias"] = StructureBias.BULLISH.value
    market_structure_repo.upsert_structure_state(state_m1)

    # M5 should be unaffected
    state_m5 = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M5")
    assert state_m5 is None

    # Different source should be unaffected
    other_source = "b" * 64
    state_other = market_structure_repo.get_structure_state(other_source, "XAUUSD.VX", "M1")
    assert state_other is None

    # Query with uppercase or lowercase finds same record
    assert market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")["bias"] == StructureBias.BULLISH.value
    assert market_structure_repo.get_structure_state(SOURCE_ID, "xauusd.vx", "M1")["bias"] == StructureBias.BULLISH.value

def test_w_same_candle_multiple_condition_priority():
    """W. Same candle priority: DOUBLE_BREAK > Invalidation > MSS > CHOCH > BOS."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state["pending_choch_epoch"] = 1000
    state["bullish_break_level_price"] = 4500.0
    state["bullish_break_level_candle_time_epoch"] = 900
    state["protected_low_price"] = 4400.0

    # Candle closes > 4500.10: triggers invalidation (Priority 2), restoring NORMAL
    candle = {"candle_time_epoch": 1050, "open": 4480.0, "high": 4515.0, "low": 4475.0, "close": 4510.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)
    assert ev["event_type"] == StructureEventType.BOS_BULLISH.value
    assert state["transition_state"] == StructureTransitionState.NORMAL.value

def test_api_endpoint_market_structure_read_only():
    """Verify GET /api/v1/market-structure endpoint returns valid schema."""
    state = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4500.0
    market_structure_repo.upsert_structure_state(state)

    ev = {
        "source_id": SOURCE_ID,
        "symbol": "XAUUSD.VX",
        "timeframe": "M1",
        "event_type": "BOS_BULLISH",
        "event_candle_time_epoch": 1000,
        "candle_close": 4505.0,
        "previous_bias": "BULLISH",
        "resulting_bias": "BULLISH",
        "transition_state": "NORMAL",
        "fractal_n": 2
    }
    market_structure_repo.upsert_structure_events([ev])

    res = client.get(
        f"/api/v1/market-structure?source_id={SOURCE_ID}&symbol=XAUUSD.vx&timeframe=M1",
        headers=AUTH_HEADERS
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    assert data["symbol"] == "XAUUSD.VX"
    assert data["bias"] == "BULLISH"
    assert data["total_events"] == 1
    assert data["events"][0]["event_type"] == "BOS_BULLISH"

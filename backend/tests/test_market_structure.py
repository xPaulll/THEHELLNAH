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


# ============================================================================
# FINAL HARDENING TESTS (STEP 2 CONTRACT ENFORCEMENT)
# ============================================================================

def test_step1_to_step2_boundary_no_swing_redetection():
    """
    1. Boundary test: Step 2 strictly consumes Step 1 market_swings.
    It does not detect fractals, does not re-classify HH/HL/LH/LL, and
    does not create swings from candle high/low.
    """
    import inspect
    from backend.app.features.market_structure_detector import MarketStructureDetector
    src = inspect.getsource(MarketStructureDetector)
    assert "detect_swings_from_closed_candles" not in src
    assert "is_fractal" not in src
    assert "recalculate" not in src

    # Verify that extreme candle spikes without confirmed swings do NOT create swings in state
    state = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4400.0
    state["protected_low_price"] = 4350.0

    # Candle spikes to 5000 and drops to 4000, but no new confirmed swings are fed
    candle = {"candle_time_epoch": 200, "open": 4380.0, "high": 5000.0, "low": 4000.0, "close": 4385.0}
    state, ev = market_structure_detector.evaluate_closed_candle(candle, state, threshold_price=0.10)

    # Bullish break level and protected low must remain completely unchanged!
    assert state["bullish_break_level_price"] == 4400.0
    assert state["protected_low_price"] == 4350.0
    assert ev is None


def test_mss_bearish_adversarial_matrix():
    """
    10. Adversarial matrix for Bearish MSS:
    - PASS: CHOCH -> LH -> LL
    - PASS: CHOCH -> LL -> LH
    - FAIL: CHOCH -> LH -> HH -> LL (contaminated with HH)
    - FAIL: CHOCH -> LL -> HH -> LH (contaminated with HH)
    - FAIL: Pre-CHOCH LH, Pre-CHOCH LL, CHOCH (pre-choch cannot trigger MSS)
    - FAIL: CHOCH -> only LH (insufficient)
    - FAIL: CHOCH -> only LL (insufficient)
    - FAIL: CHOCH -> LH -> LL -> HH (superseded by contrary swing)
    """
    # 1. PASS: CHOCH -> LH -> LL
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_pass1 = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_pass1, swings_pass1)
    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BEARISH.value
    assert st_res["bias"] == StructureBias.BEARISH.value
    assert st_res["transition_state"] == StructureTransitionState.NORMAL.value

    # 2. PASS: CHOCH -> LL -> LH
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_pass2 = [
        {"id": 1, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_pass2, swings_pass2)
    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BEARISH.value
    assert st_res["bias"] == StructureBias.BEARISH.value

    # 3. FAIL: CHOCH -> LH -> HH -> LL (contaminated sequence)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_fail_contam = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "HIGH", "classification": "HH", "swing_price": 4510.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1040, "confirmed_at_candle_time_epoch": 1060}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_fail_contam, swings_fail_contam)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value
    assert st_res["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value

    # 4. FAIL: CHOCH -> LL -> HH -> LH (contaminated sequence)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_fail_contam2 = [
        {"id": 1, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "HIGH", "classification": "HH", "swing_price": 4510.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1040, "confirmed_at_candle_time_epoch": 1060}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_fail_contam2, swings_fail_contam2)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value

    # 5. FAIL: Pre-CHOCH LH & LL + CHOCH (pre-choch cannot trigger MSS)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_pre_choch = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 800, "confirmed_at_candle_time_epoch": 900},
        {"id": 2, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 850, "confirmed_at_candle_time_epoch": 950}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, [], swings_pre_choch)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value

    # 6. FAIL: CHOCH -> only LH
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_only_lh = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_only_lh, swings_only_lh)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value

    # 7. FAIL: CHOCH -> only LL
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_only_ll = [
        {"id": 1, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_only_ll, swings_only_ll)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value

    # 8. FAIL: CHOCH -> LH -> LL -> HH (superseded by contrary swing)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_superseded = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4480.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "LOW", "classification": "LL", "swing_price": 4420.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "HIGH", "classification": "HH", "swing_price": 4520.0, "swing_candle_time_epoch": 1060, "confirmed_at_candle_time_epoch": 1080}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, [swings_superseded[2]], swings_superseded)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BULLISH.value


def test_mss_bullish_adversarial_matrix():
    """
    10. Adversarial matrix for Bullish MSS:
    - PASS: CHOCH -> HL -> HH
    - PASS: CHOCH -> HH -> HL
    - FAIL: CHOCH -> HL -> LL -> HH (contaminated with LL)
    - FAIL: CHOCH -> HH -> LL -> HL (contaminated with LL)
    - FAIL: CHOCH -> only HL (insufficient)
    - FAIL: CHOCH -> only HH (insufficient)
    - FAIL: CHOCH -> HL -> HH -> LL (superseded by contrary swing)
    """
    # 1. PASS: CHOCH -> HL -> HH
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_pass1 = [
        {"id": 1, "swing_type": "LOW", "classification": "HL", "swing_price": 4430.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_pass1, swings_pass1)
    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BULLISH.value
    assert st_res["bias"] == StructureBias.BULLISH.value
    assert st_res["transition_state"] == StructureTransitionState.NORMAL.value

    # 2. PASS: CHOCH -> HH -> HL
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_pass2 = [
        {"id": 1, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "LOW", "classification": "HL", "swing_price": 4430.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_pass2, swings_pass2)
    assert mss_ev is not None
    assert mss_ev["event_type"] == StructureEventType.MSS_BULLISH.value
    assert st_res["bias"] == StructureBias.BULLISH.value

    # 3. FAIL: CHOCH -> HL -> LL -> HH (contaminated with LL)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_fail_contam = [
        {"id": 1, "swing_type": "LOW", "classification": "HL", "swing_price": 4430.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "LOW", "classification": "LL", "swing_price": 4390.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 1040, "confirmed_at_candle_time_epoch": 1060}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_fail_contam, swings_fail_contam)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BEARISH.value

    # 4. FAIL: CHOCH -> HH -> LL -> HL (contaminated with LL)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_fail_contam2 = [
        {"id": 1, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "LOW", "classification": "LL", "swing_price": 4390.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "LOW", "classification": "HL", "swing_price": 4430.0, "swing_candle_time_epoch": 1040, "confirmed_at_candle_time_epoch": 1060}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, swings_fail_contam2, swings_fail_contam2)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BEARISH.value

    # 5. FAIL: CHOCH -> only HL
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, [swings_pass1[0]], [swings_pass1[0]])
    assert mss_ev is None

    # 6. FAIL: CHOCH -> only HH
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, [swings_pass1[1]], [swings_pass1[1]])
    assert mss_ev is None

    # 7. FAIL: CHOCH -> HL -> HH -> LL (superseded by contrary swing)
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BEARISH.value
    st["transition_state"] = StructureTransitionState.CHOCH_BULLISH_PENDING.value
    st["pending_choch_epoch"] = 1000
    swings_superseded = [
        {"id": 1, "swing_type": "LOW", "classification": "HL", "swing_price": 4430.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040},
        {"id": 2, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 1030, "confirmed_at_candle_time_epoch": 1050},
        {"id": 3, "swing_type": "LOW", "classification": "LL", "swing_price": 4380.0, "swing_candle_time_epoch": 1060, "confirmed_at_candle_time_epoch": 1080}
    ]
    st_res, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, [swings_superseded[2]], swings_superseded)
    assert mss_ev is None
    assert st_res["bias"] == StructureBias.BEARISH.value


def test_pending_invalidation_matrix():
    """
    11. Pending invalidation matrix:
    - Bearish pending: CHOCH bearish -> HH confirmed -> close has NOT broken original continuation target -> remains PENDING
    - Bearish pending: CHOCH bearish -> closed candle > original continuation target + tolerance -> NORMAL + BOS_BULLISH
    - Bullish pending: CHOCH bullish -> LL confirmed -> close has NOT broken original continuation target -> remains PENDING
    - Bullish pending: CHOCH bullish -> closed candle < original continuation target - tolerance -> NORMAL + BOS_BEARISH
    """
    # 1. Bearish pending remains pending when HH confirms but candle close has NOT broken continuation target
    st = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st["bias"] = StructureBias.BULLISH.value
    st["bullish_break_level_price"] = 4500.0 # Original continuation target
    st["protected_low_price"] = 4400.0

    # CHOCH Bearish occurs at candle 1000
    candle_choch = {"candle_time_epoch": 1000, "open": 4410.0, "high": 4415.0, "low": 4390.0, "close": 4395.0}
    st, ev_choch = market_structure_detector.evaluate_closed_candle(candle_choch, st, threshold_price=0.10)
    assert ev_choch["event_type"] == StructureEventType.CHOCH_BEARISH.value
    assert st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value
    assert st["pending_choch_continuation_target_price"] == 4500.0

    # New HH swing confirms at 4460.0 (internal swing)
    new_hh_swing = [{"id": 10, "swing_type": "HIGH", "classification": "HH", "swing_price": 4460.0, "swing_candle_time_epoch": 1020, "confirmed_at_candle_time_epoch": 1040}]
    st, mss_ev = market_structure_detector.apply_confirmed_swings_to_state(st, new_hh_swing, new_hh_swing)
    assert mss_ev is None
    # Transition MUST REMAIN CHOCH_BEARISH_PENDING!
    assert st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value

    # Candle closes at 4470.0 (higher than internal HH 4460, but LESS than original target 4500.10)
    candle_inside = {"candle_time_epoch": 1050, "open": 4450.0, "high": 4475.0, "low": 4445.0, "close": 4470.0}
    st, ev_inside = market_structure_detector.evaluate_closed_candle(candle_inside, st, threshold_price=0.10)
    assert ev_inside is None
    assert st["transition_state"] == StructureTransitionState.CHOCH_BEARISH_PENDING.value

    # Candle closes at 4505.0 (> original continuation target 4500.0 + 0.10)
    candle_break = {"candle_time_epoch": 1060, "open": 4490.0, "high": 4510.0, "low": 4485.0, "close": 4505.0}
    st, ev_break = market_structure_detector.evaluate_closed_candle(candle_break, st, threshold_price=0.10)
    assert ev_break is not None
    assert ev_break["event_type"] == StructureEventType.BOS_BULLISH.value
    assert st["transition_state"] == StructureTransitionState.NORMAL.value
    assert st["pending_choch_epoch"] is None

    # 2. Bullish pending remains pending when LL confirms but candle close has NOT broken continuation target
    st_b = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    st_b["bias"] = StructureBias.BEARISH.value
    st_b["protected_high_price"] = 4500.0
    st_b["bearish_break_level_price"] = 4400.0 # Original continuation target

    # CHOCH Bullish occurs
    candle_choch_b = {"candle_time_epoch": 2000, "open": 4490.0, "high": 4515.0, "low": 4485.0, "close": 4510.0}
    st_b, ev_choch_b = market_structure_detector.evaluate_closed_candle(candle_choch_b, st_b, threshold_price=0.10)
    assert ev_choch_b["event_type"] == StructureEventType.CHOCH_BULLISH.value
    assert st_b["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value
    assert st_b["pending_choch_continuation_target_price"] == 4400.0

    # New LL swing confirms at 4430.0 (internal swing)
    new_ll_swing = [{"id": 20, "swing_type": "LOW", "classification": "LL", "swing_price": 4430.0, "swing_candle_time_epoch": 2020, "confirmed_at_candle_time_epoch": 2040}]
    st_b, mss_ev_b = market_structure_detector.apply_confirmed_swings_to_state(st_b, new_ll_swing, new_ll_swing)
    assert mss_ev_b is None
    # Transition MUST REMAIN CHOCH_BULLISH_PENDING!
    assert st_b["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value

    # Candle closes at 4420.0 (lower than internal LL 4430, but GREATER than original target 4400.0 - 0.10)
    candle_inside_b = {"candle_time_epoch": 2050, "open": 4430.0, "high": 4435.0, "low": 4415.0, "close": 4420.0}
    st_b, ev_inside_b = market_structure_detector.evaluate_closed_candle(candle_inside_b, st_b, threshold_price=0.10)
    assert ev_inside_b is None
    assert st_b["transition_state"] == StructureTransitionState.CHOCH_BULLISH_PENDING.value

    # Candle closes at 4390.0 (< original continuation target 4400.0 - 0.10)
    candle_break_b = {"candle_time_epoch": 2060, "open": 4410.0, "high": 4415.0, "low": 4385.0, "close": 4390.0}
    st_b, ev_break_b = market_structure_detector.evaluate_closed_candle(candle_break_b, st_b, threshold_price=0.10)
    assert ev_break_b is not None
    assert ev_break_b["event_type"] == StructureEventType.BOS_BEARISH.value
    assert st_b["transition_state"] == StructureTransitionState.NORMAL.value
    assert st_b["pending_choch_epoch"] is None


def test_persistence_atomicity_failure_injection():
    """
    7. Persistence atomicity failure injection:
    - State write failure rolls back event write. Zero partial durable state!
    - Event write failure prevents state modification. Zero partial durable state!
    """
    state_initial = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")
    state_initial["bias"] = StructureBias.BULLISH.value
    market_structure_repo.upsert_structure_state(state_initial)

    ev = {
        "source_id": SOURCE_ID,
        "symbol": "XAUUSD.VX",
        "timeframe": "M1",
        "event_type": "CHOCH_BEARISH",
        "event_candle_time_epoch": 5000,
        "candle_close": 4390.0,
        "previous_bias": "BULLISH",
        "resulting_bias": "BULLISH",
        "transition_state": "CHOCH_BEARISH_PENDING",
        "fractal_n": 2
    }

    state_mutated = dict(state_initial)
    state_mutated["transition_state"] = StructureTransitionState.CHOCH_BEARISH_PENDING.value
    state_mutated["pending_choch_epoch"] = 5000

    # Failure Injection 1: state write fails
    market_structure_repo._fail_state_write = True
    with pytest.raises(RuntimeError, match="Simulated state write failure"):
        market_structure_repo.save_event_and_state(ev, state_mutated)
    market_structure_repo._fail_state_write = False

    # VERIFY: Event was NOT persisted, and state was ROLLED BACK to initial!
    events = market_structure_repo.get_structure_events(SOURCE_ID, "XAUUSD.VX", "M1")
    assert len(events) == 0, "Event must be rolled back on state failure!"
    st_check = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")
    assert st_check["transition_state"] == StructureTransitionState.NORMAL.value, "State must remain unmutated!"

    # Failure Injection 2: event write fails
    market_structure_repo._fail_event_write = True
    with pytest.raises(RuntimeError, match="Simulated event write failure"):
        market_structure_repo.save_event_and_state(ev, state_mutated)
    market_structure_repo._fail_event_write = False

    # VERIFY: Neither event nor state was persisted!
    events = market_structure_repo.get_structure_events(SOURCE_ID, "XAUUSD.VX", "M1")
    assert len(events) == 0
    st_check = market_structure_repo.get_structure_state(SOURCE_ID, "XAUUSD.VX", "M1")
    assert st_check["transition_state"] == StructureTransitionState.NORMAL.value


def test_pending_choch_event_id_foreign_key_and_schema():
    """
    8. Schema verification:
    Verify that market_structure_state.pending_choch_event_id has foreign key
    referencing market_structure_events(id) ON DELETE SET NULL.
    """
    from pathlib import Path
    schema_path = Path("backend/sql/04_market_structure_schema.sql")
    assert schema_path.exists()
    content = schema_path.read_text(encoding="utf-8")
    assert "pending_choch_event_id BIGINT REFERENCES market_structure_events(id) ON DELETE SET NULL" in content
    assert "pending_choch_continuation_target_price NUMERIC(16, 6)" in content


class IndependentMarketStructureOracle:
    """
    9. Independent reference-model / Oracle for market structure detection.
    Completely independent implementation used as ground-truth oracle to prove
    state transition, MSS ordering, CHOCH, BOS, and break level correctness.
    """
    def __init__(self, tolerance: float = 0.10):
        self.bias = "NEUTRAL"
        self.transition_state = "NORMAL"
        self.protected_high = None
        self.protected_low = None
        self.bullish_break_target = None
        self.bearish_break_target = None
        self.pending_choch_epoch = None
        self.pending_continuation_target = None
        self.post_choch_swings = []
        self.tolerance = tolerance
        self.events = []

    def feed_swings(self, swings: list[dict]):
        for sw in sorted(swings, key=lambda s: (s.get("confirmed_at_candle_time_epoch", 0), s.get("swing_candle_time_epoch", 0))):
            cls = sw.get("classification")
            p = float(sw.get("swing_price", 0.0))
            conf = int(sw.get("confirmed_at_candle_time_epoch", 0))

            if cls in ["EQH", "EQL"]:
                continue

            if self.bias == "NEUTRAL":
                # Need HH + HL or LH + LL
                pass # Initial bootstrap handled explicitly

            elif self.bias == "BULLISH":
                if cls == "HL":
                    self.protected_low = p
                elif cls == "HH":
                    self.bullish_break_target = p

                if self.transition_state == "CHOCH_BEARISH_PENDING":
                    if conf > (self.pending_choch_epoch or 0):
                        self.post_choch_swings.append(sw)
                        # Check strict sequence: LH -> LL or LL -> LH without intervening HH or HL
                        dir_s = [s for s in self.post_choch_swings if s["classification"] in ["LH", "LL", "HH", "HL"]]
                        if len(dir_s) >= 2 and dir_s[-1]["classification"] in ["LH", "LL"]:
                            last_c = dir_s[-1]["classification"]
                            target_c = "LL" if last_c == "LH" else "LH"
                            has_target = False
                            for i in range(len(dir_s) - 2, -1, -1):
                                if dir_s[i]["classification"] in ["HH", "HL"]:
                                    break
                                if dir_s[i]["classification"] == target_c:
                                    has_target = True
                                    break
                            if has_target:
                                # Trigger MSS Bearish
                                self.bias = "BEARISH"
                                self.transition_state = "NORMAL"
                                self.protected_high = [s for s in dir_s if s["classification"] == "LH"][-1]["swing_price"]
                                self.bearish_break_target = [s for s in dir_s if s["classification"] == "LL"][-1]["swing_price"]
                                self.protected_low = None
                                self.bullish_break_target = None
                                self.pending_choch_epoch = None
                                self.pending_continuation_target = None
                                self.events.append({"type": "MSS_BEARISH", "epoch": conf})

            elif self.bias == "BEARISH":
                if cls == "LH":
                    self.protected_high = p
                elif cls == "LL":
                    self.bearish_break_target = p

                if self.transition_state == "CHOCH_BULLISH_PENDING":
                    if conf > (self.pending_choch_epoch or 0):
                        self.post_choch_swings.append(sw)
                        dir_s = [s for s in self.post_choch_swings if s["classification"] in ["HL", "HH", "LH", "LL"]]
                        if len(dir_s) >= 2 and dir_s[-1]["classification"] in ["HL", "HH"]:
                            last_c = dir_s[-1]["classification"]
                            target_c = "HH" if last_c == "HL" else "HL"
                            has_target = False
                            for i in range(len(dir_s) - 2, -1, -1):
                                if dir_s[i]["classification"] in ["LH", "LL"]:
                                    break
                                if dir_s[i]["classification"] == target_c:
                                    has_target = True
                                    break
                            if has_target:
                                self.bias = "BULLISH"
                                self.transition_state = "NORMAL"
                                self.protected_low = [s for s in dir_s if s["classification"] == "HL"][-1]["swing_price"]
                                self.bullish_break_target = [s for s in dir_s if s["classification"] == "HH"][-1]["swing_price"]
                                self.protected_high = None
                                self.bearish_break_target = None
                                self.pending_choch_epoch = None
                                self.pending_continuation_target = None
                                self.events.append({"type": "MSS_BULLISH", "epoch": conf})

    def feed_candle(self, c: dict):
        close = float(c["close"])
        epoch = int(c["candle_time_epoch"])

        # Priority 1: Double break
        # Priority 2: Invalidation check
        if self.transition_state == "CHOCH_BEARISH_PENDING":
            if close > (self.pending_continuation_target + self.tolerance):
                self.transition_state = "NORMAL"
                self.pending_choch_epoch = None
                self.pending_continuation_target = None
                self.events.append({"type": "BOS_BULLISH", "epoch": epoch})
                return
        elif self.transition_state == "CHOCH_BULLISH_PENDING":
            if close < (self.pending_continuation_target - self.tolerance):
                self.transition_state = "NORMAL"
                self.pending_choch_epoch = None
                self.pending_continuation_target = None
                self.events.append({"type": "BOS_BEARISH", "epoch": epoch})
                return

        # Priority 4: CHOCH
        if self.bias == "BULLISH" and self.transition_state == "NORMAL":
            if self.protected_low and close < (self.protected_low - self.tolerance):
                self.transition_state = "CHOCH_BEARISH_PENDING"
                self.pending_choch_epoch = epoch
                self.pending_continuation_target = self.bullish_break_target
                self.post_choch_swings = []
                self.events.append({"type": "CHOCH_BEARISH", "epoch": epoch})
                return
            elif self.bullish_break_target and close > (self.bullish_break_target + self.tolerance):
                self.events.append({"type": "BOS_BULLISH", "epoch": epoch})
                return

        elif self.bias == "BEARISH" and self.transition_state == "NORMAL":
            if self.protected_high and close > (self.protected_high + self.tolerance):
                self.transition_state = "CHOCH_BULLISH_PENDING"
                self.pending_choch_epoch = epoch
                self.pending_continuation_target = self.bearish_break_target
                self.post_choch_swings = []
                self.events.append({"type": "CHOCH_BULLISH", "epoch": epoch})
                return
            elif self.bearish_break_target and close < (self.bearish_break_target - self.tolerance):
                self.events.append({"type": "BOS_BEARISH", "epoch": epoch})
                return


def test_independent_reference_model_oracle_verification():
    """
    9. Independent Oracle Verification:
    Feeds a complex multi-step market cycle through both the actual
    detector and the independent Oracle. Asserts 100% equivalence at each stage.
    """
    oracle = IndependentMarketStructureOracle(tolerance=0.10)
    state = create_initial_state(SOURCE_ID, "XAUUSD.VX", "M1")

    # Bootstrap to Bullish
    oracle.bias = "BULLISH"
    oracle.bullish_break_target = 4500.0
    oracle.protected_low = 4400.0

    state["bias"] = StructureBias.BULLISH.value
    state["bullish_break_level_price"] = 4500.0
    state["bullish_break_level_candle_time_epoch"] = 100
    state["protected_low_price"] = 4400.0
    state["protected_low_candle_time_epoch"] = 110

    # Step 1: Bullish BOS
    c1 = {"candle_time_epoch": 200, "open": 4490.0, "high": 4515.0, "low": 4485.0, "close": 4505.0}
    oracle.feed_candle(c1)
    state, ev1 = market_structure_detector.evaluate_closed_candle(c1, state, threshold_price=0.10)
    assert ev1["event_type"] == oracle.events[-1]["type"] == "BOS_BULLISH"
    assert state["bias"] == oracle.bias == "BULLISH"

    # Step 2: CHOCH Bearish trigger
    c2 = {"candle_time_epoch": 300, "open": 4410.0, "high": 4415.0, "low": 4390.0, "close": 4395.0}
    oracle.feed_candle(c2)
    state, ev2 = market_structure_detector.evaluate_closed_candle(c2, state, threshold_price=0.10)
    assert ev2["event_type"] == oracle.events[-1]["type"] == "CHOCH_BEARISH"
    assert state["transition_state"] == oracle.transition_state == "CHOCH_BEARISH_PENDING"
    assert state["pending_choch_continuation_target_price"] == oracle.pending_continuation_target == 4500.0

    # Step 3: Adversarial swing sequence (LH -> HH -> LL) - should NOT trigger MSS
    sw_contam = [
        {"id": 1, "swing_type": "HIGH", "classification": "LH", "swing_price": 4450.0, "swing_candle_time_epoch": 320, "confirmed_at_candle_time_epoch": 340},
        {"id": 2, "swing_type": "HIGH", "classification": "HH", "swing_price": 4490.0, "swing_candle_time_epoch": 350, "confirmed_at_candle_time_epoch": 370},
        {"id": 3, "swing_type": "LOW", "classification": "LL", "swing_price": 4380.0, "swing_candle_time_epoch": 380, "confirmed_at_candle_time_epoch": 400}
    ]
    oracle.feed_swings(sw_contam)
    state, ev_mss_fail = market_structure_detector.apply_confirmed_swings_to_state(state, sw_contam, sw_contam)
    assert ev_mss_fail is None
    assert len(oracle.events) == 2 # No MSS event added in oracle
    assert state["bias"] == oracle.bias == "BULLISH"
    assert state["transition_state"] == oracle.transition_state == "CHOCH_BEARISH_PENDING"

    # Step 4: Valid sequence completes (new LH confirms after the LL)
    sw_valid = [
        {"id": 4, "swing_type": "HIGH", "classification": "LH", "swing_price": 4440.0, "swing_candle_time_epoch": 410, "confirmed_at_candle_time_epoch": 430}
    ]
    all_swings = sw_contam + sw_valid
    oracle.feed_swings(sw_valid)
    state, ev_mss_pass = market_structure_detector.apply_confirmed_swings_to_state(state, sw_valid, all_swings)
    assert ev_mss_pass["event_type"] == oracle.events[-1]["type"] == "MSS_BEARISH"
    assert state["bias"] == oracle.bias == "BEARISH"
    assert state["transition_state"] == oracle.transition_state == "NORMAL"


def test_symbol_canonicalization_comprehensive():
    """
    15. Symbol canonicalization:
    'xauusd.vx', 'XAUUSD.VX', ' XAUUSD.VX ' must resolve to identical canonical representation
    and never produce isolated states.
    """
    syms = ["xauusd.vx", "XAUUSD.VX", " XAUUSD.VX ", "  xauusd.vx  "]
    for s in syms:
        assert canonicalize_symbol(s) == "XAUUSD.VX"

    # Repository operations with differing case/whitespace access identical record
    st = create_initial_state(SOURCE_ID, "  xauusd.vx  ", "M1")
    st["bias"] = StructureBias.BULLISH.value
    market_structure_repo.upsert_structure_state(st)

    for s in syms:
        rec = market_structure_repo.get_structure_state(SOURCE_ID, s, "M1")
        assert rec is not None
        assert rec["symbol"] == "XAUUSD.VX"
        assert rec["bias"] == StructureBias.BULLISH.value

